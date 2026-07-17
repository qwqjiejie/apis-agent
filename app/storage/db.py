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
        f"postgresql+psycopg2://{s.pg_user}:{s.pg_password}"
        f"@{s.pg_host}:{s.pg_port}/{s.pg_db}"
    )


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_build_pg_url(), pool_pre_ping=True, pool_recycle=3600)
    return _engine


def check_db():
    """启动时强制检查 PostgreSQL 连接，失败则抛出 DatabaseError。"""
    try:
        s = new_session()
        s.execute(text("SELECT 1"))
        s.close()
        s = get_settings()
        logger.info(f"PostgreSQL 连接成功: {s.pg_host}:{s.pg_port}/{s.pg_db}")
    except Exception as e:
        raise DatabaseError(f"PostgreSQL 连接失败: {e}")


def new_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()
