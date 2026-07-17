"""API 层共享的身份和资源归属检查。"""

from dataclasses import dataclass

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.modules.chat.sessions import store
from app.modules.identity.auth import get_current_user_id


@dataclass(frozen=True, slots=True)
class SessionAccess:
    user_id: str
    owner_id: str | None

    @property
    def exists(self) -> bool:
        return self.owner_id is not None

    @property
    def allowed(self) -> bool:
        return self.exists and self.owner_id == self.user_id


async def inspect_session_access(request: Request, session_id: str) -> SessionAccess:
    return SessionAccess(
        user_id=get_current_user_id(request),
        owner_id=await run_in_threadpool(store.get_session_owner, session_id),
    )
