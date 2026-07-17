"""运行时 Skills 的发现、持久化状态和安全上传。"""

from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

from app.config.settings import get_settings
from app.infrastructure.postgres.database import session_scope
from app.utils.frontmatter_utils import parse_frontmatter

logger = logging.getLogger("apis")

SYNC_INTERVAL_SEC = 180
SKILLS_DIR = get_settings().bundled_skills_path
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class SkillManager:
    """内置 Skill 只读，上传 Skill 写入可配置运行数据目录。"""

    def __init__(
        self,
        *,
        bundled_dir: Path | None = None,
        managed_dir: Path | None = None,
    ):
        settings = get_settings()
        self._bundled_dir = bundled_dir or settings.bundled_skills_path
        self._managed_dir = managed_dir or settings.managed_skills_path
        self._db_available = False

    @property
    def managed_dir(self) -> Path:
        return self._managed_dir

    def initialize(self) -> bool:
        """验证迁移后的表并同步文件系统；失败时降级为只读扫描。"""
        self._managed_dir.mkdir(parents=True, exist_ok=True)
        try:
            with session_scope() as db:
                db.execute(text("SELECT 1 FROM agentx_skill LIMIT 1"))
            self._db_available = True
            self.sync_filesystem_to_db()
        except Exception as exc:
            self._db_available = False
            logger.warning("[SkillManager] DB 不可用，降级到文件系统: %s", exc)
        return self._db_available

    def close(self) -> None:
        self._db_available = False

    def _skill_dirs(self) -> Iterable[Path]:
        seen: set[Path] = set()
        for root in (self._bundled_dir, self._managed_dir):
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                resolved = child.resolve()
                if (
                    child.is_dir()
                    and not child.name.startswith("_")
                    and (child / "SKILL.md").is_file()
                    and resolved not in seen
                ):
                    seen.add(resolved)
                    yield child

    def sync_filesystem_to_db(self) -> int:
        if not self._db_available:
            return 0
        existing = self._list_db_names()
        added = 0
        for child in self._skill_dirs():
            spec = parse_frontmatter(child / "SKILL.md")
            name = str(spec.get("name", child.name))
            if name in existing:
                continue
            try:
                with session_scope() as db:
                    db.execute(
                        text(
                            """INSERT INTO agentx_skill
                               (name, skill_path, description, enabled, file_name)
                               VALUES (:name, :skill_path, :description, TRUE, :file_name)
                               ON CONFLICT (name) DO NOTHING"""
                        ),
                        {
                            "name": name,
                            "skill_path": str(child.resolve()),
                            "description": spec.get("description", ""),
                            "file_name": child.name,
                        },
                    )
                added += 1
                existing.add(name)
            except Exception as exc:
                logger.warning("[SkillManager] 同步 %s 失败: %s", name, exc)
        return added

    def _list_db_names(self) -> set[str]:
        try:
            with session_scope() as db:
                rows = db.execute(text("SELECT name FROM agentx_skill")).all()
            return {row.name for row in rows}
        except Exception:
            return set()

    def get_enabled_skill_dirs(self) -> list[str]:
        if self._db_available:
            try:
                with session_scope() as db:
                    rows = db.execute(
                        text(
                            "SELECT skill_path FROM agentx_skill "
                            "WHERE enabled = TRUE ORDER BY name"
                        )
                    ).all()
                paths = [
                    str(Path(row.skill_path))
                    for row in rows
                    if row.skill_path and Path(row.skill_path).is_dir()
                ]
                if paths:
                    return paths
            except Exception:
                pass
        return [str(path) for path in self._skill_dirs()]

    def list_skills(self) -> list[dict]:
        if self._db_available:
            try:
                with session_scope() as db:
                    rows = db.execute(text(
                        """SELECT name, skill_path, description, enabled,
                                  version, author, file_name
                           FROM agentx_skill ORDER BY name"""
                    )).all()
                return [
                    {
                        "name": row.name,
                        "skillPath": row.skill_path,
                        "description": row.description or "",
                        "enabled": bool(row.enabled),
                        "version": row.version or "",
                        "author": row.author or "",
                        "fileName": row.file_name or "",
                    }
                    for row in rows
                ]
            except Exception:
                pass
        return [
            {
                "name": spec.get("name", path.name),
                "skillPath": str(path),
                "description": spec.get("description", ""),
                "enabled": True,
                "version": spec.get("version", ""),
                "author": spec.get("author", ""),
                "fileName": path.name,
            }
            for path in self._skill_dirs()
            for spec in (parse_frontmatter(path / "SKILL.md"),)
        ]

    def toggle_enabled(self, name: str, enabled: bool) -> bool:
        if not self._db_available:
            return False
        with session_scope() as db:
            result = db.execute(
                text(
                    "UPDATE agentx_skill SET enabled = :enabled, updated_at = NOW() "
                    "WHERE name = :name"
                ),
                {"enabled": enabled, "name": name},
            )
            return (result.rowcount or 0) > 0

    def delete_skill(self, name: str) -> bool:
        """仅删除运行数据目录中的 Skill，内置 Skill 不允许物理删除。"""
        if not self._db_available:
            return False
        with session_scope() as db:
            row = db.execute(
                text("SELECT skill_path FROM agentx_skill WHERE name = :name"),
                {"name": name},
            ).first()
            if not row:
                return False
            skill_path = Path(row.skill_path).resolve()
            try:
                skill_path.relative_to(self._managed_dir.resolve())
            except ValueError:
                return False
            db.execute(
                text("DELETE FROM agentx_skill WHERE name = :name"),
                {"name": name},
            )
        shutil.rmtree(skill_path, ignore_errors=True)
        return True

    def upload_zip(self, zip_data: bytes, file_name: str) -> dict | None:
        stem = Path(file_name).stem
        if not _SAFE_NAME.fullmatch(stem):
            return None
        destination = self._managed_dir / stem
        if destination.exists():
            return None
        destination.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
                root = destination.resolve()
                for member in archive.infolist():
                    target = (destination / member.filename).resolve()
                    if root not in target.parents and target != root:
                        raise ValueError("Skill zip 包含越界路径")
                archive.extractall(destination)
            skill_md = destination / "SKILL.md"
            if not skill_md.is_file():
                raise ValueError("Skill 包缺少 SKILL.md")
            spec = parse_frontmatter(skill_md)
            self.sync_filesystem_to_db()
            return {
                "name": spec.get("name", destination.name),
                "skillPath": str(destination),
                "description": spec.get("description", ""),
                "enabled": True,
                "fileName": file_name,
            }
        except Exception as exc:
            logger.warning("[SkillManager] 上传失败: %s", exc)
            shutil.rmtree(destination, ignore_errors=True)
            return None


# 仅供旧导入和无应用容器的离线工具使用；在线运行实例由 ApplicationContainer 持有。
skill_manager = SkillManager()
