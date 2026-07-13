from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.config.settings import settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_pass}@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}?charset=utf8mb4"
        _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _engine


def new_session() -> Session | None:
    global _SessionLocal
    try:
        if _SessionLocal is None:
            _SessionLocal = sessionmaker(bind=get_engine())
        return _SessionLocal()
    except Exception:
        return None
