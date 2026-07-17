"""兼容导出；新代码使用 :mod:`app.modules.skills.manager`。"""

from app.modules.skills.manager import (
    SKILLS_DIR,
    SYNC_INTERVAL_SEC,
    SkillManager,
    skill_manager,
)

__all__ = ["SKILLS_DIR", "SYNC_INTERVAL_SEC", "SkillManager", "skill_manager"]
