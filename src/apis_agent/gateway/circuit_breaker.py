import asyncio
import logging
import time

from src.apis_agent.gateway.types import CircuitState

logger = logging.getLogger("apis")


class CircuitBreaker:
    """标准三态熔断器: CLOSED → OPEN → HALF_OPEN → CLOSED。

    CLOSED: 正常放行，连续失败 ≥ threshold 触发熔断
    OPEN: 拒绝请求（快速失败），冷却 cooldown_seconds 后进入 HALF_OPEN
    HALF_OPEN: 允许少量探测请求，成功则恢复 CLOSED，失败则重新 OPEN
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0,
                 half_open_max_requests: int = 1):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_requests = half_open_max_requests

        self.state = CircuitState.CLOSED
        self._state_changed_at: float = time.time()
        self._half_open_count: int = 0
        self._failure_count: int = 0
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    async def before_request(self) -> bool:
        """每次模型调用前检查。返回 True 放行，False 拒绝。"""
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                elapsed = time.time() - self._state_changed_at
                if elapsed >= self.cooldown_seconds:
                    self.state = CircuitState.HALF_OPEN
                    self._state_changed_at = time.time()
                    self._half_open_count = 0
                    logger.info(f"[CircuitBreaker] HALF_OPEN (冷却 {elapsed:.1f}s)")
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_count < self.half_open_max_requests:
                    self._half_open_count += 1
                    return True
                return False

            return False

    async def on_success(self):
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                self._failure_count = 0
            elif self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self._state_changed_at = time.time()
                self._failure_count = 0
                logger.info("[CircuitBreaker] 恢复: HALF_OPEN → CLOSED")

    async def on_failure(self):
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    self._state_changed_at = time.time()
                    logger.warning(
                        f"[CircuitBreaker] 熔断: CLOSED → OPEN (连续失败 {self._failure_count})"
                    )
            elif self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self._state_changed_at = time.time()
                logger.warning("[CircuitBreaker] 探测失败: HALF_OPEN → OPEN")

    async def trip(self):
        async with self._lock:
            self.state = CircuitState.OPEN
            self._state_changed_at = time.time()

    async def reset(self):
        async with self._lock:
            self.state = CircuitState.CLOSED
            self._state_changed_at = time.time()
            self._half_open_count = 0
            self._failure_count = 0
