import logging
import os
import re
from typing import Any

from langchain_core.tools import StructuredTool

logger = logging.getLogger("apis")

SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills"))


def _parse_skill(filepath: str) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    meta = {}
    body = content
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if m:
        body = content[m.end():]
        for line in m.group(1).strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()

    name = meta.get("name", os.path.basename(os.path.dirname(filepath)))
    description = meta.get("description", f"技能: {name}")
    return {"name": name, "description": description, "body": body.strip(), "path": filepath}


def _make_skill_tool(skill: dict) -> Any:
    name = skill["name"].replace("-", "_").replace(" ", "_")

    def skill_func(query: str = "") -> str:
        """执行技能（由 agent 自动调用）"""
        return skill["body"]

    skill_func.__name__ = name

    return StructuredTool.from_function(
        func=skill_func,
        name=name,
        description=skill["description"],
    )


def load_skills() -> list:
    if not os.path.isdir(SKILLS_DIR):
        return []
    tools = []
    for root, dirs, files in os.walk(SKILLS_DIR):
        for f in files:
            if f.upper() == "SKILL.MD":
                skill = _parse_skill(os.path.join(root, f))
                if skill:
                    tools.append(_make_skill_tool(skill))
                    logger.info(f"[skills] 加载: {skill['name']}")
    return tools
