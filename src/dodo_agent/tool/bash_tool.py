import logging
import os
import subprocess
import time

from langchain_core.tools import tool

logger = logging.getLogger("dodo")

TIMEOUT = 30
MAX_OUTPUT = 10000
WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@tool
def bash_tool(command: str) -> str:
    """执行 Shell 命令，返回 stdout + stderr。

    适用场景：运行脚本、安装依赖、执行系统命令、查看进程状态等。
    超时 30 秒，输出上限 10000 字符。
    工作目录为项目根目录。
    """
    logger.info(f"[bash] {command[:200]}")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=WORK_DIR,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        output = proc.stdout
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output[-MAX_OUTPUT:]
    except subprocess.TimeoutExpired:
        return f"命令超时（{TIMEOUT}s）: {command[:100]}"
    except Exception as e:
        return f"执行失败: {e}"
