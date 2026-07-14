import asyncio
import json
import re
import time

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import JSONResponse

from src.dodo_agent.agent.react_agent import build_react_agent, build_skills_agent
from src.dodo_agent.api.file_service import file_service
from src.dodo_agent.api.rag_service import build_context
from src.dodo_agent.api.session import store
from src.dodo_agent.common.logger import logger
from src.dodo_agent.common.redis import listen_stop, publish_stop
from src.dodo_agent.config.settings import settings

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
RECOMMEND_PATTERN = re.compile(r"<recommend>(.*?)</recommend>", re.DOTALL)

router = APIRouter(prefix="/agent", tags=["agent"])

_running_tasks: dict[str, asyncio.Event] = {}


def _build_history_messages(history: list[dict]) -> list:
    msgs = []
    for h in history:
        msgs.append(("user", h["question"]))
        if h.get("answer"):
            msgs.append(("assistant", h["answer"]))
    return msgs


async def stream_agent(conversation_id: str, query: str, file_id: str = "", agent_type: str = "chat"):
    cancel_event = asyncio.Event()
    _running_tasks[conversation_id] = cancel_event
    redis_listener_task = asyncio.create_task(listen_stop(conversation_id, cancel_event))

    builder = build_skills_agent if agent_type == "skills" else build_react_agent
    agent = builder()
    history = store.load_history(conversation_id, limit=settings.max_history_rounds)
    history_msgs = _build_history_messages(history)

    file_context = ""
    if file_id:
        content = file_service.get_content(file_id)
        if content and content.get("extractedText"):
            ctx = build_context(query, file_id, content["extractedText"])
            if ctx:
                file_context = (
                    f"\n\n【参考以下文件内容回答问题，优先基于文件内容作答，若文件内容不足以回答再结合搜索】\n\n{ctx}"
                )

    inputs = {"messages": history_msgs + [("user", query + file_context)]}

    final_text = ""
    references = []
    thinking_parts: list[str] = []
    tools_used = set()
    think_buffer = ""
    recommend_buffer = ""
    recommend_json = ""
    t0 = time.time()
    first_token_sent = False
    first_response_ms = 0

    try:
        async for chunk in agent.astream_events(inputs, version="v2"):
            if cancel_event.is_set():
                yield {"event": "message",
                       "data": json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False)}
                yield {"event": "message", "data": json.dumps({"type": "complete"}, ensure_ascii=False)}
                yield {"event": "message", "data": "[DONE]"}
                return

            kind = chunk["event"]

            if kind == "on_tool_start":
                name = chunk.get("name", "unknown")
                tools_used.add(name)
                yield {"event": "message", "data": json.dumps(
                    {"type": "tool_start", "toolName": name, "toolCallId": chunk.get("run_id", "")},
                    ensure_ascii=False)}

            elif kind == "on_tool_end":
                name = chunk.get("name", "unknown")
                yield {"event": "message", "data": json.dumps(
                    {"type": "tool_end", "toolName": name, "toolCallId": chunk.get("run_id", "")},
                    ensure_ascii=False)}
                output = chunk.get("data", {}).get("output", "")
                if isinstance(output, str) and "SOURCES:" in output:
                    import ast
                    try:
                        sources_str = output.split("SOURCES: ", 1)[1].split("\n\nDETAILS:")[
                            0] if "SOURCES: " in output else "[]"
                        refs = ast.literal_eval(sources_str)
                        references.extend(refs)
                        if refs:
                            yield {"event": "message", "data": json.dumps(
                                {"type": "reference", "content": refs}, ensure_ascii=False)}
                    except Exception:
                        pass

            elif kind == "on_chat_model_stream":
                data = chunk.get("data", {})
                chunk_obj = data.get("chunk", "")

                reasoning = None
                if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                    reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                if reasoning:
                    thinking_parts.append(reasoning)
                    yield {"event": "message", "data": json.dumps(
                        {"type": "thinking", "content": reasoning}, ensure_ascii=False)}

                content_text = chunk_obj.content if hasattr(chunk_obj, "content") and chunk_obj.content else ""
                if content_text:
                    think_buffer += content_text
                    while True:
                        m = THINK_PATTERN.search(think_buffer)
                        if not m:
                            break
                        think_content = m.group(1)
                        if think_content.strip():
                            thinking_parts.append(think_content)
                            yield {"event": "message", "data": json.dumps(
                                {"type": "thinking", "content": think_content}, ensure_ascii=False)}
                        think_buffer = think_buffer[:m.start()] + think_buffer[m.end():]

                    if "<think>" in think_buffer:
                        tag_pos = think_buffer.rfind("<think>")
                        text_part = think_buffer[:tag_pos]
                        if text_part:
                            final_text += text_part
                            if not first_token_sent:
                                first_response_ms = int((time.time() - t0) * 1000)
                                first_token_sent = True
                            yield {"event": "message", "data": json.dumps(
                                {"type": "text", "content": text_part}, ensure_ascii=False)}
                        think_buffer = think_buffer[tag_pos:]
                    else:
                        if think_buffer:
                            if recommend_buffer:
                                recommend_buffer += think_buffer
                            elif "<recommend" in think_buffer:
                                idx = think_buffer.find("<recommend")
                                text_part = think_buffer[:idx]
                                if text_part:
                                    final_text += text_part
                                    if not first_token_sent:
                                        first_response_ms = int((time.time() - t0) * 1000)
                                        first_token_sent = True
                                    yield {"event": "message", "data": json.dumps(
                                        {"type": "text", "content": text_part}, ensure_ascii=False)}
                                recommend_buffer += think_buffer[idx:]
                            else:
                                final_text += think_buffer
                                if not first_token_sent:
                                    first_response_ms = int((time.time() - t0) * 1000)
                                    first_token_sent = True
                                yield {"event": "message", "data": json.dumps(
                                    {"type": "text", "content": think_buffer}, ensure_ascii=False)}
                        think_buffer = ""
                        if recommend_buffer and "</recommend>" in recommend_buffer:
                            m = RECOMMEND_PATTERN.search(recommend_buffer)
                            if m:
                                recommend_json = m.group(1).strip()
                            recommend_buffer = ""

    except asyncio.CancelledError:
        yield {"event": "message", "data": json.dumps({"type": "error", "content": "任务已取消"}, ensure_ascii=False)}
    except Exception as e:
        yield {"event": "message", "data": json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)}
    finally:
        redis_listener_task.cancel()
        try:
            await redis_listener_task
        except asyncio.CancelledError:
            pass
        _running_tasks.pop(conversation_id, None)

    if recommend_buffer:
        m = RECOMMEND_PATTERN.search(recommend_buffer)
        if m and not recommend_json:
            recommend_json = m.group(1).strip()
        recommend_buffer = ""

    if think_buffer.strip():
        clean = THINK_PATTERN.sub("", think_buffer).strip()
        if clean:
            final_text += clean
            yield {"event": "message", "data": json.dumps({"type": "text", "content": clean}, ensure_ascii=False)}

    if not first_token_sent:
        first_response_ms = int((time.time() - t0) * 1000)
    total_ms = int((time.time() - t0) * 1000)

    if not recommend_json:
        m = RECOMMEND_PATTERN.search(final_text)
        if m:
            recommend_json = m.group(1).strip()
            final_text = RECOMMEND_PATTERN.sub("", final_text).strip()
    if not recommend_json:
        recommend_json = "[]"

    store.save_message(
        session_id=conversation_id,
        question=query,
        answer=final_text,
        thinking="\n".join(thinking_parts),
        reference=json.dumps(references, ensure_ascii=False),
        recommend=recommend_json,
        tools=",".join(tools_used),
        agent_type=agent_type,
        fileid=file_id,
    )

    yield {"event": "message", "data": json.dumps({"type": "recommend", "content": recommend_json}, ensure_ascii=False)}
    yield {"event": "message", "data": json.dumps({"type": "complete"}, ensure_ascii=False)}
    yield {"event": "message", "data": "[DONE]"}


