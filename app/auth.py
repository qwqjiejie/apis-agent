"""兼容导出；新代码使用 :mod:`app.modules.identity.auth`。"""

from app.modules.identity.auth import (
    generate_token,
    get_current_user_id,
    hash_password,
    verify_password,
    verify_token,
)

__all__ = [
    "generate_token",
    "get_current_user_id",
    "hash_password",
    "verify_password",
    "verify_token",
]
