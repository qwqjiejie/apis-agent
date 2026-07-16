import asyncio
import json

from fastapi import APIRouter, UploadFile, File, Form, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.apis_agent.document.document_event_bus import event_bus
from src.apis_agent.service.file_service import file_service
from src.apis_agent.common.response import ok, ok_paged, error
from src.apis_agent.common.streaming import make_sse

router = APIRouter(prefix="/file", tags=["file"])


class FileListRequest(BaseModel):
    pageNum: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class FileDetailRequest(BaseModel):
    fileId: str = Field(..., min_length=1)


@router.post("/list")
async def file_list(req: FileListRequest):
    files, total = file_service.list_files(page=req.pageNum, size=req.pageSize)
    return ok_paged(files, total, req.pageNum, req.pageSize)


@router.post("/upload")
async def file_upload(
        file: UploadFile = File(...),
        conversationId: str = Form(default=""),
):
    result = await file_service.upload(file, conversationId)
    return ok(result)


@router.post("/info")
async def file_info(req: FileDetailRequest):
    info = file_service.get_info(req.fileId)
    if not info:
        return error(404, "文件不存在")
    return ok(info)


@router.post("/content")
async def file_content(req: FileDetailRequest):
    content = file_service.get_content(req.fileId)
    if not content:
        return error(404, "文件不存在")
    return ok(content)


@router.post("/delete")
async def file_delete(req: FileDetailRequest):
    ok_deleted = file_service.delete(req.fileId)
    if not ok_deleted:
        return error(404, "文件不存在")
    return ok(None)


@router.post("/exists")
async def file_exists(req: FileDetailRequest):
    return ok(file_service.exists(req.fileId))


@router.post("/progress")
async def file_progress(req: FileDetailRequest):
    """SSE 流式推送文档处理进度。"""

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
