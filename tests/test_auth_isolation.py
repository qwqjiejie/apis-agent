from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.api.routes import file as file_routes
from app.api.routes import session as session_routes
from app.auth import generate_token
from app.tool import bash_tool


def test_v7_session_detail_and_delete_require_owner(monkeypatch):
    test_app = FastAPI()
    test_app.include_router(session_routes.router, prefix="/api/v1")
    deleted = []

    monkeypatch.setattr(
        session_routes.store,
        "get_session",
        lambda session_id: {"conversationId": session_id, "messages": []},
    )
    monkeypatch.setattr(
        session_routes.store,
        "get_session_owner",
        lambda session_id: "user_a",
    )
    monkeypatch.setattr(
        session_routes.store,
        "delete_session",
        lambda session_id: deleted.append(session_id) or True,
    )

    headers = {"Authorization": f"Bearer {generate_token('user_b')}"}
    with TestClient(test_app) as client:
        detail = client.post(
            "/api/v1/session/detail",
            json={"conversationId": "session_a"},
            headers=headers,
        )
        delete = client.post(
            "/api/v1/session/delete",
            json={"conversationId": "session_a"},
            headers=headers,
        )

    assert detail.json()["code"] == 403
    assert delete.json()["code"] == 403
    assert deleted == []


def test_v7_session_list_ignores_forged_body_user_id(monkeypatch):
    test_app = FastAPI()
    test_app.include_router(session_routes.router, prefix="/api/v1")
    observed = []

    def list_sessions(page, size, user_id):
        observed.append(user_id)
        return [], 0

    monkeypatch.setattr(session_routes.store, "list_sessions", list_sessions)
    headers = {"Authorization": f"Bearer {generate_token('user_b')}"}
    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/session/list",
            json={"pageNum": 1, "pageSize": 20, "userId": "user_a"},
            headers=headers,
        )

    assert response.json()["code"] == 200
    assert observed == ["user_b"]


def test_v7_file_upload_rejects_foreign_conversation(monkeypatch):
    test_app = FastAPI()
    test_app.include_router(file_routes.router, prefix="/api/v1")
    upload = AsyncMock(return_value={"fileId": "never"})
    monkeypatch.setattr(file_routes.file_service, "upload", upload)
    monkeypatch.setattr(
        file_routes.store,
        "get_session_owner",
        lambda session_id: "user_a",
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/file/upload",
            data={"conversationId": "session_a"},
            files={"file": ("test.txt", b"content", "text/plain")},
            headers={"Authorization": f"Bearer {generate_token('user_b')}"},
        )

    assert response.json()["code"] == 403
    upload.assert_not_awaited()


def test_v7_shell_confirmation_requires_request_owner(monkeypatch):
    class Event:
        called = False

        def set(self):
            self.called = True

    event = Event()
    monkeypatch.setitem(
        bash_tool._pending,
        "confirm_v7",
        {
            "event": event,
            "approved": False,
            "command": "rm protected",
            "user_id": "user_a",
        },
    )

    assert not bash_tool.resolve_confirmation("confirm_v7", True, "user_b")
    assert not event.called
    assert bash_tool.resolve_confirmation("confirm_v7", True, "user_a")
    assert event.called
