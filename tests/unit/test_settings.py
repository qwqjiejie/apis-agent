from pathlib import Path

from app.config.settings import _get_env_file


def test_default_env_file_is_in_project_root(monkeypatch):
    monkeypatch.delenv("APIS_ENV_FILE", raising=False)

    assert _get_env_file() == Path(__file__).resolve().parents[2] / ".env"


def test_env_file_can_be_overridden(monkeypatch):
    monkeypatch.setenv("APIS_ENV_FILE", "/tmp/apis-agent-test.env")

    assert _get_env_file() == Path("/tmp/apis-agent-test.env")
