from fastapi import APIRouter, UploadFile, File, Query, Form

from src.dodo_agent.service.file_service import file_service
from src.dodo_agent.common.response import ok, error

router = APIRouter(prefix="/file", tags=["file"])


@router.get("/list")
async def file_list():
    files = file_service.list_files()
    return ok(files)


@router.post("/upload")
async def file_upload(
        file: UploadFile = File(...),
        conversationId: str = Form(default=""),
):
    result = file_service.upload(file, conversationId)
    return ok(result)


@router.get("/info/{file_id}")
async def file_info(file_id: str):
    info = file_service.get_info(file_id)
    if not info:
        return error(404, "文件不存在")
    return ok(info)


@router.get("/content/{file_id}")
async def file_content(file_id: str):
    content = file_service.get_content(file_id)
    if not content:
        return error(404, "文件不存在")
    return ok(content)


@router.delete("/{file_id}")
async def file_delete(file_id: str):
    ok_deleted = file_service.delete(file_id)
    if not ok_deleted:
        return error(404, "文件不存在")
    return ok(None)


@router.get("/exists/{file_id}")
async def file_exists(file_id: str):
    return ok(file_service.exists(file_id))
