"""兼容导出；新代码使用 PostgreSQL infrastructure 模型。"""

from app.infrastructure.postgres.models.file import AiFileInfo, FileInfoRepo

__all__ = ["AiFileInfo", "FileInfoRepo"]
