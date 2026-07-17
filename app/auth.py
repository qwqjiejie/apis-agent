"""轻量认证 — 匿名用户识别 + JWT 签发/校验。

设计原则（对标豆包）：
- 无需登录即可使用：匿名用户通过 X-Anonymous-Id 请求头标识
- 匿名 ID 由前端生成（UUID），存储在 localStorage，跨标签页共享
- 注册/登录后签发 JWT，前端替换 X-Anonymous-Id 为 Authorization: Bearer <token>
- 登录时自动将匿名 session 迁移到账号下

使用方式:
    from app.auth import get_current_user_id

    # 在路由中获取当前用户ID（匿名UUID或登录用户ID）
    user_id = get_current_user_id(request)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.config.settings import get_settings

logger = logging.getLogger("apis")

# 简单 JWT 实现（不引入第三方库，避免额外依赖）
_JWT_SECRET = get_settings().jwt_secret or os.environ.get("JWT_SECRET", "")
if not _JWT_SECRET:
    _JWT_SECRET = uuid.uuid4().hex
    logger.warning("JWT_SECRET 未配置，登录令牌将在服务重启后失效")
_JWT_EXPIRE_HOURS = 72


def _base64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _base64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def generate_token(user_id: str, username: str = "") -> str:
    """签发 JWT。"""
    import json
    header = _base64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _base64url_encode(json.dumps({
        "sub": user_id,
        "username": username,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS)).timestamp()),
    }).encode())
    signature = _base64url_encode(
        hmac.new(
            _JWT_SECRET.encode(),
            f"{header}.{payload}".encode(),
            hashlib.sha256,
        ).digest()
    )
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> dict | None:
    """校验 JWT，返回 payload 或 None。"""
    try:
        import json
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = _base64url_encode(
            hmac.new(
                _JWT_SECRET.encode(),
                f"{header}.{payload}".encode(),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(signature, expected_sig):
            return None
        data = json.loads(_base64url_decode(payload))
        exp = data.get("exp", 0)
        if exp < datetime.now(timezone.utc).timestamp():
            return None
        return data
    except Exception:
        return None


def get_current_user_id(request: Request) -> str:
    """从请求中提取当前用户 ID。

    优先级：
    1. Authorization: Bearer <token> → 解析 JWT 获取 user_id
    2. X-Anonymous-Id: <uuid> → 匿名用户 ID
    3. 无任何标识 → 自动生成匿名 ID 并返回
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = verify_token(token)
        if payload:
            return payload["sub"]

    anonymous_id = request.headers.get("X-Anonymous-Id", "")
    if anonymous_id:
        return f"anon_{anonymous_id}"

    # 无任何标识，生成临时匿名 ID
    return f"anon_{uuid.uuid4().hex}"


def hash_password(password: str) -> str:
    """SHA-256 哈希密码（生产环境应使用 Argon2id）。"""
    salt = _JWT_SECRET[:16]
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed
