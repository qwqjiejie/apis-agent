"""SkillsTool — 从 SkillManager 动态加载 enabled skills。"""

import logging
from typing import Any

logger = logging.getLogger("apis")


def load_skills() -> list:
    """从 SkillManager 加载所有 enabled skills，构建工具列表。

    DB 不可用时自动回退到文件系统扫描。
    """
    from app.skill.skill_manager import skill_manager
    from app.utils.frontmatter_utils import parse_frontmatter
    from pathlib import Path

    dirs = skill_manager.get_enabled_skill_dirs()
    tools = []
    for d in dirs:
        skill_md = Path(d) / "SKILL.md"
        if not skill_md.is_file():
            continue
        spec = parse_frontmatter(skill_md)
        name = spec.get("name", Path(d).name)
        description = spec.get("description", f"技能: {name}")
        body = spec.get("body", "")

        # 构建 LangChain 工具
        from langchain_core.tools import StructuredTool

        safe_name = name.replace("-", "_").replace(" ", "_")

        def _make_func(skill_body: str, skill_name: str):
            def skill_func(query: str = "") -> str:
                return skill_body
            skill_func.__name__ = skill_name
            return skill_func

        tool = StructuredTool.from_function(
            func=_make_func(body, safe_name),
            name=safe_name,
            description=description,
        )
        tools.append(tool)
        logger.info(f"[SkillsTool] 加载: {name} ({d})")

    return tools
