"""
RAG 离线评估 — 评估检索与生成质量。

用法:
    python -m src.apis_agent.evaluation.offline_eval_rag [--dataset path/to/rag_datasets.json]

依赖:
    pip install deepeval

指标:
    Faithfulness — 回答是否忠实于检索上下文
    AnswerRelevancy — 回答是否与问题相关
    ContextualRelevancy — 检索上下文是否与问题相关
    ContextualRecall — 检索是否覆盖了预期上下文
    ContextualPrecision — 检索结果中相关文档的排名
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("rag_eval")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_DEFAULT = Path(__file__).resolve().parent / "datasets" / "rag_datasets.json"


def _build_eval_model():
    """用项目 LLM 构建 deepEval 评估模型。"""
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
    logger.info(f"加载 {len(data['goldens'])} 条 RAG 测试用例")
    return data["goldens"]


def build_metrics():
    from deepeval.metrics import (
        FaithfulnessMetric, AnswerRelevancyMetric,
        ContextualRelevancyMetric, ContextualRecallMetric, ContextualPrecisionMetric,
    )
    model = _build_eval_model()
    return [
        FaithfulnessMetric(threshold=0.6, include_reason=True, model=model),
        AnswerRelevancyMetric(threshold=0.6, include_reason=True, model=model),
        ContextualRelevancyMetric(threshold=0.6, include_reason=True, model=model),
        ContextualRecallMetric(threshold=0.6, include_reason=True, model=model),
        ContextualPrecisionMetric(threshold=0.6, include_reason=True, model=model),
    ]


async def run_case(golden: dict, metrics: list) -> dict:
    """执行单条 RAG 评估用例。"""
    from deepeval.test_case import LLMTestCase
    from src.apis_agent.service.rag_service import build_context

    query = golden["input"]
    expected = golden.get("expected_output", "")

    # 获取实际 RAG 输出
    try:
        actual = await build_context(query, file_id="eval", full_text="")
    except Exception:
        actual = ""

    retrieval_context = golden.get("retrieval_context", [])
    if not retrieval_context:
        # 无预定义检索上下文时，用实际检索结果
        retrieval_context = [actual] if actual else ["(空)"]

    test_case = LLMTestCase(
        input=query,
        actual_output=actual[:2000],
        expected_output=expected,
        retrieval_context=retrieval_context,
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
    parser = argparse.ArgumentParser(description="RAG 离线评估")
    parser.add_argument("--dataset", type=str, default=str(DATASET_DEFAULT))
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        logger.error(f"数据集不存在: {dataset_path}")
        return

    goldens = load_dataset(str(dataset_path))
    metrics = build_metrics()
    logger.info(f"RAG 指标: {[m.__class__.__name__ for m in metrics]}")

    aggregation: dict[str, list] = {m.__class__.__name__: [] for m in metrics}
    all_results = {}

    for idx, golden in enumerate(goldens, 1):
        results = await run_case(golden, metrics)
        all_results[golden["input"][:60]] = results
        for name, data in results.items():
            aggregation[name].append(data["score"])
        scores = ", ".join(f"{k}={v['score']:.2f}" for k, v in results.items())
        logger.info(f"[{idx}/{len(goldens)}] {golden['input'][:50]}: {scores}")

    logger.info("\n" + "=" * 60)
    logger.info("RAG 评估汇总")
    logger.info("=" * 60)
    for name, scores in aggregation.items():
        if scores:
            avg = sum(scores) / len(scores)
            logger.info(f"  {name}: avg={avg:.3f} (n={len(scores)})")

    output_path = Path(__file__).resolve().parent / "rag_eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
