import logging
import re
from pathlib import Path

logger = logging.getLogger("apis")

SPECIALIST_DIR = Path(__file__).resolve().parent.parent / "subagents"


def _parse_agent_md(filepath: Path) -> dict | None:
    """解析 AGENT.md 文件，返回 {name, description, system_prompt, allowed_tools}。"""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    meta = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if m:
        body = content[m.end():]
        for line in m.group(1).strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    else:
        body = content

    name = meta.get("name", filepath.parent.name)
    if not name:
        return None

    allowed = meta.get("allowed_tools", "")
    allowed_tools = [t.strip() for t in allowed.split(",") if t.strip()] if allowed else []

    return {
        "name": name,
        "description": meta.get("description", f"Specialist: {name}"),
        "system_prompt": body.strip(),
        "allowed_tools": allowed_tools,
    }


def discover_specialists() -> list[dict]:
    """扫描 subagents/ 目录，返回所有已发现的子 Agent 定义。"""
    if not SPECIALIST_DIR.is_dir():
        return []

    specialists = []
    for entry in sorted(SPECIALIST_DIR.iterdir()):
        if not entry.is_dir():
            continue
        agent_md = entry / "AGENT.md"
        if agent_md.is_file():
            spec = _parse_agent_md(agent_md)
            if spec:
                specialists.append(spec)
                logger.info(f"[SubAgent] 发现: {spec['name']} ({entry.name})")

    return specialists
