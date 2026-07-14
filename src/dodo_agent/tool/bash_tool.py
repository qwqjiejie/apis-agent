import asyncio
import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger("dodo")

TIMEOUT = 30
MAX_OUTPUT = 10000
WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@tool
async def bash_tool(command: str) -> str:
    """执行 Shell 命令，返回 stdout + stderr。

    适用场景：运行脚本、安装依赖、执行系统命令、查看进程状态等。
    超时 30 秒，输出上限 10000 字符。
    工作目录为项目根目录。
    """
    logger.info(f"[bash] {command[:200]}")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            shell=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORK_DIR,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        if stderr:
            output += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output[-MAX_OUTPUT:]
    except asyncio.TimeoutError:
        return f"命令超时（{TIMEOUT}s）: {command[:100]}"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return f"执行失败: {e}"
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
