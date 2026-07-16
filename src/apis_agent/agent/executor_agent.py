"""ExecutorAgent — 后台任务执行引擎。

基于 create_react_agent，持有精简工具集（task/request_approval/read_task_journal）。
在 TaskExecutor 的后台 asyncio.Task 中运行，不继承 BaseAgent。
"""

import asyncio
import json
import logging

from src.apis_agent.common.streaming import make_event, make_sse
from src.apis_agent.harness.subagent_discovery import discover_specialists
from src.apis_agent.prompt.executor_prompt import build_executor_prompt
from src.apis_agent.tool import TOOL_REGISTRY

logger = logging.getLogger("apis")


class ExecutorAgent:
    """后台任务执行 Agent。

    不继承 BaseAgent（不在 HTTP 请求上下文中运行），
    在 TaskExecutor 的后台 asyncio.Task 中自治执行。

    与 TriageAgent 使用同一套 create_agent 工厂，
    差异仅在于 system_prompt（引导逐步执行）和 tools（精简）。
    """

    def __init__(self, snapshot, plan_text: str = ""):
        self.snapshot = snapshot
        self.plan_text = plan_text
        self.cancel_event = snapshot.cancel_event

    async def run(self):
        from src.apis_agent.agent.agent_factory import create_executor_agent
        from src.apis_agent.gateway.model_gateway import model_gateway

        yield {"_task_status": "running"}

        try:
            specialists = discover_specialists()
            prompt = build_executor_prompt(specialists)

            # Executor 工具：task（委托 Specialist）
            executor_tools: list = []
            for name in ("request_approval", "read_task_journal"):
                if name in TOOL_REGISTRY:
                    executor_tools.append(TOOL_REGISTRY[name])

            # 构建输入
            query = self.snapshot.query
            if self.plan_text:
                query = f"执行计划：\n{self.plan_text[:1000]}\n\n原始任务：{query}"

            agent = await create_executor_agent(
                tools=executor_tools,
                system_prompt=prompt,
                gateway=model_gateway if model_gateway._active else None,
            )

            inputs = {"messages": [("user", query)]}

            full_text = ""
            async for chunk in agent.astream_events(inputs, version="v2"):
                if self.cancel_event.is_set():
                    yield make_sse(json.dumps({"type": "text", "content": "\n\n[任务已取消]"}, ensure_ascii=False))
                    return

                kind = chunk["event"]

                if kind == "on_tool_start":
                    yield make_event("tool_start", toolName=chunk.get("name", ""))

                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")
                    if hasattr(chunk_obj, "content") and chunk_obj.content:
                        text = chunk_obj.content
                        full_text += text
                        yield make_event("text", content=text)

            self.snapshot.result = full_text
            yield {"_task_status": "completed"}

        except asyncio.CancelledError:
            yield {"_task_status": "cancelled"}
        except Exception as e:
            logger.error(f"[ExecutorAgent] 异常: {e}", exc_info=True)
            self.snapshot.error = str(e)
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
