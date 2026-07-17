"""兼容导出；新代码使用 :mod:`app.infrastructure.postgres.repository`。"""

from app.infrastructure.postgres.repository import BaseRepository

__all__ = ["BaseRepository"]
