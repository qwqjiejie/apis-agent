"""认证路由 — 注册 / 登录 / 匿名 session 迁移。

设计：
- 注册：username + password → 创建用户，返回 JWT
- 登录：username + password → 校验，返回 JWT + 同步匿名 session
- 同步：登录后前端调用 /sync 将匿名 session 迁移到账号
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.modules.identity.auth import (
    generate_token,
    get_current_user_id,
)
from app.modules.identity.service import (
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
    identity_service,
)
from app.common.response import error, ok

logger = logging.getLogger("apis")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4, max_length=100)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class SyncRequest(BaseModel):
    anonymousId: str = Field(..., min_length=1, description="匿名用户ID（X-Anonymous-Id 的值）")


@router.post("/register")
async def register(req: RegisterRequest):
    """注册新用户。"""
    try:
        identity = await run_in_threadpool(
            identity_service.register,
            req.username,
            req.password,
        )
    except UsernameAlreadyExistsError:
        return error(400, "用户名已被注册")
    except Exception:
        logger.exception("注册失败")
        return error(503, "数据库不可用")

    token = generate_token(identity.user_id, identity.username)
    return ok({
        "token": token,
        "userId": identity.user_id,
        "username": identity.username,
    })


@router.post("/login")
async def login(req: LoginRequest):
    """登录。返回 JWT。"""
    try:
        identity = await run_in_threadpool(
            identity_service.login,
            req.username,
            req.password,
        )
    except InvalidCredentialsError:
        return error(401, "用户名或密码错误")
    except Exception:
        logger.exception("登录失败")
        return error(503, "数据库不可用")

    token = generate_token(identity.user_id, identity.username)
    return ok({
        "token": token,
        "userId": identity.user_id,
        "username": identity.username,
        "message": "登录成功。请调用 /auth/sync 将匿名会话迁移到账号",
    })


@router.post("/sync")
async def sync_anonymous_sessions(req: SyncRequest, request: Request):
    """将匿名用户的 session 迁移到登录账号。

    前端登录后调用此接口：
    - 传入匿名 ID
    - 后端将该匿名 ID 下的所有 session 迁移到当前登录用户
    """
    user_id = get_current_user_id(request)
    if user_id.startswith("anon_"):
        return error(401, "请先登录再同步")

    try:
        count = await run_in_threadpool(
            identity_service.sync_anonymous_data,
            req.anonymousId,
            user_id,
        )
        logger.info(
            "[Auth] 同步匿名数据: anon_%s -> %s (%d)",
            req.anonymousId,
            user_id,
            count,
        )
        return ok({"synced": True, "userId": user_id, "count": count})
    except Exception:
        logger.exception("同步匿名数据失败")
        return error(503, "数据库不可用")


@router.get("/me")
async def get_current_user(request: Request):
    """获取当前用户信息。"""
    user_id = get_current_user_id(request)
    is_anonymous = user_id.startswith("anon_")
    return ok({
        "userId": user_id,
        "isAnonymous": is_anonymous,
    })
