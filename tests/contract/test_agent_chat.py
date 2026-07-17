import json
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from app.api.routes import chat_routes
from app.bootstrap.container import ApplicationContainer
from app.gateway.model_gateway import ModelGateway
from app.modules.identity.auth import generate_token
from app.modules.tasks.context import TaskContextManager


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


class RecordingChatModel(FakeListChatModel):
    """记录每次模型调用真正收到的 LangGraph 消息。"""

    received_messages: ClassVar[list[list[tuple[str, str]]]] = []

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.received_messages.append([
            (message.type, str(message.content))
            for message in messages
        ])
        yield from super()._stream(messages, stop, run_manager, **kwargs)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.received_messages.append([
            (message.type, str(message.content))
            for message in messages
        ])
        async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
            yield chunk


def _sse_data(response) -> list[str]:
    return [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _attach_runtime(test_app: FastAPI, agent):
    memory = SimpleNamespace(
        search=AsyncMock(return_value=[]),
        add=AsyncMock(),
        build_context_injection=lambda _memories: "",
    )
    executor = SimpleNamespace(
        list_tasks_by_session=AsyncMock(return_value=[]),
    )
    runtime = ApplicationContainer(
        model_gateway=ModelGateway(),
        agent=agent,
        semantic_memory=memory,
        task_executor=executor,
        context_manager=TaskContextManager(),
    )
    test_app.state.container = runtime
    return runtime


def test_v1_simple_chat_streams_text_and_complete(monkeypatch):
    fake_agent = FakeStreamingAgent()
    saved_messages = []

    test_app = FastAPI()
    test_app.include_router(chat_routes.router, prefix="/api/v1")
    _attach_runtime(test_app, fake_agent)

    monkeypatch.setattr(
        chat_routes.chat_service,
        "save_session",
        lambda *args, **kwargs: saved_messages.append((args, kwargs)),
    )
    monkeypatch.setattr(
        chat_routes.chat_service,
        "generate_title",
        AsyncMock(return_value="天气"),
    )
    monkeypatch.setattr(chat_routes.chat_service, "update_session_title", lambda *args: None)
    monkeypatch.setattr(chat_routes.online_eval, "record", lambda record: None)

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/chat",
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
    assert saved_messages[0][0][3] == "anon_v1-test-user"


def test_v5_same_conversation_uses_checkpointer_history(monkeypatch):
    RecordingChatModel.received_messages.clear()
    model = RecordingChatModel(responses=[
        "我会记住代号青竹",
        "你上一轮告诉我的代号是青竹",
    ])
    graph = create_agent(
        model=model,
        tools=[],
        checkpointer=InMemorySaver(),
    )
    saved_messages = []

    test_app = FastAPI()
    test_app.include_router(chat_routes.router, prefix="/api/v1")
    _attach_runtime(test_app, graph)

    monkeypatch.setattr(
        chat_routes.chat_service,
        "save_session",
        lambda *args, **kwargs: saved_messages.append((args, kwargs)),
    )
    monkeypatch.setattr(
        chat_routes.chat_service,
        "generate_title",
        AsyncMock(return_value="代号测试"),
    )
    monkeypatch.setattr(chat_routes.chat_service, "update_session_title", lambda *args: None)
    monkeypatch.setattr(
        chat_routes.store,
        "get_session_owner",
        lambda session_id: "anon_v5-test-user",
    )
    monkeypatch.setattr(chat_routes.store, "touch_last_active", lambda session_id: None)
    monkeypatch.setattr(chat_routes.online_eval, "record", lambda record: None)

    headers = {"X-Anonymous-Id": "v5-test-user"}
    with TestClient(test_app) as client:
        first = client.post(
            "/api/v1/chat",
            json={"message": "请记住我的代号是青竹"},
            headers=headers,
        )
        conversation_id = first.headers["x-session-id"]

        second = client.post(
            "/api/v1/chat",
            json={
                "message": "我上一轮说的代号是什么？",
                "conversationId": conversation_id,
            },
            headers=headers,
        )

    first_text = "".join(
        event.get("content", "")
        for item in _sse_data(first)
        if item != "[DONE]"
        for event in [json.loads(item)]
        if event["type"] == "text"
    )
    second_text = "".join(
        event.get("content", "")
        for item in _sse_data(second)
        if item != "[DONE]"
        for event in [json.loads(item)]
        if event["type"] == "text"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["x-session-id"] == conversation_id
    assert first_text == "我会记住代号青竹"
    assert second_text == "你上一轮告诉我的代号是青竹"
    assert RecordingChatModel.received_messages == [
        [("human", "请记住我的代号是青竹")],
        [
            ("human", "请记住我的代号是青竹"),
            ("ai", "我会记住代号青竹"),
            ("human", "我上一轮说的代号是什么？"),
        ],
    ]
    checkpoint = graph.get_state({
        "configurable": {"thread_id": conversation_id},
    })
    assert [
        (message.type, str(message.content))
        for message in checkpoint.values["messages"]
    ] == [
        ("human", "请记住我的代号是青竹"),
        ("ai", "我会记住代号青竹"),
        ("human", "我上一轮说的代号是什么？"),
        ("ai", "你上一轮告诉我的代号是青竹"),
    ]
    assert [call[0][0] for call in saved_messages] == [
        conversation_id,
        conversation_id,
    ]


def test_v7_user_cannot_access_another_users_conversation(monkeypatch):
    fake_agent = FakeStreamingAgent()
    test_app = FastAPI()
    test_app.include_router(chat_routes.router, prefix="/api/v1")
    _attach_runtime(test_app, fake_agent)

    monkeypatch.setattr(
        chat_routes.store,
        "get_session_owner",
        lambda session_id: "user_a",
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "message": "读取上一轮内容",
                "conversationId": "session_owned_by_a",
            },
            headers={"Authorization": f"Bearer {generate_token('user_b')}"},
        )

    assert response.json()["code"] == 403
    assert fake_agent.inputs is None
