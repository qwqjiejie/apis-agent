# TOOL_REGISTRY — 全局工具注册表 + @register_tool 装饰器

TOOL_REGISTRY: dict[str, object] = {}
_TOOL_SOURCES: dict[str, str] = {}  # tool_name → module_name


def register_tool(func):
    """装饰器：将 @tool 函数自动注册到 TOOL_REGISTRY。

    处理 @tool 装饰器改写 __module__ 的问题：
    通过 func.func (sync) / func.coroutine (async) 获取真实的来源模块名。
    同一模块的工具允许热重载覆盖，跨模块名称冲突则报错。
    """
    original = getattr(func, "coroutine", None) or getattr(func, "func", None) or func
    module_name = original.__module__

    name = getattr(func, "name", None) or original.__name__

    if name in TOOL_REGISTRY:
        existing_source = _TOOL_SOURCES.get(name, "unknown")
        if existing_source == module_name:
            TOOL_REGISTRY[name] = func
            _TOOL_SOURCES[name] = module_name
            return func
        raise ValueError(
            f"工具名冲突: '{name}' 已被 {existing_source} 注册, "
            f"当前模块: {module_name}"
        )

    TOOL_REGISTRY[name] = func
    _TOOL_SOURCES[name] = module_name
    return func


def unregister_module(module_name: str) -> list[str]:
    """清除某模块注册的所有工具（热重载时调用）。"""
    to_remove = [name for name, src in _TOOL_SOURCES.items() if src == module_name]
    for name in to_remove:
        del TOOL_REGISTRY[name]
        del _TOOL_SOURCES[name]
    return to_remove
