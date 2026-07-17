"""ExecutorAgent — 后台任务执行引擎。

基于 deepagents.create_deep_agent，内置 task 工具（原生 SubAgent spawn）。
在 TaskExecutor 的后台 asyncio.Task 中运行，不继承 BaseAgent。
"""

import asyncio
import json
import logging

from app.common.streaming import extract_text_content, make_event, make_sse

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
        # 复用 lifespan 注入的单例 executor_agent（app.state.executor_agent），
        # 该单例已绑定 checkpointer/store/interrupt_on/middleware，避免每次 new
        # 导致后台任务丢失 HITL/历史/热加载联动。
        from app.harness.task_executor import task_executor

        yield {"_task_status": "running"}

        try:
            agent = task_executor.executor_agent
            if agent is None:
                raise RuntimeError("executor_agent 未初始化（lifespan 未注入 task_executor.executor_agent）")

            # 构建输入
            query = self.snapshot.query
            if self.plan_text:
                query = f"执行计划：\n{self.plan_text[:1000]}\n\n原始任务：{query}"

            inputs = {"messages": [("user", query)]}

            full_text = ""
            # 后台任务用独立 thread_id（=task_id），与 /chat 会话历史隔离；
            # checkpointer 会为其维护独立历史，支持中断恢复
            config = {
                "recursion_limit": 200,
                "configurable": {"thread_id": self.snapshot.task_id},
            }
            interrupts = ()
            async for chunk in agent.astream_events(inputs, version="v2", config=config):
                if self.cancel_event.is_set():
                    yield make_sse(json.dumps({"type": "text", "content": "\n\n[任务已取消]"}, ensure_ascii=False))
                    return

                kind = chunk["event"]

                if kind == "on_chain_stream":
                    stream_chunk = chunk.get("data", {}).get("chunk", {})
                    if isinstance(stream_chunk, dict) and stream_chunk.get("__interrupt__"):
                        interrupts = stream_chunk["__interrupt__"]

                if kind == "on_tool_start":
                    yield make_event("tool_start", toolName=chunk.get("name", ""))

                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")
                    if hasattr(chunk_obj, "content"):
                        text = extract_text_content(chunk_obj.content)
                    else:
                        text = ""
                    if text:
                        full_text += text
                        yield make_event("text", content=text)

            if not interrupts and hasattr(agent, "aget_state"):
                state = await agent.aget_state(config)
                interrupts = getattr(state, "interrupts", ())

            if interrupts:
                interrupt_info, approval_id = _serialize_interrupts(interrupts)
                self.snapshot.result = full_text
                self.snapshot.result_summary = full_text[:500]
                self.snapshot.interrupt_info = interrupt_info
                self.snapshot.approval_id = approval_id
                yield {
                    "_task_status": "waiting_human",
                    "interrupt_info": interrupt_info,
                    "approval_id": approval_id,
                }
                return

            self.snapshot.result = full_text
            yield {"_task_status": "completed"}

        except asyncio.CancelledError:
            yield {"_task_status": "cancelled"}
        except Exception as e:
            logger.error(f"[ExecutorAgent] 异常: {e}", exc_info=True)
            self.snapshot.error = str(e)
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))


def _serialize_interrupts(interrupts) -> tuple[dict, str]:
    """Convert LangGraph Interrupt objects into a JSON-serializable task payload."""
    serialized = []
    approval_id = ""
    primary_value = None

    for item in interrupts:
        value = getattr(item, "value", None)
        interrupt_id = getattr(item, "id", "")
        serialized.append({"id": interrupt_id, "value": value})
        if primary_value is None:
            primary_value = value

        if isinstance(value, dict):
            for request in value.get("action_requests", []):
                if not isinstance(request, dict):
                    continue
                args = request.get("args", {})
                if isinstance(args, dict) and args.get("approval_id"):
                    approval_id = str(args["approval_id"])
                    break

    info = dict(primary_value) if isinstance(primary_value, dict) else {}
    info["interrupts"] = serialized
    return info, approval_id
