from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.apis_agent.common.exceptions import DatabaseError
from src.apis_agent.common.logger import logger
from src.apis_agent.config.settings import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = f"mysql+pymysql://{get_settings().mysql_user}:{get_settings().mysql_pass}@{get_settings().mysql_host}:{get_settings().mysql_port}/{get_settings().mysql_db}?charset=utf8mb4"
        _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _engine


def check_db():
    """启动时强制检查 MySQL 连接，失败则抛出 DatabaseError。"""
    try:
        s = new_session()
        s.execute(text("SELECT 1"))
        s.close()
        logger.info(f"MySQL 连接成功: {get_settings().mysql_host}:{get_settings().mysql_port}/{get_settings().mysql_db}")
    except Exception as e:
        raise DatabaseError(f"MySQL 连接失败: {e}")


def new_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()
