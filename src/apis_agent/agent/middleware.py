"""Agent 中间件 — 工具调用重试、限流、模型重试。

基于包装器模式实现，拦截工具调用和模型调用。
LangGraph create_react_agent 不提供原生中间件接口，
因此通过包装工具函数和模型实例来实现等效行为。
"""

import asyncio
import logging
import time
from collections.abc import Callable

logger = logging.getLogger("apis")


# ═══════════════════════════════════════════
# ToolRetry — 工具调用指数退避重试
# ═══════════════════════════════════════════

def wrap_tool_with_retry(tool, max_retries: int = 3, backoff_factor: float = 2.0, initial_delay: float = 1.0):
    """包装工具函数，失败时指数退避重试。

    Args:
        tool: 原始 LangChain Tool 对象
        max_retries: 最大重试次数
        backoff_factor: 退避因子
        initial_delay: 初始延迟（秒）
    """
    original_func = tool.func
    original_coroutine = tool.coroutine

    async def _retry_wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if original_coroutine is not None:
                    return await original_coroutine(*args, **kwargs)
                if asyncio.iscoroutinefunction(original_func):
                    return await original_func(*args, **kwargs)
                return original_func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"[ToolRetry] {tool.name} 第 {attempt + 1} 次失败: {e}，{delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    tool.coroutine = _retry_wrapper
    return tool


# ═══════════════════════════════════════════
# ToolCallLimit — 工具调用次数限制
# ═══════════════════════════════════════════

class ToolCallLimiter:
    """工具调用次数限制器。支持全局限制 + 按工具名限制。"""

    def __init__(self, run_limit: int = 50, thread_limit: int = 50):
        self.run_limit = run_limit
        self.thread_limit = thread_limit
        self._run_counts: dict[str, int] = {}
        self._thread_counts: dict[str, int] = {}

    def reset_run(self):
        self._run_counts.clear()

    def reset_thread(self, thread_id: str):
        self._thread_counts.pop(thread_id, None)

    def _check_and_increment(self, tool_name: str, thread_id: str = "") -> bool:
        run_count = self._run_counts.get(tool_name, 0)
        if run_count >= self.run_limit:
            return False
        if thread_id:
            thread_count = self._thread_counts.get(f"{thread_id}:{tool_name}", 0)
            if thread_count >= self.thread_limit:
                return False
            self._thread_counts[f"{thread_id}:{tool_name}"] = thread_count + 1
        self._run_counts[tool_name] = run_count + 1
        return True


def wrap_tool_with_limit(tool, limiter: ToolCallLimiter):
    """包装工具函数，限制调用次数。"""
    original_coroutine = tool.coroutine

    async def _limit_wrapper(*args, **kwargs):
        if not limiter._check_and_increment(tool.name):
            raise RuntimeError(f"工具 {tool.name} 调用次数已达上限，已拒绝")
        if original_coroutine is not None:
            return await original_coroutine(*args, **kwargs)
        original_func = tool.func
        if asyncio.iscoroutinefunction(original_func):
            return await original_func(*args, **kwargs)
        return original_func(*args, **kwargs)

    tool.coroutine = _limit_wrapper
    return tool


# ═══════════════════════════════════════════
# ModelRetry — 模型调用快速重试
# ═══════════════════════════════════════════

class ModelRetryWrapper:
    """模型调用快速兜底重试。1 次重试，短延迟。"""

    def __init__(self, llm, max_retries: int = 1, initial_delay: float = 0.5):
        self._llm = llm
        self._max_retries = max_retries
        self._initial_delay = initial_delay

    async def ainvoke(self, *args, **kwargs):
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._llm.ainvoke(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    logger.warning(f"[ModelRetry] 第 {attempt + 1} 次失败: {e}，{self._initial_delay}s 后重试")
                    await asyncio.sleep(self._initial_delay)
        raise last_error  # type: ignore[misc]

    async def astream(self, *args, **kwargs):
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                async for chunk in self._llm.astream(*args, **kwargs):
                    yield chunk
                return
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    logger.warning(f"[ModelRetry] 流式第 {attempt + 1} 次失败: {e}，{self._initial_delay}s 后重试")
                    await asyncio.sleep(self._initial_delay)
        raise last_error  # type: ignore[misc]

    @property
    def _llm_type(self) -> str:
        return getattr(self._llm, "_llm_type", "model-retry-wrapper")

    def __getattr__(self, name):
        return getattr(self._llm, name)
