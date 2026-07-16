import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.apis_agent.service.session_service import store
from src.apis_agent.common.response import ok, ok_paged, error
from src.apis_agent.auth import get_current_user_id

router = APIRouter(prefix="/session", tags=["session"])


class SessionListRequest(BaseModel):
    pageNum: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)
    userId: str = Field(default="", description="当前用户ID（匿名UUID或登录token）")


class SessionDetailRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)
    userId: str = Field(default="")


class SessionDeleteRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)
    userId: str = Field(default="")


@router.post("")
async def create_session():
    session_token = secrets.token_urlsafe(24)
    cid = f"sess_{session_token}"
    return ok({"conversationId": cid, "question": "新对话"})


@router.post("/list")
async def list_sessions(req: SessionListRequest, request: Request):
    user_id = req.userId or get_current_user_id(request)
    records, total = store.list_sessions(page=req.pageNum, size=req.pageSize, user_id=user_id)
    return ok_paged(records, total, req.pageNum, req.pageSize)


@router.post("/detail")
async def get_session(req: SessionDetailRequest):
    session = store.get_session(req.conversationId)
    if not session:
        return error(404, "会话不存在")
    return ok(session)


@router.post("/delete")
async def delete_session(req: SessionDeleteRequest):
    ok_deleted = store.delete_session(req.conversationId)
    if not ok_deleted:
        return error(404, "会话不存在")
    return ok(None)
