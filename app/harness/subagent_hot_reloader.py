"""SubAgentHotReloader — 监听 specialist/ 目录，AGENT.md 变更时触发 Agent 重建。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

from watchfiles import awatch

logger = logging.getLogger("apis")


class SubAgentHotReloader:
    """监听 agent/specialist/ 目录，AGENT.md 变更时触发 Agent 重建。"""

    def __init__(self, subagents_dir: Path, on_reload: Callable[[], Awaitable[None]]):
        self._subagents_dir = subagents_dir
        self._on_reload = on_reload
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self):
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"[SubAgentHotReload] 已启动，监听 {self._subagents_dir}")

    async def stop(self):
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                async for changes in awatch(self._subagents_dir):
                    if self._stop_event.is_set():
                        break
                    has_md_change = any(Path(p).name == "AGENT.md" for _, p in changes)
                    if has_md_change:
                        logger.info("[SubAgentHotReload] 检测到 AGENT.md 变更，触发重建")
                        await self._on_reload()
                break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[SubAgentHotReload] watch loop error")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
