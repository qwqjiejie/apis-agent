import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class HealthRecord:
    total_requests: int = 0
    total_errors: int = 0
    consecutive_errors: int = 0
    last_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    last_success_ts: float = 0.0
    last_error_ts: float = 0.0
    last_error_message: str = ""
    latency_samples: list[float] = field(default_factory=list)
    MAX_SAMPLES: int = field(default=100, init=False)

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    @property
    def is_healthy(self) -> bool:
        return self.last_success_ts > self.last_error_ts

    def add_latency(self, ms: float):
        self.latency_samples.append(ms)
        if len(self.latency_samples) > self.MAX_SAMPLES:
            self.latency_samples.pop(0)
        self.last_latency_ms = ms
        self.p50_latency_ms = _percentile(self.latency_samples, 50)
        self.p95_latency_ms = _percentile(self.latency_samples, 95)


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(s):
        return s[f] + c * (s[f + 1] - s[f])
    return s[f]
