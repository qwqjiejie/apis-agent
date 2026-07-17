"""兼容导出；新代码使用 PostgreSQL infrastructure 模型。"""

from app.infrastructure.postgres.models.session import AiSession, AiSessionRepo, Base

__all__ = ["AiSession", "AiSessionRepo", "Base"]
