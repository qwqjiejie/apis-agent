import importlib
import logging
from pathlib import Path

from app.tool.registry import TOOL_REGISTRY, register_tool, unregister_module  # noqa: F401

logger = logging.getLogger("apis")

# 自动发现并导入 tools/ 下所有 .py 模块（跳过 _ 前缀和 registry）
_tools_dir = Path(__file__).parent
for _file in sorted(_tools_dir.glob("*.py")):
    _name = _file.stem
    if _name.startswith("_") or _name == "registry":
        continue
    try:
        importlib.import_module(f"app.tool.{_name}")
    except Exception:
        logger.exception(f"导入工具模块失败: {_name}")

logger.info(f"工具注册中心初始化完成 — {len(TOOL_REGISTRY)} 个工具已注册")
