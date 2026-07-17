"""PostgreSQL ORM 模型。"""

from app.infrastructure.postgres.models.file import AiFileInfo, FileInfoRepo
from app.infrastructure.postgres.models.ppt import AiPptInst, PptInstRepo, PptStatus
from app.infrastructure.postgres.models.session import AiSession, AiSessionRepo, Base

__all__ = [
    "AiFileInfo",
    "AiPptInst",
    "AiSession",
    "AiSessionRepo",
    "Base",
    "FileInfoRepo",
    "PptInstRepo",
    "PptStatus",
]
