import time
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from src.api.session import store
from src.common.response import ok, ok_paged

router = APIRouter(prefix="/session", tags=["session"])


@router.post("")
async def create_session():
    cid = f"chat_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    return ok({"conversationId": cid, "question": "新对话"})


@router.get("/list")
async def list_sessions(pageNum: int = Query(default=1), pageSize: int = Query(default=100)):
    records, total = store.list_sessions(page=pageNum, size=pageSize)
    return ok_paged(records, total, pageNum, pageSize)


@router.get("/{conversation_id}")
async def get_session(conversation_id: str):
    session = store.get_session(conversation_id)
    if not session:
        return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "会话不存在"})
    return ok(session)


@router.delete("/{conversation_id}")
async def delete_session(conversation_id: str):
    ok_deleted = store.delete_session(conversation_id)
    if not ok_deleted:
        return JSONResponse(status_code=404, content={"code": 404, "data": None, "message": "会话不存在"})
    return JSONResponse({"code": 200, "data": None, "message": "success"})
