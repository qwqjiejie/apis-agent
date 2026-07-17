"""Markdown frontmatter 解析工具。"""

import re
from pathlib import Path


def parse_frontmatter(filepath: Path) -> dict:
    """解析 SKILL.md / AGENT.md 的 YAML frontmatter。

    返回 {name, description, body, ...}。
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return {}

    meta: dict[str, str] = {}
    body = content
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if m:
        body = content[m.end():]
        in_multiline = False
        multiline_key = ""
        multiline_lines: list[str] = []

        for line in m.group(1).strip().split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if in_multiline:
                if line and line[0] in (" ", "\t"):
                    multiline_lines.append(line)
                    continue
                else:
                    meta[multiline_key] = "\n".join(multiline_lines).strip()
                    in_multiline = False

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if value in ("|", ">", "|-", ">-"):
                    in_multiline = True
                    multiline_key = key
                    multiline_lines = []
                else:
                    meta[key] = value

        if in_multiline:
            meta[multiline_key] = "\n".join(multiline_lines).strip()

    meta["body"] = body.strip()
    if "name" not in meta:
        meta["name"] = filepath.parent.name
    return meta
