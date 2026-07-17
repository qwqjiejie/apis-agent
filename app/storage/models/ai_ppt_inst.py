"""兼容导出；新代码使用 PostgreSQL infrastructure 模型。"""

from app.infrastructure.postgres.models.ppt import AiPptInst, PptInstRepo, PptStatus

__all__ = ["AiPptInst", "PptInstRepo", "PptStatus"]
