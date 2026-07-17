from types import SimpleNamespace

import pytest

from app.infrastructure.postgres import database
from app.infrastructure.reliability import retry_async, retry_sync


class FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_session_scope_commits_and_closes(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(database, "new_session", lambda: session)

    with database.session_scope() as current:
        assert current is session

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


def test_session_scope_rolls_back_and_closes(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(database, "new_session", lambda: session)

    with pytest.raises(RuntimeError, match="failed"):
        with database.session_scope():
            raise RuntimeError("failed")

    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_retry_sync_is_bounded_and_returns_success():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary")
        return "ok"

    assert retry_sync(operation, attempts=3, delay_seconds=0) == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_async_is_bounded_and_returns_success():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise ConnectionError("temporary")
        return "ok"

    assert await retry_async(operation, attempts=2, delay_seconds=0) == "ok"
    assert calls == 2


def test_milvus_health_check_degrades_when_not_configured(monkeypatch):
    from app.infrastructure.milvus import vector_store as module

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(milvus_host=""),
    )
    result = module.VectorStore().health_check()

    assert result.available is False
    assert result.detail == "not configured"


def test_minio_factory_degrades_when_not_configured():
    from app.config.settings import Settings
    from app.infrastructure.minio.client import create_minio_client

    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        minio_host="",
    )

    assert create_minio_client(settings) is None
