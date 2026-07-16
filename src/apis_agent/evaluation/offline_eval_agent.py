"""
Agent 离线评估 — 评估 Agent 工具使用和任务完成质量。

用法:
    python -m src.apis_agent.evaluation.offline_eval_agent [--dataset path/to/agent_datasets.json]

依赖:
    pip install deepeval

指标:
    GoalAccuracy — 任务目标达成度
    ToolUse — 工具选择和使用正确性
    ConversationCompleteness — 多轮对话完成度
"""
import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("agent_eval")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_DEFAULT = Path(__file__).resolve().parent / "datasets" / "agent_datasets.json"


def _build_eval_model():
    from deepeval.models import DeepEvalBaseLLM

    class ApisEvalModel(DeepEvalBaseLLM):
        def __init__(self):
            from src.apis_agent.common.llm import _create_raw_llm
            self._llm = _create_raw_llm()

        def load_model(self):
            return self._llm

        def generate(self, prompt: str) -> str:
            result = self._llm.invoke(prompt)
            return result.content if hasattr(result, "content") else str(result)

        async def a_generate(self, prompt: str) -> str:
            result = await self._llm.ainvoke(prompt)
            return result.content if hasattr(result, "content") else str(result)

        def get_model_name(self) -> str:
            from src.apis_agent.config.settings import get_settings
            return get_settings().llm_model

    return ApisEvalModel()


def load_dataset(file_path: str) -> list:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info(f"加载 {len(data['goldens'])} 条 Agent 测试用例")
    return data["goldens"]


def build_metrics():
    from deepeval.metrics import (
        GoalAccuracyMetric, ToolUseMetric, ConversationCompletenessMetric,
    )
    from deepeval.test_case import ToolCall
    from src.apis_agent.tool import TOOL_REGISTRY

    model = _build_eval_model()
    available_tools = [
        ToolCall(name=name, description=getattr(t, "description", name))
        for name, t in TOOL_REGISTRY.items()
    ]
    return [
        GoalAccuracyMetric(threshold=0.6, include_reason=True, model=model),
        ToolUseMetric(threshold=0.6, include_reason=True, available_tools=available_tools, model=model),
        ConversationCompletenessMetric(threshold=0.6, include_reason=True, model=model),
    ]


async def run_case(golden: dict, metrics: list) -> dict:
    """执行单条 Agent 评估用例。"""
    from deepeval.test_case import ConversationalTestCase, Turn
    from src.apis_agent.agent.triage_agent import TriageAgent

    turns = golden.get("turns", [])
    test_turns = []

    for turn in turns:
        if turn["role"] != "user":
            continue

        session_id = f"eval_{uuid.uuid4().hex[:8]}"
        agent = TriageAgent(session_id, turn["content"])
        output_text = ""
        tool_names = []

        try:
            async for event in agent.run():
                if isinstance(event, dict):
                    t = event.get("type", "")
                    if t == "text":
                        output_text += event.get("content", "")
                    elif t == "tool_start":
                        tool_names.append(event.get("toolName", ""))
        except Exception as e:
            logger.warning(f"Agent 执行异常: {e}")
            output_text = str(e)

        from deepeval.test_case import ToolCall
        tool_calls = [ToolCall(name=n, description=f"Call {n}", input_parameters={})
                      for n in tool_names]

        test_turns.append(Turn(role="user", content=turn["content"]))
        test_turns.append(Turn(
            role="assistant", content=output_text[:2000],
            tools_called=tool_calls if tool_calls else None,
        ))

    test_case = ConversationalTestCase(
        turns=test_turns,
        expected_outcome=golden.get("expected_outcome", ""),
    )

    results = {}
    for metric in metrics:
        name = metric.__class__.__name__
        try:
            if hasattr(metric, "a_measure"):
                await metric.a_measure(test_case)
            else:
                metric.measure(test_case)
            results[name] = {"score": metric.score, "reason": metric.reason}
        except Exception as e:
            logger.warning(f"指标 {name} 失败: {e}")
            results[name] = {"score": 0.0, "reason": str(e)}

    return results


async def main():
    parser = argparse.ArgumentParser(description="Agent 离线评估")
    parser.add_argument("--dataset", type=str, default=str(DATASET_DEFAULT))
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        logger.error(f"数据集不存在: {dataset_path}")
        return

    goldens = load_dataset(str(dataset_path))
    metrics = build_metrics()
    logger.info(f"Agent 指标: {[m.__class__.__name__ for m in metrics]}")

    aggregation: dict[str, list] = {m.__class__.__name__: [] for m in metrics}
    all_results = {}

    for idx, golden in enumerate(goldens, 1):
        scenario = golden.get("scenario", f"case_{idx}")
        results = await run_case(golden, metrics)
        all_results[scenario] = results
        for name, data in results.items():
            aggregation[name].append(data["score"])
        scores = ", ".join(f"{k}={v['score']:.2f}" for k, v in results.items())
        logger.info(f"[{idx}/{len(goldens)}] {scenario}: {scores}")

    logger.info("\n" + "=" * 60)
    logger.info("Agent 评估汇总")
    logger.info("=" * 60)
    for name, scores_vals in aggregation.items():
        if scores_vals:
            avg = sum(scores_vals) / len(scores_vals)
            logger.info(f"  {name}: avg={avg:.3f} (n={len(scores_vals)})")

    output_path = Path(__file__).resolve().parent / "agent_eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
