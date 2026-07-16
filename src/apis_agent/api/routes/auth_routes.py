"""认证路由 — 注册 / 登录 / 匿名 session 迁移。

设计：
- 注册：username + password → 创建用户，返回 JWT
- 登录：username + password → 校验，返回 JWT + 同步匿名 session
- 同步：登录后前端调用 /sync 将匿名 session 迁移到账号
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.apis_agent.auth import (
    generate_token,
    get_current_user_id,
    hash_password,
    verify_password,
)
from src.apis_agent.common.response import error, ok
from src.apis_agent.storage.db import new_session

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


# ── 启动时建表 ──

_ensure_table_sql = """CREATE TABLE IF NOT EXISTS agentx_user (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    username VARCHAR(50) NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_user_user_id ON agentx_user(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_user_username ON agentx_user(username);
"""


@router.post("/register")
async def register(req: RegisterRequest):
    """注册新用户。"""
    db = new_session()
    try:
        db.execute(_ensure_table_sql)
        db.commit()
    except Exception:
        db.rollback()
        return error(500, "数据库不可用")

    # 检查用户名是否已存在
    existing = db.execute(
        "SELECT id FROM agentx_user WHERE username = %s", (req.username,)
    ).fetchone()
    if existing:
        db.close()
        return error(400, "用户名已被注册")

    import uuid
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    hashed = hash_password(req.password)

    try:
        db.execute(
            "INSERT INTO agentx_user (user_id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, req.username, hashed),
        )
        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        return error(500, f"注册失败: {e}")

    db.close()
    token = generate_token(user_id, req.username)
    return ok({"token": token, "userId": user_id, "username": req.username})


@router.post("/login")
async def login(req: LoginRequest):
    """登录。返回 JWT。"""
    db = new_session()
    try:
        db.execute(_ensure_table_sql)
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        return error(500, "数据库不可用")

    row = db.execute(
        "SELECT user_id, username, password_hash FROM agentx_user WHERE username = %s",
        (req.username,),
    ).fetchone()
    db.close()

    if not row:
        return error(401, "用户名或密码错误")

    user_id, username, hashed = row
    if not verify_password(req.password, hashed):
        return error(401, "用户名或密码错误")

    token = generate_token(user_id, username)
    return ok({
        "token": token,
        "userId": user_id,
        "username": username,
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

    anonymous_id = f"anon_{req.anonymousId}"
    db = new_session()
    try:
        db.execute(
            "UPDATE agentx_session SET user_id = %s WHERE user_id = %s",
            (user_id, anonymous_id),
        )
        db.execute(
            "UPDATE agentx_file SET user_id = %s WHERE user_id = %s",
            (user_id, anonymous_id),
        )
        db.commit()
        count = db.rowcount
        db.close()
        logger.info(f"[Auth] 同步匿名 session: {anonymous_id} → {user_id}")
        return ok({"synced": True, "userId": user_id})
    except Exception as e:
        db.rollback()
        db.close()
        return error(500, f"同步失败: {e}")


@router.get("/me")
async def get_current_user(request: Request):
    """获取当前用户信息。"""
    user_id = get_current_user_id(request)
    is_anonymous = user_id.startswith("anon_")
    return ok({
        "userId": user_id,
        "isAnonymous": is_anonymous,
    })
