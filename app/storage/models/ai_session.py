from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, Text, DateTime, Index, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.storage.base import BaseRepository


class Base(DeclarativeBase):
    pass


class AiSession(Base):
    __tablename__ = "agentx_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    thinking: Mapped[str | None] = mapped_column(Text)
    tools: Mapped[str | None] = mapped_column(String(500))
    reference: Mapped[str | None] = mapped_column(Text)
    recommend: Mapped[str | None] = mapped_column(String(1000))
    agent_type: Mapped[str | None] = mapped_column(String(64))
    fileid: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_session_session_id", "session_id"),
        Index("idx_session_user_id", "user_id"),
        Index("idx_session_created_at", "created_at"),
    )


class AiSessionRepo(BaseRepository[AiSession]):
    model = AiSession

    def find_by_session_id(self, session_id: str, limit: int = 20) -> list[AiSession]:
        return self.find_by(
            AiSession.session_id == session_id,
            order_by=AiSession.created_at.asc(),
        )[-limit:]

    def list_distinct_sessions(self, page: int = 1, size: int = 100, user_id: str = "") -> tuple[list[AiSession], int]:
        sub = (
            select(func.min(AiSession.id).label("first_id"))
            .group_by(AiSession.session_id)
            .subquery()
        )
        where = AiSession.id.in_(select(sub.c.first_id))
        if user_id:
            where_uid = AiSession.user_id == user_id
            total = self.count(where_uid)
            rows, _ = self.paginate(page, size, where_uid & where, order_by=AiSession.created_at.desc())
        else:
            total = self.count()
            rows, _ = self.paginate(page, size, where, order_by=AiSession.created_at.desc())
        return rows, total

    def delete_by_session_id(self, session_id: str) -> int:
        return self.delete_by(AiSession.session_id == session_id)
