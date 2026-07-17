import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.common.exceptions import QueryTooLongError, ValidationError
from app.common.llm import _create_raw_llm
from app.common.logger import logger
from app.common.response import error, ok
from app.common.streaming import extract_text_content, make_event, make_sse
from app.config.settings import get_settings
from app.gateway.status_events import drain_gateway_status, gateway_status_queue
from app.harness.task_context import ChatContext, TaskStatus
from app.prompt.triage_prompt import parse_capability_prefix
from app.evaluation.online_eval import EvalRecord, online_eval
from app.auth import get_current_user_id
from app.service.session_service import store
from app.storage.db import new_session
from app.storage.models.ai_ppt_inst import PptInstRepo
from app.tool.bash_tool import resolve_confirmation

router = APIRouter(prefix="/agent", tags=["agent"])
chat_router = APIRouter(tags=["agent"])


class ChatRequest(BaseModel):
    message: str = Field(default="", min_length=0)
    query: str = Field(default="", min_length=0, description="(已废弃)")
    conversationId: str = Field(default="", min_length=0)
    fileIds: list[str] = Field(default_factory=list)
    online: bool = Field(default=True)
    userId: str = Field(default="")

    def get_message(self) -> str:
        return self.message or self.query

    def get_conversation_id(self) -> str:
        return self.conversationId or ""


class StopRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)


class ShellConfirmRequest(BaseModel):
    confirmId: str = Field(..., min_length=1)
    approved: bool


class TaskQueryRequest(BaseModel):
    taskId: str = Field(..., min_length=1)


class TaskResumeRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    action: Literal["approved", "rejected"] = Field(
        default="approved",
        description="approved=通过 rejected=拒绝",
    )
    comment: str = Field(default="", max_length=500)


class GatewaySwitchRequest(BaseModel):
    modelName: str = Field(..., min_length=1)


class FeedbackRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)
    rating: int = Field(..., ge=-1, le=1, description="1=点赞 -1=点踩")
    comment: str = Field(default="", max_length=500)


def _check_query_length(query: str):
    if len(query) > get_settings().max_query_length:
        raise QueryTooLongError(get_settings().max_query_length)


# ═══════════════════════════════════════════
# 统一对话入口 — 使用启动时创建的单例 Agent
# ═══════════════════════════════════════════

@chat_router.post("/chat")
@router.post("/chat", deprecated=True)
async def agent_chat(req: ChatRequest, request: Request):
    query = req.get_message()
    conversation_id = req.get_conversation_id()
    if not query:
        raise ValidationError("message 不能为空")
    _check_query_length(query)

    # ── 认证 + session 归属校验 ────────────────────
    user_id = get_current_user_id(request)
    runtime = request.app.state.container
    agent = runtime.agent
    semantic_memory = runtime.semantic_memory
    task_executor = runtime.task_executor
    task_context_manager = runtime.context_manager
    thread_id = conversation_id or str(uuid.uuid4())

    if conversation_id:
        owner = store.get_session_owner(conversation_id)
        if owner is not None and owner != user_id:
            return error(403, "无权访问该会话")
        try:
            store.touch_last_active(conversation_id)
        except Exception:
            pass

    # 能力前缀解析 — 注入 fileIds 上下文

    _, clean_query = parse_capability_prefix(query)
    if req.fileIds:
        file_ctx = "\n".join(f"- fileId: {fid}" for fid in req.fileIds)
        clean_query = f"{clean_query}\n\n用户上传的文件:\n{file_ctx}"

    async def event_generator():
        # ── 语义长期记忆注入（从 TriageAgent 封装下沉）──────────
        memory_context = ""
        try:
            memories = await semantic_memory.search(user_id, clean_query)
            memory_context = semantic_memory.build_context_injection(memories)
        except Exception:
            pass

        # ── 注入已完成的后台任务结果 ──────────────────────
        final_query = clean_query
        try:
            done_tasks = await task_executor.list_tasks_by_session(
                thread_id,
                user_id=user_id,
            )
            completed = [t for t in done_tasks
                         if t.get("status") == "completed" and t.get("result")]
            if completed:
                ctx_lines = [
                    f"[已完成后台任务 {t['taskId']}] {t.get('result', '')[:300]}"
                    for t in completed[-5:]
                ]
                final_query = clean_query + "\n\n[历史后台任务产出]\n" + "\n".join(ctx_lines)
        except Exception:
            pass

        messages = [{"role": "user", "content": final_query}]
        if memory_context:
            messages.insert(0, {"role": "system", "content": memory_context})

        chat_context = ChatContext(user_id=user_id, session_id=thread_id)
        parent_context = task_context_manager.get()
        task_context_manager.set(chat_context)
        config = {
            "recursion_limit": 100,
            "configurable": chat_context.configurable(),
        }

        full_answer = ""
        tools_used: set[str] = set()
        start_time = __import__("time").monotonic()
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
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")
                    if not chunk_obj:
                        continue

                    if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                        reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                        if reasoning:
                            yield make_event("thinking", content=reasoning)

                    content = extract_text_content(
                        chunk_obj.content if hasattr(chunk_obj, "content") else ""
                    )
                    if content:
                        full_answer += content
                        yield make_event("text", content=content)

            for status_event in drain_gateway_status(status_queue):
                yield make_event("status", **status_event)

            # ── 保存会话（含工具列表）──────────────
            try:
                _save_session(thread_id, clean_query, full_answer, user_id,
                              tools=",".join(sorted(tools_used)))
            except Exception:
                pass

            # ── 异步存储语义长期记忆 ────────────────
            if full_answer.strip():
                try:
                    import asyncio as _asyncio
                    _asyncio.create_task(
                        semantic_memory.add(user_id, clean_query, full_answer)
                    )
                except Exception:
                    pass

            # ── 在线评估记录 ───────────────────────
            try:
                total_time = int((__import__("time").monotonic() - start_time) * 1000)
                online_eval.record(EvalRecord(
                    session_id=thread_id,
                    query_length=len(clean_query),
                    answer_length=len(full_answer),
                    tool_count=len(tools_used),
                    completed=True,
                    total_response_ms=total_time,
                ))
            except Exception:
                pass

            # ── 首次对话生成标题 ────────────────────
            if not req.get_conversation_id() and full_answer.strip():
                try:
                    title = await _generate_title(clean_query, full_answer)
                    _update_session_title(thread_id, title)
                except Exception:
                    pass

            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        except Exception as e:
            logger.error(f"Agent 异常: {e}", exc_info=True)
            for status_event in drain_gateway_status(status_queue):
                yield make_event("status", **status_event)
            # 失败也记录评估
            try:
                total_time = int((__import__("time").monotonic() - start_time) * 1000)
                online_eval.record(EvalRecord(
                    session_id=thread_id,
                    query_length=len(clean_query),
                    answer_length=len(full_answer),
                    tool_count=len(tools_used),
                    completed=False,
                    total_response_ms=total_time,
                ))
            except Exception:
                pass
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        finally:
            task_context_manager.set(parent_context)
            gateway_status_queue.set(parent_status_queue)

    return EventSourceResponse(
        event_generator(),
        headers={"X-Session-Id": thread_id},
    )


# ═══════════════════════════════════════════
# PPT 下载 / 停止 / Shell确认 / 任务 / 网关
# ═══════════════════════════════════════════

@router.post("/pptx/download")
async def agent_pptx_download(req: StopRequest, request: Request):
    from minio import Minio
    from minio.error import S3Error


    if store.get_session_owner(req.conversationId) != get_current_user_id(request):
        return error(403, "无权访问该会话")

    repo = PptInstRepo()
    inst = repo.find_by_session_id(req.conversationId)
    if not inst or not inst.file_url:
        return error(404, "PPT文件不存在")

    file_url = inst.file_url
    if file_url.startswith("local://"):
        local_path = file_url[len("local://"):]
        if not os.path.exists(local_path):
            return error(404, "文件已被清理")
        return FileResponse(local_path, filename=os.path.basename(local_path),
                            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    if file_url.startswith("minio://"):
        bucket_obj = file_url[len("minio://"):]
        bucket, obj_name = bucket_obj.split("/", 1)
        try:
            client = Minio(f"{get_settings().minio_host}:{get_settings().minio_port}",
                           access_key=get_settings().minio_access_key,
                           secret_key=get_settings().minio_secret_key, secure=False)
            from io import BytesIO
            from fastapi.responses import StreamingResponse
            data = client.get_object(bucket, obj_name)
            return StreamingResponse(
                BytesIO(data.read()),
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": f"attachment; filename={obj_name.rsplit('/', 1)[-1]}"},
            )
        except S3Error:
            return error(404, "文件下载失败")
    return error(404, "无效的文件路径")


@router.post("/stop")
async def agent_stop(req: StopRequest, request: Request):
    if store.get_session_owner(req.conversationId) != get_current_user_id(request):
        return error(403, "无权访问该会话")
    await __import__("app.common.redis", fromlist=["publish_stop"]).publish_stop(req.conversationId)
    return ok(None, message="已发送停止信号")


@router.post("/shell/confirm")
async def shell_confirm(req: ShellConfirmRequest, request: Request):
    ok_result = resolve_confirmation(
        req.confirmId,
        req.approved,
        get_current_user_id(request),
    )
    if not ok_result:
        return error(404, "确认请求不存在或已过期")
    return ok(None, message="已确认" if req.approved else "已拒绝")


@router.post("/task/status")
async def task_status(req: TaskQueryRequest, request: Request):
    result = await request.app.state.container.task_executor.get_status(
        req.taskId,
        user_id=get_current_user_id(request),
    )
    if not result:
        return error(404, "任务不存在")
    return ok(result)


@router.post("/task/stream")
async def task_stream(req: TaskQueryRequest, request: Request):
    snapshot = await request.app.state.container.task_executor.get_status(
        req.taskId,
        user_id=get_current_user_id(request),
    )
    if not snapshot:
        async def _err():
            yield make_sse(json.dumps({"type": "error", "content": "任务不存在"}, ensure_ascii=False))
        return EventSourceResponse(_err())

    async def event_generator():
        status = snapshot["status"]
        if status == TaskStatus.COMPLETED.value:
            yield make_sse(json.dumps({"type": "text", "content": snapshot.get("result", "")}, ensure_ascii=False))
        elif status == TaskStatus.FAILED.value:
            yield make_sse(json.dumps({"type": "error", "content": snapshot.get("error", "执行失败")}, ensure_ascii=False))
        elif status == TaskStatus.CANCELLED.value:
            yield make_sse(json.dumps({"type": "error", "content": "任务已取消"}, ensure_ascii=False))
        elif status in (
            TaskStatus.CREATED.value,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.EXECUTING.value,
            TaskStatus.WAITING_HUMAN.value,
        ):
            yield make_sse(json.dumps({
                "type": "task_status",
                "taskId": req.taskId,
                "status": status,
                "approvalId": snapshot.get("approvalId", ""),
                "interruptInfo": snapshot.get("interruptInfo"),
            }, ensure_ascii=False))
        yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield make_sse("[DONE]")

    return EventSourceResponse(event_generator())


@router.post("/task/cancel")
async def task_cancel(req: TaskQueryRequest, request: Request):
    if await request.app.state.container.task_executor.cancel(
        req.taskId,
        user_id=get_current_user_id(request),
    ):
        return ok(None, message="已发送取消信号")
    return error(404, "任务不存在")


@router.post("/task/resume")
async def task_resume(req: TaskResumeRequest, request: Request):
    """恢复挂起的后台任务（HITL 审批完成后调用）。"""
    ok_resumed = await request.app.state.container.task_executor.resume(
        req.taskId,
        {"action": req.action, "comment": req.comment},
        user_id=get_current_user_id(request),
    )
    if not ok_resumed:
        return error(404, "任务不存在或非挂起状态")
    return ok(None, message=f"任务已恢复（{req.action}）")


@router.post("/task/list")
async def task_list(request: Request):
    return ok(await request.app.state.container.task_executor.list_tasks(
        user_id=get_current_user_id(request),
    ))


@router.post("/admin/gateway")
async def gateway_status(request: Request):
    gateway = request.app.state.container.model_gateway
    return ok(gateway.get_all_status())


@router.post("/admin/gateway/switch")
async def gateway_switch(req: GatewaySwitchRequest, request: Request):
    gateway = request.app.state.container.model_gateway
    try:
        await gateway.set_active(req.modelName)
        return ok(None, message=f"已切换到 {req.modelName}")
    except ValueError as e:
        return error(400, str(e))


# ═══════════════════════════════════════════
# 用户反馈
# ═══════════════════════════════════════════

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request):

    user_id = get_current_user_id(request)
    if store.get_session_owner(req.conversationId) != user_id:
        return error(403, "无权访问该会话")

    db = new_session()
    try:
        db.execute(
            """INSERT INTO agentx_feedback (session_id, user_id, rating, comment, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (req.conversationId, user_id, req.rating, req.comment, datetime.now(timezone.utc)),
        )
        db.commit()
        db.close()
        return ok(None, message="反馈已记录")
    except Exception as e:
        db.rollback()
        db.close()
        return error(500, str(e))


# ═══════════════════════════════════════════
# 会话保存 / 标题生成
# ═══════════════════════════════════════════

def _save_session(session_id: str, question: str, answer: str, user_id: str = "",
                  tools: str = ""):
    """写 agentx_session 表。"""


    store.save_message(
        session_id=session_id, question=question, answer=answer,
        user_id=user_id or "", agent_type="triage", tools=tools,
    )


def _update_session_title(session_id: str, title: str):
    try:

        db = new_session()
        db.execute(
            "UPDATE agentx_session SET title = %s WHERE session_id = %s",
            (title[:40], session_id),
        )
        db.commit()
        db.close()
    except Exception:
        pass


async def _generate_title(question: str, answer: str) -> str:
    """LLM 生成对话标题。"""

    llm = _create_raw_llm()
    prompt = f"请根据以下对话生成5-15字简短标题，只输出标题: 用户:{question[:200]} 助手:{answer[:200]}"
    try:
        resp = await llm.ainvoke(prompt)
        title = (resp.content if hasattr(resp, "content") else str(resp)).strip().replace("\n", "")[:15]
        return title or "新对话"
    except Exception:
        return "新对话"