@router.get("/chat/stream")
async def agent_chat_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(default=""),
):
    async def event_generator():
        async for payload in stream_agent(conversationId, query, fileId):
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/file/stream")
async def agent_file_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(...),
):
    async def event_generator():
        async for payload in stream_agent(conversationId, query, fileId):
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/pptx/stream")
async def agent_pptx_stream(query: str = Query(...), conversationId: str = Query(...)):
    async def event_generator():
        yield {"event": "message",
               "data": json.dumps({"type": "error", "content": "PPT生成功能尚未实现"}, ensure_ascii=False)}
        yield {"event": "message", "data": json.dumps({"type": "complete"}, ensure_ascii=False)}
        yield {"event": "message", "data": "[DONE]"}

    return EventSourceResponse(event_generator())


@router.get("/deep/stream")
async def agent_deep_stream(query: str = Query(...), conversationId: str = Query(...)):
    async def event_generator():
        yield {"event": "message",
               "data": json.dumps({"type": "error", "content": "深度研究功能尚未实现"}, ensure_ascii=False)}
        yield {"event": "message", "data": json.dumps({"type": "complete"}, ensure_ascii=False)}
        yield {"event": "message", "data": "[DONE]"}

    return EventSourceResponse(event_generator())


@router.get("/skills/stream")
async def agent_skills_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(default=""),
):
    async def event_generator():
        async for payload in stream_agent(conversationId, query, fileId, agent_type="skills"):
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/stop")
async def agent_stop(conversationId: str = Query(...)):
    event = _running_tasks.get(conversationId)
    if event:
        event.set()
    await publish_stop(conversationId)
    if event:
        return JSONResponse({"code": 200, "data": None, "message": "已发送停止信号"})
    return JSONResponse({"code": 200, "data": None, "message": "无运行中的任务"})
