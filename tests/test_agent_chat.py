import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent as agent_routes


class FakeStreamingAgent:
    def __init__(self):
        self.inputs = None
        self.config = None

    async def astream_events(self, inputs, *, version, config):
        self.inputs = inputs
        self.config = config
        assert version == "v2"
        yield {
            "event": "on_chat_model_stream",
            "data": {
                "chunk": SimpleNamespace(content="今天", additional_kwargs={}),
            },
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {
                "chunk": SimpleNamespace(
                    content=[{"type": "text", "text": "天气晴朗"}],
                    additional_kwargs={},
                ),
            },
        }


def _sse_data(response) -> list[str]:
    return [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_v1_simple_chat_streams_text_and_complete(monkeypatch):
    fake_agent = FakeStreamingAgent()
    saved_messages = []

    test_app = FastAPI()
    test_app.include_router(agent_routes.router, prefix="/api/v1")
    test_app.state.agent = fake_agent

    monkeypatch.setattr(
        agent_routes,
        "_save_session",
        lambda *args, **kwargs: saved_messages.append((args, kwargs)),
    )
    monkeypatch.setattr(agent_routes, "_generate_title", AsyncMock(return_value="天气"))
    monkeypatch.setattr(agent_routes, "_update_session_title", lambda *args: None)
    monkeypatch.setattr(agent_routes.semantic_memory, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_routes.semantic_memory, "add", AsyncMock())
    monkeypatch.setattr(agent_routes.online_eval, "record", lambda record: None)

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/agent/chat",
            json={"message": "今天天气"},
            headers={"X-Anonymous-Id": "v1-test-user"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-session-id"]

    data = _sse_data(response)
    events = [json.loads(item) for item in data if item != "[DONE]"]
    assert [event["type"] for event in events] == ["text", "text", "complete"]
    assert "".join(event.get("content", "") for event in events) == "今天天气晴朗"
    assert data[-1] == "[DONE]"
    assert all(event["type"] != "error" for event in events)

    assert fake_agent.inputs == {
        "messages": [{"role": "user", "content": "今天天气"}],
    }
    assert fake_agent.config["recursion_limit"] == 100
    assert "recursion_limit" not in fake_agent.config["configurable"]
    assert fake_agent.config["configurable"]["user_id"] == "anon_v1-test-user"
    assert len(saved_messages) == 1
