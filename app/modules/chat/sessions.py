"""对话会话仓储用例。"""

from datetime import datetime, timezone

from sqlalchemy import text

from app.infrastructure.postgres.database import session_scope
from app.modules.chat.ports import BaseStore
from app.infrastructure.postgres.models.session import AiSession, AiSessionRepo


class Store(BaseStore):

    def _repo(self) -> AiSessionRepo:
        return AiSessionRepo()

    def save_message(self, session_id: str, question: str, answer: str, *,
                     thinking: str = "", reference: str = "", recommend: str = "",
                     tools: str = "", agent_type: str = "chat", fileid: str = "",
                     user_id: str = "") -> None:
        now = datetime.now(timezone.utc)
        uid = user_id or ""
        with self._repo() as repo:
            repo.save(AiSession(
                session_id=session_id,
                user_id=uid,
                question=question, answer=answer,
                thinking=thinking, reference=reference, recommend=recommend,
                tools=tools, agent_type=agent_type, fileid=fileid,
                created_at=now, updated_at=now,
            ))

    def load_history(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._repo() as repo:
            rows = repo.find_by_session_id(session_id, limit)
        return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]

    def list_sessions(self, page: int = 1, size: int = 100, user_id: str = "") -> tuple[list[dict], int]:
        with self._repo() as repo:
            rows, total = repo.list_distinct_sessions(page, size, user_id=user_id)
        return [
            {"conversationId": r.session_id, "question": r.title or r.question or "",
             "agentType": r.agent_type or "chat", "fileid": r.fileid or ""}
            for r in rows
        ], total

    def get_session(self, session_id: str) -> dict | None:
        with self._repo() as repo:
            rows = repo.find_by_session_id(session_id)
        if not rows:
            return None
        return {
            "conversationId": rows[0].session_id,
            "question": rows[0].title or rows[0].question or "",
            "agentType": rows[0].agent_type or "chat",
            "fileid": rows[0].fileid or "",
            "messages": [
                {"id": str(r.id), "role": "user", "question": r.question or "",
                 "answer": r.answer or "", "thinking": r.thinking or "",
                 "reference": r.reference or "",
                 "createTime": str(r.created_at) if r.created_at else ""}
                for r in rows
            ],
        }

    def get_session_owner(self, session_id: str) -> str | None:
        """返回会话归属；None 表示会话不存在，空字符串表示历史脏数据。"""
        with self._repo() as repo:
            rows = repo.find_by_session_id(session_id, limit=1)
        if not rows:
            return None
        return rows[-1].user_id or ""

    def touch_last_active(self, session_id: str):
        """更新会话最后活跃时间（当前表结构为更新该 session 所有行的 updated_at）。"""
        with session_scope() as db:
            db.execute(
                text(
                    "UPDATE agentx_session "
                    "SET updated_at = :updated_at WHERE session_id = :session_id"
                ),
                {
                    "updated_at": datetime.now(timezone.utc),
                    "session_id": session_id,
                },
            )

    def delete_session(self, session_id: str) -> bool:
        from app.infrastructure.postgres.models.ppt import PptInstRepo
        with session_scope() as db:
            ppt_repo = PptInstRepo(session=db)
            ppt_inst = ppt_repo.find_by_session_id(session_id)
            if ppt_inst:
                ppt_repo.delete(ppt_inst)
            return AiSessionRepo(session=db).delete_by_session_id(session_id) > 0

    def is_available(self) -> bool:
        return True


store = Store()
