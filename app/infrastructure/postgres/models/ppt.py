from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import BigInteger, String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.postgres.models.session import Base
from app.infrastructure.postgres.repository import BaseRepository


class PptStatus(str, Enum):
    INIT = "INIT"
    SCHEMA = "SCHEMA"
    OUTLINE = "OUTLINE"
    CONTENT = "CONTENT"
    RENDER = "RENDER"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AiPptInst(Base):
    __tablename__ = "agentx_ppt_inst"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), default="")
    template_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(32), default=PptStatus.INIT.value)
    query: Mapped[str | None] = mapped_column(Text)
    requirement: Mapped[str | None] = mapped_column(Text)
    search_info: Mapped[str | None] = mapped_column(Text)
    outline: Mapped[str | None] = mapped_column(Text)
    ppt_schema: Mapped[dict | None] = mapped_column(JSONB)
    file_url: Mapped[str | None] = mapped_column(String(1000))
    error_msg: Mapped[str | None] = mapped_column(Text)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("uk_ppt_inst_id", "inst_id", unique=True),
        Index("idx_ppt_session_id", "session_id"),
        Index("idx_ppt_status", "status"),
    )


class PptInstRepo(BaseRepository[AiPptInst]):
    model = AiPptInst

    def find_by_inst_id(self, inst_id: str) -> AiPptInst | None:
        return self.find_one(AiPptInst.inst_id == inst_id)

    def find_by_session_id(self, session_id: str) -> AiPptInst | None:
        return self.find_one(AiPptInst.session_id == session_id)

    def update_status(self, inst: AiPptInst, status: PptStatus, **fields):
        inst.status = status.value if isinstance(status, PptStatus) else status
        inst.updated_at = datetime.now(timezone.utc)
        for k, v in fields.items():
            setattr(inst, k, v)
        self._s.merge(inst)
        self._s.flush()
        if self._own_session:
            self._s.commit()
        return inst
