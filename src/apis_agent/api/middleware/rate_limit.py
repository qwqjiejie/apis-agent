import asyncio
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.apis_agent.common.logger import logger
from src.apis_agent.common.redis import get_redis
from src.apis_agent.config.settings import get_settings

PREFIX = "ratelimit"
EXEMPT_PATHS = ("/health", "/ping", "/metrics", "/docs", "/openapi.json", "/redoc", "/static")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """滑动窗口限流中间件。

    IP 级 + 会话级双维度限流，Redis sorted set 实现，
    Redis 不可用时自动降级为进程内存计数。
    """

    def __init__(self, app):
        super().__init__(app)
        s = get_settings()
        self._enabled = s.rate_limit_enabled
        self._user_limit = s.rate_limit_user_per_min
        self._ip_limit = s.rate_limit_ip_per_min
        self._window = s.rate_limit_window_sec

        self._redis_client = None
        self._redis_available = False
        self._redis_attempted = False
        self._memory: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

        logger.info(
            f"[RateLimit] user={self._user_limit}/min ip={self._ip_limit}/min "
            f"window={self._window}s enabled={self._enabled}"
        )

    async def _ensure_redis(self):
        if self._redis_attempted:
            return
        self._redis_attempted = True
        try:
            self._redis_client = await get_redis()
            self._redis_available = True
        except Exception:
            pass

    async def dispatch(self, request: Request, call_next):
        if not self._enabled or self._is_exempt(request.url.path):
            return await call_next(request)

        await self._ensure_redis()

        client_ip = request.client.host if request.client else "unknown"

        if not await self._check(f"ip:{client_ip}", self._ip_limit):
            logger.info(f"[RateLimit] IP={client_ip} 被限制")
            return self._build_429()

        session_id = request.headers.get("X-Session-Id", "")
        if session_id:
            if not await self._check(f"session:{session_id}", self._user_limit):
                logger.info(f"[RateLimit] session={session_id[:12]} 被限制")
                return self._build_429()

        return await call_next(request)

    # ---- 核心算法 ----

    async def _check(self, key: str, limit: int) -> bool:
        if self._redis_available:
            try:
                return await self._redis_check(key, limit)
            except Exception:
                self._redis_available = False

        async with self._lock:
            return self._memory_check(key, limit)

    async def _redis_check(self, key: str, limit: int) -> bool:
        now_ns = time.monotonic_ns()
        window_ns = self._window * 1_000_000_000
        cutoff = now_ns - window_ns
        full_key = f"{PREFIX}:{key}"

        pipe = self._redis_client.pipeline()
        pipe.zremrangebyscore(full_key, 0, cutoff)
        pipe.zadd(full_key, {str(now_ns): now_ns})
        pipe.zcard(full_key)
        pipe.expire(full_key, int(self._window * 2))
        _, _, count, _ = await pipe.execute()

        return count <= limit

    def _memory_check(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - self._window

        if key not in self._memory:
            self._memory[key] = []

        self._memory[key] = [t for t in self._memory[key] if t > cutoff]

        if len(self._memory[key]) >= limit:
            return False

        self._memory[key].append(now)
        return True

    # ---- Helpers ----

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return any(path.startswith(p) for p in EXEMPT_PATHS)

    @staticmethod
    def _build_429() -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"code": 429, "data": None, "message": "请求过于频繁，请稍后重试"},
            headers={"Retry-After": "30"},
        )
