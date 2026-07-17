from datetime import datetime, timezone

from app.storage import BaseStore
from app.storage.models.ai_session import AiSession, AiSessionRepo


class Store(BaseStore):

    def _repo(self) -> AiSessionRepo:
        return AiSessionRepo()

    def save_message(self, session_id: str, question: str, answer: str, *,
                     thinking: str = "", reference: str = "", recommend: str = "",
                     tools: str = "", agent_type: str = "chat", fileid: str = "",
                     user_id: str = "") -> None:
        now = datetime.now(timezone.utc)
        uid = user_id or ""
        self._repo().save(AiSession(
            session_id=session_id,
            user_id=uid,
            question=question, answer=answer,
            thinking=thinking, reference=reference, recommend=recommend,
            tools=tools, agent_type=agent_type, fileid=fileid,
            created_at=now, updated_at=now,
        ))

    def load_history(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self._repo().find_by_session_id(session_id, limit)
        return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]

    def list_sessions(self, page: int = 1, size: int = 100, user_id: str = "") -> tuple[list[dict], int]:
        rows, total = self._repo().list_distinct_sessions(page, size, user_id=user_id)
        return [
            {"conversationId": r.session_id, "question": r.question or "",
             "agentType": r.agent_type or "chat", "fileid": r.fileid or ""}
            for r in rows
        ], total

    def get_session(self, session_id: str) -> dict | None:
        rows = self._repo().find_by_session_id(session_id)
        if not rows:
            return None
        return {
            "conversationId": rows[0].session_id,
            "question": rows[0].question or "",
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

    def delete_session(self, session_id: str) -> bool:
        from app.storage.models.ai_ppt_inst import PptInstRepo
        ppt_repo = PptInstRepo()
        ppt_inst = ppt_repo.find_by_session_id(session_id)
        if ppt_inst:
            ppt_repo.delete(ppt_inst)
        return self._repo().delete_by_session_id(session_id) > 0

    def is_available(self) -> bool:
        return True


store = Store()
