import uuid

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

from src.common.response import ok

router = APIRouter(prefix="/file", tags=["file"])


@router.get("/list")
async def file_list():
    return ok([])


@router.post("/upload")
async def file_upload(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    return ok({"fileId": file_id, "fileName": file.filename})


@router.get("/info/{file_id}")
async def file_info(file_id: str):
    return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "文件不存在"})


@router.get("/content/{file_id}")
async def file_content(file_id: str):
    return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "文件不存在"})


@router.delete("/{file_id}")
async def file_delete(file_id: str):
    return JSONResponse({"code": 200, "data": None, "message": "success"})


@router.get("/exists/{file_id}")
async def file_exists(file_id: str):
    return ok(False)
