"""SkillManager — Skills DB 生命周期管理 + 文件系统同步。

- 启动时自动建表 + 同步文件系统 → DB
- 定时同步（每 3 分钟）
- 提供 enabled skills 目录路径供 SkillsTool 使用
- 支持 zip 上传、启用/禁用、删除
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger("apis")

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SYNC_INTERVAL_SEC = 180


class SkillManager:
    """Skills 管理器 — DB 状态 + 文件系统同步。"""

    def __init__(self):
        self._db_available = False
        self._jdbc = None

    def init_db(self, jdbc_template=None):
        """初始化 DB 连接并建表。jdbc_template 为 SQLAlchemy/JdbcTemplate。"""
        if jdbc_template is None:
            try:
                from src.apis_agent.storage.db import new_session
                self._jdbc = new_session()
                self._db_available = True
            except Exception:
                logger.warning("[SkillManager] DB 不可用，Skills 仅从文件系统加载")
                return

        if self._db_available:
            self._ensure_table()
            self.sync_filesystem_to_db()

    def _ensure_table(self):
        """自动创建 agentx_skill 表（幂等）。"""
        try:
            self._jdbc.execute(
                """CREATE TABLE IF NOT EXISTS agentx_skill (
                    id BIGSERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    skill_path VARCHAR(500) NOT NULL,
                    description VARCHAR(500),
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    version VARCHAR(20) DEFAULT '1.0.0',
                    author VARCHAR(100),
                    file_name VARCHAR(200),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            self._jdbc.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_name ON agentx_skill(name)"""
            )
            self._jdbc.commit()
            logger.info("[SkillManager] agentx_skill 表已就绪")
        except Exception as e:
            logger.warning(f"[SkillManager] 建表失败: {e}")
            self._jdbc.rollback()

    def sync_filesystem_to_db(self) -> int:
        """扫描 skills/ 目录，将新 skill 同步到 DB，返回新增数量。"""
        if not self._db_available or not SKILLS_DIR.is_dir():
            return 0

        from src.apis_agent.utils.frontmatter_utils import parse_frontmatter

        existing = self._list_db_names()
        added = 0
        for child in sorted(SKILLS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue

            spec = parse_frontmatter(skill_md)
            name = spec.get("name", child.name)
            if name in existing:
                continue

            try:
                self._jdbc.execute(
                    """INSERT INTO agentx_skill (name, skill_path, description, enabled, file_name)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (name) DO NOTHING""",
                    (
                        name,
                        str(child),
                        spec.get("description", ""),
                        True,
                        child.name,
                    ),
                )
                self._jdbc.commit()
                added += 1
                logger.info(f"[SkillManager] 发现新 Skill: {name}")
            except Exception as e:
                logger.warning(f"[SkillManager] 同步 Skill {name} 失败: {e}")
                self._jdbc.rollback()

        return added

    def _list_db_names(self) -> set[str]:
        try:
            rows = self._jdbc.execute("SELECT name FROM agentx_skill").fetchall()
            return {r[0] for r in rows}
        except Exception:
            return set()

    # ── 对外接口 ──────────────────────────────────

    def get_enabled_skill_dirs(self) -> list[str]:
        """返回所有 enabled skill 的目录路径。DB 不可用时回退文件系统扫描。"""
        if self._db_available:
            try:
                rows = self._jdbc.execute(
                    "SELECT skill_path FROM agentx_skill WHERE enabled = TRUE"
                ).fetchall()
                dirs = [r[0] for r in rows if r[0] and Path(r[0]).is_dir()]
                if dirs:
                    return dirs
            except Exception:
                pass
        return self._scan_filesystem()

    def _scan_filesystem(self) -> list[str]:
        dirs = []
        if SKILLS_DIR.is_dir():
            for child in SKILLS_DIR.iterdir():
                if child.is_dir() and not child.name.startswith("_"):
                    if (child / "SKILL.md").is_file():
                        dirs.append(str(child))
        return dirs

    def list_skills(self) -> list[dict]:
        """返回所有 Skill 信息（含启用状态）。"""
        if self._db_available:
            try:
                rows = self._jdbc.execute(
                    """SELECT name, skill_path, description, enabled, version, author, file_name
                       FROM agentx_skill ORDER BY name"""
                ).fetchall()
                return [
                    {
                        "name": r[0], "skillPath": r[1], "description": r[2] or "",
                        "enabled": bool(r[3]), "version": r[4] or "", "author": r[5] or "",
                        "fileName": r[6] or "",
                    }
                    for r in rows
                ]
            except Exception:
                pass
        return []

    def toggle_enabled(self, name: str, enabled: bool) -> bool:
        if not self._db_available:
            return False
        try:
            self._jdbc.execute(
                "UPDATE agentx_skill SET enabled = %s, updated_at = NOW() WHERE name = %s",
                (enabled, name),
            )
            self._jdbc.commit()
            logger.info(f"[SkillManager] {name} enabled={enabled}")
            return True
        except Exception:
            self._jdbc.rollback()
            return False

    def delete_skill(self, name: str) -> bool:
        """删除 Skill（DB 记录 + 文件系统目录）。"""
        if not self._db_available:
            return False
        try:
            row = self._jdbc.execute(
                "SELECT skill_path FROM agentx_skill WHERE name = %s", (name,)
            ).fetchone()
            if not row:
                return False

            skill_path = Path(row[0])
            if skill_path.is_dir():
                import shutil
                shutil.rmtree(skill_path, ignore_errors=True)

            self._jdbc.execute("DELETE FROM agentx_skill WHERE name = %s", (name,))
            self._jdbc.commit()
            logger.info(f"[SkillManager] 已删除: {name}")
            return True
        except Exception:
            self._jdbc.rollback()
            return False

    def upload_zip(self, zip_data: bytes, file_name: str) -> dict | None:
        """上传 Skill zip 包，解压到 skills/ 目录，同步到 DB。"""
        import io
        import zipfile

        dest_dir = SKILLS_DIR / file_name.rsplit(".", 1)[0]
        if dest_dir.exists():
            return None

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                zf.extractall(dest_dir)
        except Exception as e:
            logger.error(f"[SkillManager] zip 解压失败: {e}")
            return None

        skill_md = dest_dir / "SKILL.md"
        if not skill_md.is_file():
            import shutil
            shutil.rmtree(dest_dir, ignore_errors=True)
            return None

        self.sync_filesystem_to_db()
        from src.apis_agent.utils.frontmatter_utils import parse_frontmatter
        spec = parse_frontmatter(skill_md)
        return {
            "name": spec.get("name", dest_dir.name),
            "skillPath": str(dest_dir),
            "description": spec.get("description", ""),
            "enabled": True,
            "fileName": file_name,
        }


skill_manager = SkillManager()
