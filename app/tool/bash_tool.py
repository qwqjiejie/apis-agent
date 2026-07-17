import asyncio
import logging
import os
import re
import uuid

from langchain_core.tools import tool

from app.tool.registry import register_tool

logger = logging.getLogger("apis")

TIMEOUT = 30
MAX_OUTPUT = 10000
WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# =============================================================================
# 命令分类 — 读命令直接放行，写/删命令需用户确认
# =============================================================================

SAFE_PATTERNS = [
    r'^(ls|dir|vdir)\b', r'^(cat|head|tail|less|more|zcat|zless)\b',
    r'^(grep|egrep|fgrep|rg|ack|ag)\b', r'^(find|locate|which|whereis|where)\b',
    r'^(wc|md5|md5sum|sha1sum|sha256sum|sha512sum|cksum|file|stat)\b',
    r'^(du|df|ncdu)\b', r'^(ps|top|htop|pstree|pgrep|pidof)\b',
    r'^(who|w|whoami|id|groups|users|last|lastlog)\b',
    r'^(date|cal|uptime|hostname|uname|arch)\b',
    r'^(echo|printf|pwd|printenv|env)\b',
    r'^(man|info|whatis|apropos|help)\b',
    r'^(ping|traceroute|nslookup|dig|host|netstat|ss|ip\s+addr|ip\s+link|ip\s+route|ifconfig)\b',
    r'^(curl\s+\S+\|?\s*(head|tail|less|grep|wc|sort))', r'^(wget\s+\S+\|?\s*(head|tail|less|grep|wc|sort))',
    r'^(git\s+(status|log|diff|show|branch|tag|stash\s+list|remote\s+\S+\s+show))\b',
    r'^(docker\s+(ps|images|info|logs|inspect|stats|version))\b',
    r'^(pip|pip3)\s+(list|show|freeze|check)\b',
    r'^(npm|yarn|pnpm)\s+(list|ls|info|view|outdated)\b',
]

DANGEROUS_PATTERNS = [
    r'\brm\b', r'\brmdir\b', r'\bunlink\b',
    r'>\s*/dev/', r'>\s*\S+', r'>>\s*\S+',
    r'\b(mv|move)\b', r'\bcp\s.*\b', r'\bdd\b',
    r'\b(chmod|chown|chgrp|chattr|chroot)\b',
    r'\b(kill|pkill|killall|xkill)\b',
    r'\b(apt|apt-get|yum|dnf|brew|pacman|zypper)\s+(install|remove|purge|update|upgrade|dist-upgrade)\b',
    r'\b(pip|pip3|python\S*\s+-m\s+pip)\s+(install|uninstall)\b',
    r'\b(npm|yarn|pnpm)\s+(install|uninstall|add|remove)\s',
    r'\b(git\s+(push|merge|rebase|reset|clean|stash\s+(drop|clear)|branch\s+-D|tag\s+-d))\b',
    r'\b(docker\s+(rm|rmi|stop|kill|prune|build|push|tag|commit|exec|run|start|restart))\b',
    r'\b(systemctl|service)\s+(start|stop|restart|enable|disable|mask)\b',
    r'\b(shutdown|reboot|halt|poweroff|init\s+[0-6])\b',
    r'\b(mount|umount|mkfs|fdisk|parted|fsck)\b',
    r'\b(useradd|userdel|usermod|groupadd|groupdel|passwd)\b',
    r'\b(iptables|ufw|firewall-cmd|nft)\b',
    r'\bchsh\b', r'\bcrontab\b',
    r'\b(export|unset|source)\s+\S',
    r'\|\s*(sh|bash|zsh|dash|ksh|python|perl|ruby)\b',
    r'\b(curl|wget)\s+.*\|\s*(sh|bash|zsh)\b',
    r'\b(curl|wget)\s+.*-o\s', r'\b(curl|wget)\s+.*-O\s',
    r'\bopenssl\b', r'\bssh-keygen\b', r'\bssh-copy-id\b',
    r'\beval\b', r'\bexec\b',
    r'\b(ln|link)\s+-[sf]\b',
    r'\.\.\/', r'\/etc\/', r'\/var\/', r'\/tmp\/',
]


def classify_command(command: str) -> tuple[bool, str]:
    """判断命令是否安全。返回 (is_safe, reason)。

    先检查安全模式，再检查危险模式。
    都不匹配时默认为安全（LLM 通常只执行简单的读命令）。
    """
    cmd_clean = command.strip()

    for pattern in SAFE_PATTERNS:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return True, ""

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return False, f"命令包含危险操作: {pattern}"

    return True, ""


# =============================================================================
# 确认机制 — 全局 store，agent 循环通过 side_queue 监听
# =============================================================================

_pending: dict[str, dict] = {}          # confirm_id -> {event, approved, command}
_side_queue: asyncio.Queue | None = None


def set_shell_side_queue(queue: asyncio.Queue | None):
    global _side_queue
    _side_queue = queue


async def _request_confirmation(command: str) -> bool:
    """请求用户确认危险命令。超时 120 秒默认拒绝。"""
    confirm_id = uuid.uuid4().hex[:8]
    event = asyncio.Event()
    _pending[confirm_id] = {"event": event, "approved": False, "command": command}

    if _side_queue is not None:
        await _side_queue.put({"type": "confirm_shell", "confirmId": confirm_id, "command": command})

    try:
        await asyncio.wait_for(event.wait(), timeout=120)
        return _pending[confirm_id]["approved"]
    except asyncio.TimeoutError:
        return False
    finally:
        _pending.pop(confirm_id, None)


def resolve_confirmation(confirm_id: str, approved: bool):
    """API 端点调用此函数来确认/拒绝命令。"""
    entry = _pending.get(confirm_id)
    if entry:
        entry["approved"] = approved
        entry["event"].set()
        return True
    return False


def get_pending_confirmations() -> list[dict]:
    """获取所有待确认的命令列表。"""
    return [{"confirmId": cid, "command": entry["command"]} for cid, entry in _pending.items()]


# =============================================================================
# 工具函数
# =============================================================================


@register_tool
@tool
async def bash_tool(command: str) -> str:
    """执行 Shell 命令，返回 stdout + stderr。

    适用场景：运行脚本、安装依赖、执行系统命令、查看进程状态等。
    超时 30 秒，输出上限 10000 字符。
    工作目录为项目根目录。
    危险命令（删除/修改文件、安装软件、kill 进程等）需要用户确认。
    """
    logger.info(f"[bash] {command[:200]}")

    is_safe, reason = classify_command(command)
    if not is_safe:
        logger.warning(f"[bash] 危险命令需确认: {reason}")
        approved = await _request_confirmation(command)
        if not approved:
            return "用户取消了命令执行"

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
