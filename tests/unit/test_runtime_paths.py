import io
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.modules.skills.manager import SkillManager


def build_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="test-key",
        data_dir=str(tmp_path),
        **overrides,
    )


def test_runtime_paths_resolve_under_configured_data_dir(tmp_path):
    settings = build_settings(tmp_path)

    assert settings.upload_path == tmp_path / "uploads"
    assert settings.managed_skills_path == tmp_path / "skills"
    assert settings.artifacts_path == tmp_path / "artifacts"
    assert settings.evaluation_results_path == tmp_path / "evaluations"


def test_production_requires_stable_secrets(tmp_path):
    with pytest.raises(ValidationError, match="jwt_secret"):
        build_settings(tmp_path, app_env="production", jwt_secret="", pg_password="")


def test_skill_upload_writes_only_to_managed_directory(tmp_path):
    bundled = tmp_path / "bundled"
    managed = tmp_path / "managed"
    manager = SkillManager(bundled_dir=bundled, managed_dir=managed)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "SKILL.md",
            "---\nname: uploaded\ndescription: test\n---\nbody\n",
        )

    result = manager.upload_zip(archive.getvalue(), "uploaded.zip")

    assert result is not None
    assert (managed / "uploaded" / "SKILL.md").is_file()
    assert not bundled.exists()


def test_skill_upload_rejects_zip_path_escape(tmp_path):
    manager = SkillManager(
        bundled_dir=tmp_path / "bundled",
        managed_dir=tmp_path / "managed",
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escaped.txt", "bad")
        output.writestr("SKILL.md", "---\nname: unsafe\n---\n")

    assert manager.upload_zip(archive.getvalue(), "unsafe.zip") is None
    assert not (tmp_path / "escaped.txt").exists()
