from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.apis_agent.storage.models.ai_session import Base
from src.apis_agent.storage.base import BaseRepository


class AiFileInfo(Base):
    __tablename__ = "ai_file_info"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(50))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    minio_path: Mapped[str | None] = mapped_column(String(1000))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    conversation_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(50), default="PENDING")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    embed: Mapped[int | None] = mapped_column()

    __table_args__ = (
        Index("uk_file_id", "file_id", unique=True),
        Index("idx_conversation_id", "conversation_id"),
    )


class FileInfoRepo(BaseRepository[AiFileInfo]):
    model = AiFileInfo

    def find_by_file_id(self, file_id: str) -> AiFileInfo | None:
        return self.find_one(AiFileInfo.file_id == file_id)

    def delete_by_file_id(self, file_id: str) -> int:
        return self.delete_by(AiFileInfo.file_id == file_id)
