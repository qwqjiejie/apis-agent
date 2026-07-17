import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.agent.agent_factory import _build_llm, _build_middleware
from app.gateway.middleware import GatewayModelWrapper
from app.gateway.model_gateway import ModelGateway
from app.gateway.status_events import drain_gateway_status, gateway_status_queue
from app.gateway.types import ModelRole
from app.harness.dead_letter import DeadLetterQueue
from app.harness.task_context import PgTaskStore
from app.memory.semantic_memory import SemanticMemoryStore


class FakeLangGraphStore:
    def __init__(self):
        self.data = {}

    async def aput(self, namespace, key, value, index=None):
        self.data[(namespace, key)] = value

    async def aget(self, namespace, key, **kwargs):
        value = self.data.get((namespace, key))
        return SimpleNamespace(key=key, value=value) if value is not None else None

    async def asearch(self, namespace, *, filter=None, limit=10, **kwargs):
        items = [
            SimpleNamespace(key=key, value=value)
            for (item_namespace, key), value in self.data.items()
            if item_namespace[:len(namespace)] == namespace
        ]
        if filter:
            items = [
                item
                for item in items
                if all(item.value.get(name) == expected for name, expected in filter.items())
            ]
        return items[:limit]

    async def adelete(self, namespace, key):
        self.data.pop((namespace, key), None)


class BindableFakeChatModel(FakeListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


@pytest.mark.asyncio
async def test_v8_tool_retry_middleware_retries_three_times():
    middleware = next(
        item
        for item in _build_middleware()
        if item.__class__.__name__ == "ToolRetryMiddleware"
    )
    middleware.initial_delay = 0
    middleware.jitter = False
    calls = 0

    async def flaky_handler(_request):
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise RuntimeError("temporary tool failure")
        return ToolMessage(content="recovered", tool_call_id="call_v8")

    request = SimpleNamespace(
        tool=SimpleNamespace(name="flaky_tool"),
        tool_call={"id": "call_v8", "name": "flaky_tool", "args": {}},
    )
    result = await middleware.awrap_tool_call(request, flaky_handler)

    assert calls == 4
    assert result.content == "recovered"


@pytest.mark.asyncio
async def test_v9_gateway_falls_back_and_emits_status():
    gateway = ModelGateway()
    primary = FakeListChatModel(
        responses=["primary"],
        error_on_chunk_number=0,
    )
    fallback = FakeListChatModel(responses=["fallback-ok"])
    await gateway.register("primary", primary, is_primary=True)
    await gateway.register("fallback", fallback, is_primary=False)
    gateway.set_fallback(["fallback"])

    wrapper = _build_llm(gateway)
    assert isinstance(wrapper, GatewayModelWrapper)

    queue: asyncio.Queue[dict] = asyncio.Queue()
    parent_queue = gateway_status_queue.get()
    gateway_status_queue.set(queue)
    try:
        chunks = []
        async for chunk in wrapper.astream([HumanMessage(content="hello")]):
            chunks.append(str(chunk.content))
    finally:
        gateway_status_queue.set(parent_queue)

    assert "".join(chunks) == "fallback-ok"
    assert drain_gateway_status(queue) == [{
        "status": "fallback",
        "fromModel": "primary",
        "toModel": "fallback",
        "reason": "",
    }]
    assert gateway.get_all_status()["primary"]["health"]["total_errors"] == 1


@pytest.mark.asyncio
async def test_v9_gateway_wrapper_supports_deepagent_tool_binding():
    @tool
    def echo(value: str) -> str:
        """Echo a value."""
        return value

    gateway = ModelGateway()
    await gateway.register(
        "bindable",
        BindableFakeChatModel(responses=["tool-binding-ok"]),
        is_primary=True,
    )
    graph = create_agent(model=_build_llm(gateway), tools=[echo])

    text = ""
    async for event in graph.astream_events(
        {"messages": [{"role": "user", "content": "hello"}]},
        version="v2",
    ):
        if event.get("event") == "on_chat_model_stream":
            text += str(event["data"]["chunk"].content)

    assert text == "tool-binding-ok"


@pytest.mark.asyncio
async def test_v10_semantic_memory_is_persistent_and_user_isolated(monkeypatch):
    backend = FakeLangGraphStore()

    def embed(text):
        return [1.0, 0.0] if "青竹" in text else [0.0, 1.0]

    first = SemanticMemoryStore(threshold=0.9)
    first.configure(backend)
    monkeypatch.setattr(first, "_embed_text", embed)
    await first.add("user_a", "我的代号是青竹", "已记住")
    await first.add("user_b", "我喜欢红色", "已记住")

    restarted = SemanticMemoryStore(threshold=0.9)
    restarted.configure(backend)
    monkeypatch.setattr(restarted, "_embed_text", embed)

    user_a = await restarted.search("user_a", "青竹代号")
    user_b = await restarted.search("user_b", "青竹代号")
    assert [item["question"] for item in user_a] == ["我的代号是青竹"]
    assert user_b == []


@pytest.mark.asyncio
async def test_c2_journal_persists_across_repository_instances():
    backend = FakeLangGraphStore()
    writer = PgTaskStore(backend)
    reader = PgTaskStore(backend)

    await writer.append_journal("task_journal", "created", "created")
    await writer.append_journal("task_journal", "completed", "completed")

    entries = await reader.read_journal("task_journal")
    assert [entry.step for entry in entries] == [1, 2]
    assert [entry.event for entry in entries] == ["created", "completed"]


@pytest.mark.asyncio
async def test_c3_dead_letter_survives_queue_restart_and_retries():
    backend = FakeLangGraphStore()
    first_queue = DeadLetterQueue(backend)
    record_id = await first_queue.enqueue(
        "write_result",
        {"task_id": "task_dlq", "value": "done"},
        error="database unavailable",
    )

    retried = []
    restarted_queue = DeadLetterQueue(backend)

    async def handler(args):
        retried.append(args)

    restarted_queue.register_retry_handler("write_result", handler)
    assert [item["id"] for item in await restarted_queue.list_pending()] == [record_id]
    assert await restarted_queue.retry_all() == 1
    assert retried == [{"task_id": "task_dlq", "value": "done"}]
    assert await restarted_queue.list_pending() == []
