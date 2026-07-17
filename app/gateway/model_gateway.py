import asyncio
import logging
import time
from typing import Any

from app.gateway.circuit_breaker import CircuitBreaker
from app.gateway.types import HealthRecord

logger = logging.getLogger("apis")


class ModelGateway:
    """模型智能网关。

    职责：注册表管理、健康跟踪、熔断路由、热切换、后台探活。
    实例由应用运行时容器持有，不在模块导入时创建。
    """

    def __init__(self):
        self._models: dict[str, Any] = {}           # name → LLM instance
        self._health: dict[str, HealthRecord] = {}   # name → HealthRecord
        self._breakers: dict[str, CircuitBreaker] = {}  # name → CircuitBreaker
        self._active: str | None = None              # 当前活跃模型名
        self._fallback: list[str] = []               # 降级链
        self._lock = asyncio.Lock()
        self._health_locks: dict[str, asyncio.Lock] = {}
        self._probe_task: asyncio.Task | None = None

    # ---- 注册 ----

    async def register(self, name: str, instance: Any, is_primary: bool = True):
        async with self._lock:
            self._models[name] = instance
            if name not in self._health:
                self._health[name] = HealthRecord()
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker()
            if is_primary:
                self._active = name
            logger.info(f"[Gateway] 注册模型: {name} (primary={is_primary})")

    def set_fallback(self, names: list[str]):
        self._fallback = names
        logger.info(f"[Gateway] 降级链: {names}")

    # ---- 路由 ----

    def get_model(self) -> tuple[str, Any] | None:
        result = self.get_model_chain(None)
        return result[0] if result else None

    def get_model_chain(self, role=None) -> list[tuple[str, Any]]:
        """获取健康排序的模型链 (name, instance)。

        顺序：活跃模型（非熔断）→ 降级链 → 活跃模型（兜底）
        """
        result: list[tuple[str, Any]] = []
        seen: set[str] = set()

        if self._active and self._active in self._models:
            cb = self._breakers.get(self._active)
            if cb is None or not cb.is_open():
                result.append((self._active, self._models[self._active]))
                seen.add(self._active)

        for name in self._fallback:
            if name in seen or name not in self._models:
                continue
            cb = self._breakers.get(name)
            if cb is not None and cb.is_open():
                continue
            result.append((name, self._models[name]))
            seen.add(name)

        return result

    def get_active_name(self) -> str | None:
        return self._active

    # ---- 熔断器代理 ----

    async def before_request(self, name: str) -> bool:
        cb = self._breakers.get(name)
        if cb is None:
            return True
        return await cb.before_request()

    async def record_success(self, name: str, latency_ms: float):
        hr = self._health.get(name)
        if hr is None:
            return
        lock = self._get_health_lock(name)
        async with lock:
            hr.total_requests += 1
            hr.consecutive_errors = 0
            hr.last_success_ts = time.time()
            hr.add_latency(latency_ms)

        cb = self._breakers.get(name)
        if cb is not None:
            await cb.on_success()

    async def record_failure(self, name: str, error_message: str):
        hr = self._health.get(name)
        if hr is None:
            return
        lock = self._get_health_lock(name)
        async with lock:
            hr.total_requests += 1
            hr.total_errors += 1
            hr.consecutive_errors += 1
            hr.last_error_ts = time.time()
            hr.last_error_message = error_message

        cb = self._breakers.get(name)
        if cb is not None:
            await cb.on_failure()

    def _get_health_lock(self, name: str) -> asyncio.Lock:
        if name not in self._health_locks:
            self._health_locks[name] = asyncio.Lock()
        return self._health_locks[name]

    # ---- 热切换 ----

    async def set_active(self, name: str):
        async with self._lock:
            if name not in self._models:
                raise ValueError(f"未知模型: {name}")
            self._active = name
            cb = self._breakers.get(name)
            if cb is not None:
                await cb.reset()
            logger.info(f"[Gateway] 热切换: {name}")

    def get_circuit_state(self, name: str) -> str:
        cb = self._breakers.get(name)
        return cb.state.value if cb else "unknown"

    # ---- 状态查询 ----

    def get_all_status(self) -> dict[str, Any]:
        result = {}
        for name in self._models:
            hr = self._health.get(name)
            cb = self._breakers.get(name)
            result[name] = {
                "health": {
                    "total_requests": hr.total_requests if hr else 0,
                    "total_errors": hr.total_errors if hr else 0,
                    "error_rate": round(hr.error_rate, 4) if hr else 0.0,
                    "consecutive_errors": hr.consecutive_errors if hr else 0,
                    "p50_latency_ms": round(hr.p50_latency_ms, 2) if hr else 0,
                    "p95_latency_ms": round(hr.p95_latency_ms, 2) if hr else 0,
                    "is_healthy": hr.is_healthy if hr else False,
                },
                "circuit": {"state": cb.state.value if cb else "unknown"},
                "active": name == self._active,
            }
        return result

    # ---- 后台探活 ----

    async def start_probe(self, interval_seconds: float = 30.0):
        if self._probe_task is not None:
            return
        from app.gateway.health_probe import HealthProbe
        self._probe_task = asyncio.create_task(
            HealthProbe(self, interval_seconds).run()
        )
        logger.info(f"[Gateway] 健康探活已启动 ({interval_seconds}s)")

    async def stop_probe(self):
        if self._probe_task is not None:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
            self._probe_task = None
