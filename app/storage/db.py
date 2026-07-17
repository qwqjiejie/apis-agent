"""兼容导出；新代码使用 :mod:`app.infrastructure.postgres.database`。"""

from app.infrastructure.postgres.database import (
    check_db,
    dispose_database,
    get_engine,
    new_session,
    session_scope,
)

__all__ = [
    "check_db",
    "dispose_database",
    "get_engine",
    "new_session",
    "session_scope",
]
