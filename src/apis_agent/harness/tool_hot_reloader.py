import asyncio
import importlib
import logging
import sys
from pathlib import Path

from watchfiles import Change, awatch

from src.apis_agent.tool.registry import TOOL_REGISTRY, _TOOL_SOURCES, unregister_module

logger = logging.getLogger("apis")


class ToolHotReloader:
    """监听 tools/ 目录，文件变更时自动重载工具模块并重建 Agent。"""

    def __init__(self, tools_dir: Path, on_reload=None):
        self._tools_dir = tools_dir
        self._on_reload = on_reload
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self):
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"[HotReload] 工具热加载器已启动，监听 {self._tools_dir}")

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
        logger.info("[HotReload] 工具热加载器已停止")

    async def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                async for changes in awatch(self._tools_dir):
                    if self._stop_event.is_set():
                        break
                    await self._handle_changes(changes)
                break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[HotReload] watch loop 异常")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def _handle_changes(self, changes: set[tuple[int, str]]):
        reloaded = False

        for change_type, path_str in changes:
            path = Path(path_str)
            if path.suffix != ".py":
                continue

            name = path.stem
            if name.startswith("_") or name == "registry":
                continue

            module_name = f"src.apis_agent.tool.{name}"

            try:
                if change_type == Change.deleted:
                    removed = unregister_module(module_name)
                    if removed:
                        logger.info(f"[HotReload] 工具文件已删除: {name} → {removed}")
                    if module_name in sys.modules:
                        del sys.modules[module_name]
                    reloaded = True
                    continue

                # 新增/修改：先重载模块，成功后再清旧工具
                old_names = [n for n, src in _TOOL_SOURCES.items() if src == module_name]

                try:
                    if module_name in sys.modules:
                        importlib.reload(sys.modules[module_name])
                    else:
                        importlib.import_module(module_name)
                    logger.info(f"[HotReload] 工具模块已重载: {module_name}")
                except Exception:
                    logger.exception(f"[HotReload] 重载失败: {module_name}")
                    continue

                # 清理新版本不再导出的僵尸工具
                new_names = [n for n, src in _TOOL_SOURCES.items() if src == module_name]
                for n in set(old_names) - set(new_names):
                    del TOOL_REGISTRY[n]
                    del _TOOL_SOURCES[n]
                    logger.info(f"[HotReload] 清理旧工具: {n}")

                reloaded = True

            except Exception:
                logger.exception(f"[HotReload] 处理变更失败: {path_str}")

        if reloaded and self._on_reload is not None:
            try:
                await self._on_reload()
            except Exception:
                logger.exception("[HotReload] on_reload 回调失败")
