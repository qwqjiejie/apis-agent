import glob
import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger("dodo")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _safe_path(path: str) -> str:
    p = os.path.abspath(os.path.join(PROJECT_ROOT, path))
    if not p.startswith(PROJECT_ROOT):
        raise ValueError(f"路径超出项目范围: {path}")
    return p


@tool
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """读取文件内容，可指定起始行和行数。

    path: 相对于项目根目录的文件路径
    offset: 起始行号（0-based）
    limit: 读取行数上限，默认 500
    """
    try:
        full = _safe_path(path)
        with open(full, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        chunk = lines[offset:offset + limit]
        return "".join(chunk) if chunk else "(空)"
    except ValueError as e:
        return str(e)
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except Exception as e:
        return f"读取失败: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """写入内容到文件（覆盖模式）。

    path: 相对于项目根目录的文件路径
    content: 要写入的内容
    """
    try:
        full = _safe_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入: {path} ({len(content)} 字符)"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"写入失败: {e}"


@tool
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """在文件中替换文本（替换首次出现）。old_string 和 new_string 必须不同。

    path: 相对于项目根目录的文件路径
    old_string: 要替换的旧文本
    new_string: 替换后的新文本
    """
    try:
        full = _safe_path(path)
        with open(full, "r", encoding="utf-8") as f:
            original = f.read()
        if old_string not in original:
            return f"未找到匹配文本: {old_string[:80]}..."
        updated = original.replace(old_string, new_string, 1)
        with open(full, "w", encoding="utf-8") as f:
            f.write(updated)
        return f"已编辑: {path}"
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"编辑失败: {e}"


@tool
def list_files(path: str = ".") -> str:
    """列出目录下的文件和子目录。

    path: 相对于项目根目录的路径，默认根目录
    """
    try:
        full = _safe_path(path)
        items = []
        for name in sorted(os.listdir(full)):
            item_path = os.path.join(full, name)
            tag = "/" if os.path.isdir(item_path) else ""
            items.append(f"  {name}{tag}")
        return "\n".join(items) if items else "(空目录)"
    except ValueError as e:
        return str(e)
    except FileNotFoundError:
        return f"目录不存在: {path}"
    except Exception as e:
        return f"列出失败: {e}"


@tool
def glob_files(pattern: str) -> str:
    """使用 glob 模式匹配文件。

    pattern: glob 模式，如 "**/*.py" 或 "src/**/*.ts"
    """
    try:
        full_pattern = os.path.join(PROJECT_ROOT, pattern)
        matches = sorted(glob.glob(full_pattern, recursive=True))
        if not matches:
            return "(无匹配文件)"
        items = [os.path.relpath(m, PROJECT_ROOT) for m in matches[:100]]
        return "\n".join(f"  {item}" for item in items)
    except Exception as e:
        return f"glob 失败: {e}"
