"""Agent 用户反馈写入用例。"""

from datetime import datetime, timezone

from app.storage.db import new_session


def record_feedback(
    session_id: str,
    user_id: str,
    rating: int,
    comment: str,
) -> None:
    db = new_session()
    try:
        db.execute(
            """INSERT INTO agentx_feedback
               (session_id, user_id, rating, comment, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (session_id, user_id, rating, comment, datetime.now(timezone.utc)),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
