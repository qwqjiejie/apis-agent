"""TriageAgent — 统一入口分流器。

基于 create_react_agent，持有全部工具。
LLM 根据分流规则自主判断：
- 简单问题 → 直接处理或委托一次 Specialist
- 复杂任务 → 调用 create_background_task 创建后台任务
"""

import asyncio
import json
import logging

from src.apis_agent.agent.base_agent import BaseAgent
from src.apis_agent.common.streaming import AgentStopped, make_event, make_sse
from src.apis_agent.harness.subagent_discovery import discover_specialists
from src.apis_agent.harness.task_executor import task_executor
from src.apis_agent.prompt.triage_prompt import build_triage_prompt, parse_capability_prefix
from src.apis_agent.tool import TOOL_REGISTRY

logger = logging.getLogger("apis")


class TriageAgent(BaseAgent):
    """统一入口分流 Agent。

    使用与 ExecutorAgent 相同的 Agent 构造方式，
    差异仅在于 system_prompt（引导分流）和 tools（持有全部工具）。
    """

    def __init__(self, conversation_id: str, query: str, file_id: str = ""):
        super().__init__(conversation_id, query, file_id)
        self._delegated_task_id: str | None = None

    async def run(self):
        from src.apis_agent.agent.agent_factory import create_triage_agent
        from src.apis_agent.gateway.model_gateway import model_gateway

        ok, error_events = await self._try_start()
        if not ok:
            for evt in error_events:
                yield evt
            return

        # 解析能力前缀，如果有则高优提示
        hinted_specialist, clean_query = parse_capability_prefix(self.query)

        specialists = discover_specialists()
        prompt = build_triage_prompt(specialists)

        # 能力前缀高优提示
        if hinted_specialist:
            prompt += f"\n\n## 当前能力提示\n用户已选择「{hinted_specialist}」能力，请优先使用 ``task`` 工具委托给 ``{hinted_specialist}`` 处理。"
            self.query = clean_query

        tools = list(TOOL_REGISTRY.values())
        tools_used: set[str] = set()
        references: list[dict] = []
        full_text = ""
        trace_config = self._build_trace_config("triage")

        try:
            messages = await self._load_messages()
            agent = await create_triage_agent(
                tools=tools,
                system_prompt=prompt,
                gateway=model_gateway if model_gateway._active else None,
            )

            inputs = {"messages": messages}

            async for chunk in agent.astream_events(inputs, version="v2", config=trace_config):
                if self.cancel_event.is_set():
                    raise AgentStopped()

                kind = chunk["event"]

                if kind == "on_tool_start":
                    tool_name = chunk.get("name", "unknown")
                    tools_used.add(tool_name)
                    yield make_event("tool_start", toolName=tool_name)

                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")
                    if not chunk_obj:
                        continue

                    # reasoning_content
                    reasoning = None
                    if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                        reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                    if reasoning:
                        yield make_event("thinking", content=reasoning)

                    content = chunk_obj.content if hasattr(chunk_obj, "content") else ""
                    if content:
                        full_text += content
                        yield make_event("text", content=content)

            yield make_event("recommend", content="[]")

            # 保存消息
            self._save_message(
                answer=full_text,
                thinking="",
                references=json.dumps(references, ensure_ascii=False),
                recommend="[]",
                tools=",".join(tools_used),
                agent_type="triage",
            )

            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")

        except AgentStopped:
            yield make_sse(json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        except Exception as e:
            logger.error(f"TriageAgent 异常: {e}", exc_info=True)
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        finally:
            await self._cleanup()
