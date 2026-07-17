"""ExecutorAgent — 后台任务执行引擎。

基于 deepagents.create_deep_agent，内置 task 工具（原生 SubAgent spawn）。
在 TaskExecutor 的后台 asyncio.Task 中运行，不继承 BaseAgent。
"""

import asyncio
import json
import logging

from langgraph.types import Command

from app.bootstrap.container import get_application_container
from app.common.streaming import extract_text_content, make_event, make_sse
from app.modules.tasks.context import ChatContext

logger = logging.getLogger("apis")


class ExecutorAgent:
    """后台任务执行 Agent。

    不继承 BaseAgent（不在 HTTP 请求上下文中运行），
    在 TaskExecutor 的后台 asyncio.Task 中自治执行。

    与 TriageAgent 使用同一套 create_agent 工厂，
    差异仅在于 system_prompt（引导逐步执行）和 tools（精简）。
    """

    def __init__(
        self,
        snapshot,
        plan_text: str = "",
        *,
        executor_agent=None,
        context_manager=None,
    ):
        self.snapshot = snapshot
        self.plan_text = plan_text
        self.cancel_event = snapshot.cancel_event
        self._executor_agent = executor_agent
        self._context_manager = context_manager

    async def run(self):
        yield {"_task_status": "running"}

        query = self.snapshot.query
        if self.plan_text:
            query = f"执行计划：\n{self.plan_text[:1000]}\n\n原始任务：{query}"

        async for event in self._stream({"messages": [("user", query)]}):
            yield event

    async def resume(self, resume_data: dict):
        """使用 LangGraph Command 恢复同一 task_id 对应的 checkpoint。"""
        command = _build_resume_command(resume_data)
        async for event in self._stream(command):
            yield event

    async def _stream(self, inputs):
        executor_agent = self._executor_agent
        context_manager = self._context_manager
        if executor_agent is None or context_manager is None:
            container = get_application_container()
            executor_agent = executor_agent or container.executor_agent
            context_manager = context_manager or container.context_manager

        parent_context = context_manager.get()
        context_manager.set(ChatContext(
            user_id=self.snapshot.user_id or parent_context.user_id,
            session_id=self.snapshot.session_id or self.snapshot.conversation_id,
            task_id=self.snapshot.task_id,
            trace_id=parent_context.trace_id,
        ))
        try:
            if executor_agent is None:
                raise RuntimeError("executor_agent 未初始化（lifespan 尚未完成依赖装配）")

            # 后台任务用独立 thread_id（=task_id），与 /chat 会话历史隔离；
            # checkpointer 会为其维护独立历史，支持中断恢复
            config = {
                "recursion_limit": 200,
                "configurable": {"thread_id": self.snapshot.task_id},
            }
            interrupts = ()
            async for chunk in executor_agent.astream_events(
                inputs,
                version="v2",
                config=config,
            ):
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
                        yield make_event("text", content=text)

            if not interrupts and hasattr(executor_agent, "aget_state"):
                state = await executor_agent.aget_state(config)
                interrupts = getattr(state, "interrupts", ())

            if interrupts:
                interrupt_info, approval_id = _serialize_interrupts(interrupts)
                self.snapshot.interrupt_info = interrupt_info
                self.snapshot.approval_id = approval_id
                yield {
                    "_task_status": "waiting_human",
                    "interrupt_info": interrupt_info,
                    "approval_id": approval_id,
                }
                return

            yield {"_task_status": "completed"}

        except asyncio.CancelledError:
            yield {"_task_status": "cancelled"}
        except Exception as e:
            logger.error(f"[ExecutorAgent] 异常: {e}", exc_info=True)
            self.snapshot.error = str(e)
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
            yield {"_task_status": "failed"}
        finally:
            context_manager.set(parent_context)


def _build_resume_command(resume_data: dict) -> Command:
    action = resume_data.get("action", "approved")
    comment = str(resume_data.get("comment", "") or "")
    if action == "approved":
        decision = {"type": "approve"}
    elif action == "rejected":
        decision = {
            "type": "reject",
            "message": comment or "用户拒绝了该审批请求",
        }
    else:
        raise ValueError(f"不支持的审批动作: {action}")
    return Command(resume={"decisions": [decision]})


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
