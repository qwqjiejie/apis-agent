"""对话反馈写入用例。"""

from datetime import datetime, timezone

from sqlalchemy import text

from app.infrastructure.postgres.database import session_scope


def record_feedback(
    session_id: str,
    user_id: str,
    rating: int,
    comment: str,
) -> None:
    with session_scope() as db:
        db.execute(
            text(
                """INSERT INTO agentx_feedback
                   (session_id, user_id, rating, comment, created_at)
                   VALUES (:session_id, :user_id, :rating, :comment, :created_at)"""
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "rating": rating,
                "comment": comment,
                "created_at": datetime.now(timezone.utc),
            },
        )
