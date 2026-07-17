"""注册、登录和匿名数据迁移用例。"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.postgres.database import session_scope
from app.modules.identity.auth import hash_password, verify_password


class IdentityServiceError(Exception):
    """身份用例错误基类。"""


class UsernameAlreadyExistsError(IdentityServiceError):
    pass


class InvalidCredentialsError(IdentityServiceError):
    pass


@dataclass(frozen=True, slots=True)
class UserIdentity:
    user_id: str
    username: str


class IdentityService:
    """同步业务数据库用例；异步 API 应在线程池中调用。"""

    def register(self, username: str, password: str) -> UserIdentity:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        try:
            with session_scope() as db:
                existing = db.execute(
                    text("SELECT 1 FROM agentx_user WHERE username = :username"),
                    {"username": username},
                ).first()
                if existing:
                    raise UsernameAlreadyExistsError(username)
                db.execute(
                    text(
                        """INSERT INTO agentx_user
                           (user_id, username, password_hash)
                           VALUES (:user_id, :username, :password_hash)"""
                    ),
                    {
                        "user_id": user_id,
                        "username": username,
                        "password_hash": hash_password(password),
                    },
                )
        except IntegrityError as exc:
            raise UsernameAlreadyExistsError(username) from exc
        return UserIdentity(user_id=user_id, username=username)

    def login(self, username: str, password: str) -> UserIdentity:
        with session_scope() as db:
            row = db.execute(
                text(
                    """SELECT user_id, username, password_hash
                       FROM agentx_user WHERE username = :username"""
                ),
                {"username": username},
            ).first()
        if not row or not verify_password(password, row.password_hash):
            raise InvalidCredentialsError(username)
        return UserIdentity(user_id=row.user_id, username=row.username)

    def sync_anonymous_data(self, anonymous_id: str, user_id: str) -> int:
        source_user_id = f"anon_{anonymous_id}"
        total = 0
        with session_scope() as db:
            for table in ("agentx_session", "agentx_file"):
                result = db.execute(
                    text(
                        f"UPDATE {table} SET user_id = :user_id "
                        "WHERE user_id = :anonymous_id RETURNING 1"
                    ),
                    {"user_id": user_id, "anonymous_id": source_user_id},
                )
                total += len(result.all())
        return total


identity_service = IdentityService()
