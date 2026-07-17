import asyncio

import redis.asyncio as aioredis

from app.common.exceptions import InfrastructureError
from app.common.logger import logger
from app.config.settings import get_settings

_redis_client: aioredis.Redis | None = None

CHANNEL_PREFIX = "agent:stop"
LOCK_PREFIX = "agent:lock"


def check_redis():
    """启动时强制检查 Redis 连接，失败则抛出 InfrastructureError。"""
    import redis as sync_redis

    try:
        r = sync_redis.Redis(
            host=get_settings().redis_host,
            port=get_settings().redis_port,
            db=get_settings().redis_db,
            password=get_settings().redis_password or None,
            socket_connect_timeout=3,
        )
        r.ping()
        r.close()
        logger.info(f"Redis 连接成功: {get_settings().redis_host}:{get_settings().redis_port}")
    except Exception as e:
        raise InfrastructureError(f"Redis 连接失败: {e}", service="Redis")


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    _redis_client = aioredis.Redis(
        host=get_settings().redis_host,
        port=get_settings().redis_port,
        db=get_settings().redis_db,
        password=get_settings().redis_password or None,
    )
    await _redis_client.ping()
    return _redis_client


async def publish_stop(conversation_id: str):
    r = await get_redis()
    await r.publish(f"{CHANNEL_PREFIX}:{conversation_id}", "stop")


async def listen_stop(conversation_id: str, cancel_event: asyncio.Event):
    r = await get_redis()
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
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass


async def acquire_lock(conversation_id: str, ttl: int = 300) -> bool:
    r = await get_redis()
    return await r.set(f"{LOCK_PREFIX}:{conversation_id}", "1", nx=True, ex=ttl)


async def release_lock(conversation_id: str):
    r = await get_redis()
    await r.delete(f"{LOCK_PREFIX}:{conversation_id}")
