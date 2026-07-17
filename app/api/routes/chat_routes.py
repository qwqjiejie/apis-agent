"""同步聊天与 SSE 事件流入口。"""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.api.routes.agent_schemas import ChatRequest
from app.api.dependencies import inspect_session_access
from app.auth import get_current_user_id
from app.common.exceptions import QueryTooLongError, ValidationError
from app.common.logger import logger
from app.common.response import error
from app.common.streaming import extract_text_content, make_event, make_sse
from app.config.settings import get_settings
from app.evaluation.online_eval import EvalRecord, online_eval
from app.gateway.status_events import drain_gateway_status, gateway_status_queue
from app.harness.task_context import ChatContext
from app.prompt.triage_prompt import parse_capability_prefix
from app.service import chat_service
from app.service.session_service import store

router = APIRouter(tags=["agent"])
legacy_router = APIRouter(tags=["agent"])


def _check_query_length(query: str) -> None:
    if len(query) > get_settings().max_query_length:
        raise QueryTooLongError(get_settings().max_query_length)


@router.post("/chat")
@legacy_router.post("/chat", deprecated=True)
async def agent_chat(req: ChatRequest, request: Request):
    query = req.get_message()
    conversation_id = req.get_conversation_id()
    if not query:
        raise ValidationError("message 不能为空")
    _check_query_length(query)

    user_id = get_current_user_id(request)
    runtime = request.app.state.container
    agent = runtime.agent
    semantic_memory = runtime.semantic_memory
    task_executor = runtime.task_executor
    context_manager = runtime.context_manager
    thread_id = conversation_id or str(uuid.uuid4())

    if conversation_id:
        access = inspect_session_access(request, conversation_id)
        if access.exists and not access.allowed:
            return error(403, "无权访问该会话")
        try:
            store.touch_last_active(conversation_id)
        except Exception:
            pass

    _, clean_query = parse_capability_prefix(query)
    if req.fileIds:
        file_context = "\n".join(f"- fileId: {file_id}" for file_id in req.fileIds)
        clean_query = f"{clean_query}\n\n用户上传的文件:\n{file_context}"

    async def event_generator():
        memory_context = ""
        try:
            memories = await semantic_memory.search(user_id, clean_query)
            memory_context = semantic_memory.build_context_injection(memories)
        except Exception:
            pass

        final_query = clean_query
        try:
            done_tasks = await task_executor.list_tasks_by_session(
                thread_id,
                user_id=user_id,
            )
            completed = [
                task for task in done_tasks
                if task.get("status") == "completed" and task.get("result")
            ]
            if completed:
                lines = [
                    f"[已完成后台任务 {task['taskId']}] {task.get('result', '')[:300]}"
                    for task in completed[-5:]
                ]
                final_query = clean_query + "\n\n[历史后台任务产出]\n" + "\n".join(lines)
        except Exception:
            pass

        messages = [{"role": "user", "content": final_query}]
        if memory_context:
            messages.insert(0, {"role": "system", "content": memory_context})

        chat_context = ChatContext(user_id=user_id, session_id=thread_id)
        parent_context = context_manager.get()
        context_manager.set(chat_context)
        config = {
            "recursion_limit": 100,
            "configurable": chat_context.configurable(),
        }
        full_answer = ""
        tools_used: set[str] = set()
        start_time = time.monotonic()
        status_queue: asyncio.Queue[dict] = asyncio.Queue()
        parent_status_queue = gateway_status_queue.get()
        gateway_status_queue.set(status_queue)

        try:
            async for chunk in agent.astream_events(
                {"messages": messages},
                version="v2",
                config=config,
            ):
                for status_event in drain_gateway_status(status_queue):
                    yield make_event("status", **status_event)
                kind = chunk.get("event", "")
                if kind == "on_tool_start":
                    tool_name = chunk.get("name", "")
                    tools_used.add(tool_name)
                    yield make_event("tool_start", toolName=tool_name)
                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))
                elif kind == "on_chat_model_stream":
                    chunk_obj = chunk.get("data", {}).get("chunk", "")
                    if not chunk_obj:
                        continue
                    additional = getattr(chunk_obj, "additional_kwargs", {})
                    reasoning = additional.get("reasoning_content", "") if additional else ""
                    if reasoning:
                        yield make_event("thinking", content=reasoning)
                    content = extract_text_content(getattr(chunk_obj, "content", ""))
                    if content:
                        full_answer += content
                        yield make_event("text", content=content)

            for status_event in drain_gateway_status(status_queue):
                yield make_event("status", **status_event)
            try:
                chat_service.save_session(
                    thread_id,
                    clean_query,
                    full_answer,
                    user_id,
                    tools=",".join(sorted(tools_used)),
                )
            except Exception:
                pass
            if full_answer.strip():
                try:
                    asyncio.create_task(
                        semantic_memory.add(user_id, clean_query, full_answer)
                    )
                except Exception:
                    pass
            _record_evaluation(
                thread_id,
                clean_query,
                full_answer,
                tools_used,
                start_time,
                completed=True,
            )
            if not conversation_id and full_answer.strip():
                try:
                    title = await chat_service.generate_title(clean_query, full_answer)
                    chat_service.update_session_title(thread_id, title)
                except Exception:
                    pass
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        except Exception as exc:
            logger.error(f"Agent 异常: {exc}", exc_info=True)
            for status_event in drain_gateway_status(status_queue):
                yield make_event("status", **status_event)
            _record_evaluation(
                thread_id,
                clean_query,
                full_answer,
                tools_used,
                start_time,
                completed=False,
            )
            yield make_sse(json.dumps({"type": "error", "content": str(exc)}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        finally:
            context_manager.set(parent_context)
            gateway_status_queue.set(parent_status_queue)

    return EventSourceResponse(event_generator(), headers={"X-Session-Id": thread_id})


def _record_evaluation(
    session_id: str,
    query: str,
    answer: str,
    tools_used: set[str],
    start_time: float,
    *,
    completed: bool,
) -> None:
    try:
        online_eval.record(EvalRecord(
            session_id=session_id,
            query_length=len(query),
            answer_length=len(answer),
            tool_count=len(tools_used),
            completed=completed,
            total_response_ms=int((time.monotonic() - start_time) * 1000),
        ))
    except Exception:
        pass
