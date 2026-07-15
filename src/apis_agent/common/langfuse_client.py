import logging

from src.apis_agent.config.settings import get_settings

logger = logging.getLogger("apis")

_langfuse = None


def _build_langfuse():
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        )
    except Exception as e:
        logger.warning(f"Langfuse 初始化失败: {e}")
        return None


def get_langfuse():
    global _langfuse
    if _langfuse is None:
        _langfuse = _build_langfuse()
    return _langfuse


def langfuse_enabled() -> bool:
    return get_langfuse() is not None


def get_langfuse_callback():
    """返回 LangChain 兼容的 Langfuse CallbackHandler，未配置时返回 None。"""
    client = get_langfuse()
    if client is None:
        return None
    from langfuse.langchain import CallbackHandler
    return CallbackHandler()


def observe(**kwargs):
    """Langfuse @observe 装饰器的无感包装，未配置时原样返回函数。"""
    client = get_langfuse()
    if client is None:
        return lambda fn: fn
    from langfuse.decorators import observe as langfuse_observe
    return langfuse_observe(**kwargs)


def flush_langfuse():
    """应用关闭时 flush 剩余数据。"""
    client = get_langfuse()
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass
