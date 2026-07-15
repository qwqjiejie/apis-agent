from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from src.dodo_agent.common.exceptions import DatabaseError
from src.dodo_agent.common.logger import logger
from src.dodo_agent.config.settings import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None
_db_available: bool | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = f"mysql+pymysql://{get_settings().mysql_user}:{get_settings().mysql_pass}@{get_settings().mysql_host}:{get_settings().mysql_port}/{get_settings().mysql_db}?charset=utf8mb4"
        _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _engine


def is_db_available() -> bool:
    """检查 MySQL 是否可用，结果缓存避免重复检测。"""
    global _db_available
    if _db_available is not None:
        return _db_available
    try:
        s = new_session()
        if s is None:
            raise ConnectionError("无法创建数据库会话")
        from sqlalchemy import text
        s.execute(text("SELECT 1"))
        s.close()
        _db_available = True
        logger.info(f"MySQL 连接成功: {get_settings().mysql_host}:{get_settings().mysql_port}")
    except Exception as e:
        _db_available = False
        logger.warning(f"MySQL 不可用: {e}")
    return _db_available


def new_session() -> Session | None:
    """创建数据库会话。连接失败时返回 None，调用方据此降级。

    注意：此方法仅用于判断可用性，不抛出异常。
    业务代码中通过 is_db_available() 预检，或直接处理 None 返回值。
    """
    global _SessionLocal
    try:
        if _SessionLocal is None:
            _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
        return _SessionLocal()
    except Exception as e:
        logger.warning(f"MySQL 会话创建失败: {e}")
        return None
