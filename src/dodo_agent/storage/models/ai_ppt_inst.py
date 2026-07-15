from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.dodo_agent.storage.models.ai_session import Base
from src.dodo_agent.storage.base import BaseRepository


class PptStatus(str, Enum):
    INIT = "INIT"
    SCHEMA = "SCHEMA"
    OUTLINE = "OUTLINE"
    CONTENT = "CONTENT"
    RENDER = "RENDER"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AiPptInst(Base):
    __tablename__ = "ai_ppt_inst"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    template_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(32), default=PptStatus.INIT)
    query: Mapped[str | None] = mapped_column(Text)
    requirement: Mapped[str | None] = mapped_column(Text)
    search_info: Mapped[str | None] = mapped_column(Text)
    outline: Mapped[str | None] = mapped_column(Text)
    ppt_schema: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(String(1000))
    error_msg: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_conversation_id", "conversation_id"),
        Index("idx_status", "status"),
        Index("idx_template_code", "template_code"),
    )


class PptInstRepo(BaseRepository[AiPptInst]):
    model = AiPptInst

    def find_by_conversation_id(self, conversation_id: str) -> AiPptInst | None:
        return self.find_one(AiPptInst.conversation_id == conversation_id)

    def update_status(self, inst: AiPptInst, status: PptStatus, **fields):
        inst.status = status
        inst.update_time = datetime.now()
        for k, v in fields.items():
            setattr(inst, k, v)
        self._s.merge(inst)
        self._s.flush()
        if self._own_session:
            self._s.commit()
        return inst
