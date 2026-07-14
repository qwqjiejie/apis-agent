from fastapi import APIRouter, UploadFile, File, Query, Form
from fastapi.responses import JSONResponse

from src.dodo_agent.api.file_service import file_service
from src.dodo_agent.common.response import ok

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
    if "error" in result:
        return JSONResponse(status_code=400, content={"code": 400, "data": None, "message": result["error"]})
    return ok(result)


@router.get("/info/{file_id}")
async def file_info(file_id: str):
    info = file_service.get_info(file_id)
    if not info:
        return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "文件不存在"})
    return ok(info)


@router.get("/content/{file_id}")
async def file_content(file_id: str):
    content = file_service.get_content(file_id)
    if not content:
        return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "文件不存在"})
    return ok(content)


@router.delete("/{file_id}")
async def file_delete(file_id: str):
    ok_deleted = file_service.delete(file_id)
    if not ok_deleted:
        return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "文件不存在"})
    return JSONResponse({"code": 200, "data": None, "message": "success"})


@router.get("/exists/{file_id}")
async def file_exists(file_id: str):
    return ok(file_service.exists(file_id))
