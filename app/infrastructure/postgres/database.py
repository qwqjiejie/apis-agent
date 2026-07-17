"""PostgreSQL 业务数据库连接与同步会话生命周期。"""

from contextlib import contextmanager
from typing import Iterator
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.common.exceptions import DatabaseError
from app.common.logger import logger
from app.config.settings import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def _build_pg_url() -> str:
    s = get_settings()
    return (
        f"postgresql+psycopg2://{quote_plus(s.pg_user)}:{quote_plus(s.pg_password)}"
        f"@{s.pg_host}:{s.pg_port}/{s.pg_db}"
    )


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            _build_pg_url(),
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=s.pg_pool_size,
            max_overflow=s.pg_max_overflow,
            pool_timeout=s.external_connect_timeout_seconds,
            connect_args={
                "connect_timeout": max(1, int(s.external_connect_timeout_seconds)),
            },
        )
    return _engine


def check_db():
    """启动时强制检查 PostgreSQL 连接，失败则抛出 DatabaseError。"""
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        s = get_settings()
        logger.info(f"PostgreSQL 连接成功: {s.pg_host}:{s.pg_port}/{s.pg_db}")
    except Exception as e:
        raise DatabaseError(f"PostgreSQL 连接失败: {e}")


def new_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供单次用例级事务，保证提交、回滚和连接归还。"""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_database() -> None:
    """关闭连接池；仅由应用生命周期结束阶段调用。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
