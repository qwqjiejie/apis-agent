import asyncio

import redis.asyncio as aioredis

from src.dodo_agent.common.logger import logger
from src.dodo_agent.config.settings import settings

_redis_client: aioredis.Redis | None = None
_redis_available: bool | None = None

CHANNEL_PREFIX = "agent:stop"


async def get_redis() -> aioredis.Redis | None:
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await _redis_client.ping()
        _redis_available = True
        logger.info(f"Redis 连接成功: {settings.redis_host}:{settings.redis_port}")
        return _redis_client
    except Exception as e:
        _redis_available = False
        _redis_client = None
        logger.warning(f"Redis 不可用，降级为本地停止模式: {e}")
        return None


async def publish_stop(conversation_id: str) -> bool:
    r = await get_redis()
    if r is None:
        return False
    try:
        await r.publish(f"{CHANNEL_PREFIX}:{conversation_id}", "stop")
        return True
    except Exception as e:
        logger.warning(f"Redis 发布停止消息失败: {e}")
        return False


async def listen_stop(conversation_id: str, cancel_event: asyncio.Event) -> None:
    r = await get_redis()
    if r is None:
        return
    channel = f"{CHANNEL_PREFIX}:{conversation_id}"
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                logger.info(f"收到 Redis 跨实例停止信号: {conversation_id}")
                cancel_event.set()
                break
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Redis 停止监听异常: {e}")
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
