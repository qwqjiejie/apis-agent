import asyncio
import json

from fastapi import APIRouter, UploadFile, File, Form, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import inspect_session_access
from app.modules.documents.events import event_bus
from app.modules.documents.service import file_service
from app.common.response import ok, ok_paged, error
from app.common.streaming import make_sse
from app.auth import get_current_user_id

router = APIRouter(prefix="/file", tags=["file"])


class FileListRequest(BaseModel):
    pageNum: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class FileDetailRequest(BaseModel):
    fileId: str = Field(..., min_length=1)


@router.post("/list")
async def file_list(req: FileListRequest, request: Request):
    files, total = file_service.list_files(
        page=req.pageNum,
        size=req.pageSize,
        user_id=get_current_user_id(request),
    )
    return ok_paged(files, total, req.pageNum, req.pageSize)


@router.post("/upload")
async def file_upload(
        request: Request,
        file: UploadFile = File(...),
        conversationId: str = Form(default=""),
):
    user_id = get_current_user_id(request)
    if conversationId and not inspect_session_access(request, conversationId).allowed:
        return error(403, "无权访问该会话")
    result = await file_service.upload(file, conversationId, user_id=user_id)
    return ok(result)


@router.post("/info")
async def file_info(req: FileDetailRequest, request: Request):
    info = file_service.get_info(req.fileId, user_id=get_current_user_id(request))
    if not info:
        return error(404, "文件不存在")
    return ok(info)


@router.post("/content")
async def file_content(req: FileDetailRequest, request: Request):
    content = file_service.get_content(req.fileId, user_id=get_current_user_id(request))
    if not content:
        return error(404, "文件不存在")
    return ok(content)


@router.post("/delete")
async def file_delete(req: FileDetailRequest, request: Request):
    ok_deleted = file_service.delete(req.fileId, user_id=get_current_user_id(request))
    if not ok_deleted:
        return error(404, "文件不存在")
    return ok(None)


@router.post("/exists")
async def file_exists(req: FileDetailRequest, request: Request):
    return ok(file_service.exists(req.fileId, user_id=get_current_user_id(request)))


@router.post("/progress")
async def file_progress(req: FileDetailRequest, request: Request):
    """SSE 流式推送文档处理进度。"""

    if not file_service.exists(req.fileId, user_id=get_current_user_id(request)):
        return error(404, "文件不存在")

    async def event_generator():
        queue = event_bus.subscribe(req.fileId)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=120)
                    yield make_sse(json.dumps(event, ensure_ascii=False))
                    if event.get("status") in ("ready", "failed", "skipped"):
                        break
                except asyncio.TimeoutError:
                    yield make_sse(json.dumps({"type": "error", "content": "进度超时"}, ensure_ascii=False))
                    break
        finally:
            event_bus.unsubscribe(req.fileId)

    return EventSourceResponse(event_generator())
