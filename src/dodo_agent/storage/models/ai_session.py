from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, Index, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.dodo_agent.storage.base import BaseRepository


class Base(DeclarativeBase):
    pass


class AiSession(Base):
    __tablename__ = "ai_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    tools: Mapped[str | None] = mapped_column(String(1024))
    first_response_time: Mapped[int | None] = mapped_column(BigInteger)
    total_response_time: Mapped[int | None] = mapped_column(BigInteger)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    reference: Mapped[str | None] = mapped_column(Text)
    agent_type: Mapped[str | None] = mapped_column(String(255))
    thinking: Mapped[str | None] = mapped_column(Text)
    fileid: Mapped[str | None] = mapped_column(String(255))
    recommend: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        Index("idx_session_id", "session_id"),
        Index("idx_create_time", "create_time"),
    )


class AiSessionRepo(BaseRepository[AiSession]):
    model = AiSession

    # ---- 项目自定义方法 ----

    def find_by_session_id(self, session_id: str, limit: int = 20) -> list[AiSession]:
        return self.find_by(
            AiSession.session_id == session_id,
            order_by=AiSession.create_time.asc(),
        )[-limit:]

    def list_distinct_sessions(self, page: int = 1, size: int = 100) -> tuple[list[AiSession], int]:
        sub = (
            select(func.min(AiSession.id).label("first_id"))
            .group_by(AiSession.session_id)
            .subquery()
        )
        total = self.count()
        rows, _ = self.paginate(
            page, size,
            AiSession.id.in_(select(sub.c.first_id)),
            order_by=AiSession.create_time.desc(),
        )
        return rows, total

    def delete_by_session_id(self, session_id: str) -> int:
        return self.delete_by(AiSession.session_id == session_id)
