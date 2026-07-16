import asyncio
import logging
import time

logger = logging.getLogger("apis")

PROBE_TIMEOUT = 10.0


class HealthProbe:
    """后台定期对所有注册模型发送 ping，自动更新熔断器状态。"""

    def __init__(self, gateway, interval_seconds: float = 30.0):
        self._gateway = gateway
        self._interval = interval_seconds

    async def run(self):
        while True:
            for _ in range(int(self._interval)):
                await asyncio.sleep(1)
            await self._probe_all()

    async def _probe_all(self):
        names = list(self._gateway._models.keys())
        if not names:
            return
        tasks = [self._probe_one(name) for name in names]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_one(self, name: str):
        model = self._gateway._models.get(name)
        if model is None:
            return
        try:
            start = time.monotonic()
            if hasattr(model, "ainvoke"):
                await asyncio.wait_for(
                    model.ainvoke([{"role": "user", "content": "ping"}]),
                    timeout=PROBE_TIMEOUT,
                )
            elif hasattr(model, "acomplete"):
                await asyncio.wait_for(
                    model.acomplete("ping"),
                    timeout=PROBE_TIMEOUT,
                )
            else:
                return
            latency = (time.monotonic() - start) * 1000
            await self._gateway.record_success(name, latency)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._gateway.record_failure(name, str(e))
