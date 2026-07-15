import logging
import os
import re
import subprocess

from langchain_core.tools import tool

logger = logging.getLogger("apis")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _try_rg(pattern: str, path: str) -> str | None:
    try:
        proc = subprocess.run(
            ["rg", "--no-heading", "--line-number", "-n", pattern, path],
            capture_output=True, text=True, timeout=10, cwd=PROJECT_ROOT,
        )
        if proc.returncode in (0, 1):
            return proc.stdout[:8000] or "(无匹配)"
        return None
    except Exception:
        return None


def _python_grep(pattern: str, path: str) -> str:
    try:
        compiled = re.compile(pattern)
        full = os.path.join(PROJECT_ROOT, path)
        if os.path.isfile(full):
            targets = [full]
        else:
            targets = []
            for root, _, files in os.walk(full):
                for f in files:
                    targets.append(os.path.join(root, f))
                if len(targets) > 500:
                    break
        lines_out = []
        for fp in targets:
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if compiled.search(line):
                            rel = os.path.relpath(fp, PROJECT_ROOT)
                            lines_out.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(lines_out) >= 100:
                                break
                if len(lines_out) >= 100:
                    break
            except Exception:
                continue
        return "\n".join(lines_out) if lines_out else "(无匹配)"
    except Exception as e:
        return f"grep 失败: {e}"


@tool
def grep_tool(pattern: str, path: str = ".") -> str:
    """在项目中搜索匹配正则表达式的内容。

    pattern: 正则表达式，如 "def foo" 或 "import.*os"
    path: 搜索路径（文件或目录），相对于项目根目录，默认 "."
    优先使用 ripgrep (rg)，不可用时回退 Python 正则。
    """
    logger.info(f"[grep] pattern={pattern[:100]} path={path}")
    result = _try_rg(pattern, path)
    if result is not None:
        return result
    return _python_grep(pattern, path)
