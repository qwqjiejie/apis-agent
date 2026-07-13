import time
from datetime import datetime

from src.storage import BaseStore
from src.storage.db import new_session
from src.storage.models.ai_session import AiSession, AiSessionRepo


class Store(BaseStore):
    """先试 MySQL，连不上自动回退内存存储"""

    def __init__(self):
        self._fallback: list[dict] = []
        self._db_ok = new_session() is not None

    def _repo(self) -> AiSessionRepo:
        return AiSessionRepo()

    # ---- save ----

    def save_message(self, session_id: str, question: str, answer: str, *, thinking: str = "",
                     reference: str = "", recommend: str = "", tools: str = "", agent_type: str = "chat",
                     fileid: str = "") -> None:
        if self._db_ok:
            self._repo().save(AiSession(
                session_id=session_id, question=question, answer=answer,
                thinking=thinking, reference=reference, recommend=recommend,
                tools=tools, agent_type=agent_type, fileid=fileid,
                create_time=datetime.now(), update_time=datetime.now(),
            ))
        else:
            self._fallback.append({
                "session_id": session_id, "question": question, "answer": answer,
                "thinking": thinking, "reference": reference, "recommend": recommend,
                "tools": tools, "agent_type": agent_type, "fileid": fileid,
                "create_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

    # ---- load ----

    def load_history(self, session_id: str, limit: int = 20) -> list[dict]:
        if self._db_ok:
            rows = self._repo().find_by_session_id(session_id, limit)
            return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]

        records = [m for m in self._fallback if m["session_id"] == session_id]
        return records[-limit:]

    # ---- list ----

    def list_sessions(self, page: int = 1, size: int = 100) -> tuple[list[dict], int]:
        if self._db_ok:
            rows, total = self._repo().list_distinct_sessions(page, size)
            return [
                {"conversationId": r.session_id, "question": r.question or "",
                 "agentType": r.agent_type or "chat", "fileid": r.fileid or ""}
                for r in rows
            ], total

        seen = {}
        for m in reversed(self._fallback):
            sid = m["session_id"]
            if sid not in seen:
                seen[sid] = {"conversationId": sid, "question": m["question"],
                             "agentType": m.get("agent_type", "chat"), "fileid": m.get("fileid", "")}
        records = list(seen.values())
        total = len(records)
        start = (page - 1) * size
        return records[start:start + size], total

    # ---- detail ----

    def get_session(self, session_id: str) -> dict | None:
        if self._db_ok:
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
                     "createTime": str(r.create_time) if r.create_time else ""}
                    for r in rows
                ],
            }

        records = [m for m in self._fallback if m["session_id"] == session_id]
        if not records:
            return None
        first = records[0]
        return {
            "conversationId": session_id, "question": first["question"],
            "agentType": first.get("agent_type", "chat"), "fileid": first.get("fileid", ""),
            "messages": [
                {"id": "", "role": "user", "question": m["question"],
                 "answer": m["answer"], "thinking": m.get("thinking", ""),
                 "reference": m.get("reference", ""),
                 "createTime": m.get("create_time", "")}
                for m in records
            ],
        }

    # ---- delete ----

    def delete_session(self, session_id: str) -> bool:
        if self._db_ok:
            return self._repo().delete_by_session_id(session_id) > 0

        before = len(self._fallback)
        self._fallback = [m for m in self._fallback if m["session_id"] != session_id]
        return len(self._fallback) < before

    def is_available(self) -> bool:
        return True


store = Store()
