from datetime import datetime, timezone

from sqlalchemy import BigInteger, String, Text, DateTime, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.ai_session import Base
from app.storage.base import BaseRepository


class AiFileInfo(Base):
    __tablename__ = "agentx_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(50))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    minio_path: Mapped[str | None] = mapped_column(String(1000))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    embed: Mapped[bool] = mapped_column(Boolean, default=False)
    error_msg: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(default=0)
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("uk_file_file_id", "file_id", unique=True),
        Index("idx_file_session_id", "session_id"),
        Index("idx_file_status", "status"),
        Index("idx_file_user_id", "user_id", "created_at"),
    )


class FileInfoRepo(BaseRepository[AiFileInfo]):
    model = AiFileInfo

    def find_by_file_id(self, file_id: str) -> AiFileInfo | None:
        return self.find_one(AiFileInfo.file_id == file_id)

    def delete_by_file_id(self, file_id: str) -> int:
        return self.delete_by(AiFileInfo.file_id == file_id)
