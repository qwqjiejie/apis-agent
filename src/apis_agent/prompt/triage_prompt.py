"""Triage DeepAgent 的 system prompt。

职责：判断任务复杂度 → 简单任务直接处理或委托 Specialist / 复杂任务创建后台任务。
Specialist 列表和工具列表从注册中心动态生成。
"""

# 能力前缀 → Specialist 映射，前端在用户输入前拼接
CAPABILITY_PREFIX_MAP: dict[str, str] = {
    "生成ppt:": "ppt_specialist",
    "深度研究:": "research_specialist",
    "分析文档:": "file_analysis_specialist",
    "数据分析:": "data_analysis_specialist",
    "代码审查:": "code_review_specialist",
    "写代码:": "coding_specialist",
}


def parse_capability_prefix(message: str) -> tuple[str | None, str]:
    """解析能力前缀，返回 (specialist_name, 清洗后的消息)。"""
    for prefix, specialist in CAPABILITY_PREFIX_MAP.items():
        if message.startswith(prefix):
            return specialist, message[len(prefix):].strip()
    return None, message


def _build_specialist_table(subagents: list[dict]) -> str:
    if not subagents:
        return "（无可用 Specialist）"
    rows = []
    for sa in subagents:
        name = sa.get("name", "unknown")
        desc = sa.get("description", "")
        rows.append(f"| ``{name}`` | {desc} |")
    return "\n".join(rows)


def _build_tool_section() -> str:
    from src.apis_agent.tool import TOOL_REGISTRY

    _CATEGORIES: dict[str, str] = {
        "src.apis_agent.tool.common": "通用工具",
        "src.apis_agent.tool.tavily_search": "联网搜索",
        "src.apis_agent.tool.bash_tool": "Shell 工具",
        "src.apis_agent.tool.file_system_tools": "文件系统",
        "src.apis_agent.tool.grep_tool": "代码搜索",
        "src.apis_agent.tool.skills_tool": "技能加载",
    }
    _ORDER = ["通用工具", "联网搜索", "Shell 工具", "文件系统", "代码搜索", "技能加载"]

    categorized: dict[str, list[tuple[str, str]]] = {}
    uncategorized: list[tuple[str, str]] = []

    for name, tool in TOOL_REGISTRY.items():
        module = getattr(tool, "__module__", "")
        desc = (getattr(tool, "description", "") or "").split("\n")[0]
        cat = _CATEGORIES.get(module)
        if cat:
            categorized.setdefault(cat, []).append((name, desc))
        else:
            uncategorized.append((name, desc))

    lines: list[str] = []
    for cat in _ORDER:
        tools_in_cat = categorized.get(cat, [])
        if tools_in_cat:
            lines.append(f"### {cat}")
            for name, desc in tools_in_cat:
                lines.append(f"- **{name}**: {desc}")
            lines.append("")

    if uncategorized:
        lines.append("### 其他工具")
        for name, desc in uncategorized:
            lines.append(f"- **{name}**: {desc}")
        lines.append("")

    return "\n".join(lines)


def build_triage_prompt(subagents: list[dict] | None = None) -> str:
    subagents = subagents or []
    specialist_table = _build_specialist_table(subagents)
    tool_section = _build_tool_section()

    return f"""你是企业 Multi-Agent 系统的 **AI 助手**。

你的核心职责是**判断任务复杂度并分流**：
- **简单任务** → 直接调用工具处理，或委托给对应的 Specialist
- **复杂任务** → 调用 ``create_background_task``，交给后台引擎异步执行

## 可用 Specialist

| Specialist | 能力描述 |
|------------|---------|
{specialist_table}

## 可用工具

系统已自动绑定以下工具，根据场景选择调用：

{tool_section}
### 任务管理工具
- **task**: 将子任务委托给上表中的 Specialist（同步返回结果）
- **create_background_task**: 将复杂任务转为后台异步执行
- **get_task_status**: 查询后台任务状态、进度和结果

## 分流规则

### 简单直接处理
- 单个工具或 Specialist 就能完成
- 不需要审批或人类决策
- 单轮对话可给出完整答案

### 创建后台任务
符合以下**任一**条件：
- 需要 ≥2 个 Specialist 协作
- 涉及审批或人类决策
- 需要多步骤长周期执行

## 约束
- 用中文交流
- 不编造信息——知识库没有的内容请明确告知
- 用户询问后台任务进展时必须调用 ``get_task_status``
"""
