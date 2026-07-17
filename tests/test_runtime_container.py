import json
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.api.routes.agent import (
    GatewaySwitchRequest,
    chat_router,
    gateway_status,
    gateway_switch,
    router as agent_router,
)
from app.bootstrap.container import (
    ApplicationContainer,
    clear_application_container,
    get_application_container,
    set_application_container,
)
from app.common.llm import build_llm
from app.gateway.middleware import GatewayModelWrapper
from app.gateway.model_gateway import ModelGateway
from app.harness.dead_letter import DeadLetterQueue
from app.harness.event_bus import EventBus
from app.harness.task_context import ChatContext, TaskContextManager
from app.harness.task_executor import TaskExecutor
from app.tool.task_tools import get_task_status


@pytest.mark.asyncio
async def test_build_llm_uses_the_registered_runtime_gateway():
    gateway = ModelGateway()
    await gateway.register(
        "runtime-model",
        FakeListChatModel(responses=["ok"]),
        is_primary=True,
    )
    container = ApplicationContainer(model_gateway=gateway)
    set_application_container(container)

    try:
        llm = build_llm()
        assert isinstance(llm, GatewayModelWrapper)
        assert llm._gateway is gateway
        assert get_application_container() is container
    finally:
        clear_application_container(container)

    assert get_application_container(required=False) is None


@pytest.mark.asyncio
async def test_gateway_admin_endpoints_operate_on_the_request_container():
    gateway = ModelGateway()
    await gateway.register(
        "primary",
        FakeListChatModel(responses=["primary"]),
        is_primary=True,
    )
    await gateway.register(
        "fallback",
        FakeListChatModel(responses=["fallback"]),
        is_primary=False,
    )
    test_app = FastAPI()
    test_app.state.container = ApplicationContainer(model_gateway=gateway)
    request = Request({"type": "http", "headers": [], "app": test_app})

    before = json.loads((await gateway_status(request)).body)
    assert before["data"]["primary"]["active"] is True

    switched = json.loads((await gateway_switch(
        GatewaySwitchRequest(modelName="fallback"),
        request,
    )).body)

    assert switched["code"] == 200
    assert gateway.get_active_name() == "fallback"
    after = json.loads((await gateway_status(request)).body)
    assert after["data"]["fallback"]["active"] is True


@pytest.mark.asyncio
async def test_task_tool_uses_executor_and_context_from_runtime_container():
    executor = SimpleNamespace(
        get_status=AsyncMock(return_value={
            "taskId": "task_runtime",
            "status": "completed",
            "query": "container task",
            "result": "done",
            "error": "",
        }),
    )
    context_manager = TaskContextManager()
    context_manager.set(ChatContext(user_id="runtime-user"))
    container = ApplicationContainer(
        model_gateway=ModelGateway(),
        task_executor=executor,
        context_manager=context_manager,
    )
    set_application_container(container)

    try:
        result = await get_task_status.ainvoke({"task_id": "task_runtime"})
    finally:
        clear_application_container(container)

    assert "completed" in result
    executor.get_status.assert_awaited_once_with(
        "task_runtime",
        user_id="runtime-user",
    )


@pytest.mark.asyncio
async def test_task_executor_uses_its_injected_dead_letter_queue():
    class FailingTaskStore:
        async def save(self, _snapshot):
            raise RuntimeError("snapshot unavailable")

        async def append_journal(self, *_args, **_kwargs):
            raise RuntimeError("journal unavailable")

    event_bus = EventBus()
    context_manager = TaskContextManager()
    dead_letter_queue = DeadLetterQueue()
    executor = TaskExecutor(
        store=FailingTaskStore(),
        event_bus_instance=event_bus,
        context_manager_instance=context_manager,
        dead_letter_queue_instance=dead_letter_queue,
    )

    snapshot = SimpleNamespace(
        task_id="task_failed_write",
        to_dict=lambda: {"task_id": "task_failed_write"},
    )
    await executor._save_snapshot(snapshot)
    await executor._write_journal("task_failed_write", "created", "created")

    pending = await dead_letter_queue.list_pending()
    assert {item["operation_type"] for item in pending} == {
        "task_snapshot_save",
        "task_journal_append",
    }
    assert executor.event_bus is event_bus
    assert executor.context_manager is context_manager
    assert executor.dead_letter_queue is dead_letter_queue


def test_file_and_vector_store_construction_has_no_external_client_side_effects(
    monkeypatch,
):
    file_service_module = importlib.import_module("app.modules.documents.service")
    vector_store_module = importlib.import_module("app.storage.vector_store")
    created_clients = []

    class ExternalClient:
        def __init__(self, *_args, **_kwargs):
            created_clients.append(self)

    monkeypatch.setattr(file_service_module, "Minio", ExternalClient)
    monkeypatch.setattr(vector_store_module, "MilvusClient", ExternalClient)

    vector_store = vector_store_module.VectorStore()
    file_service = file_service_module.FileService(
        vector_store_instance=vector_store,
    )

    assert created_clients == []
    assert vector_store.ready is False
    assert file_service._minio is None


def test_agent_route_split_preserves_public_openapi_contract():
    test_app = FastAPI()
    test_app.include_router(agent_router, prefix="/api/v1")
    test_app.include_router(chat_router, prefix="/api/v1")
    paths = test_app.openapi()["paths"]

    expected_paths = {
        "/api/v1/chat",
        "/api/v1/agent/chat",
        "/api/v1/agent/task/status",
        "/api/v1/agent/task/stream",
        "/api/v1/agent/task/cancel",
        "/api/v1/agent/task/resume",
        "/api/v1/agent/task/list",
        "/api/v1/agent/admin/gateway",
        "/api/v1/agent/admin/gateway/switch",
        "/api/v1/agent/pptx/download",
        "/api/v1/agent/stop",
        "/api/v1/agent/shell/confirm",
        "/api/v1/agent/feedback",
    }
    assert set(paths) == expected_paths
    assert paths["/api/v1/agent/chat"]["post"]["deprecated"] is True
    assert "deprecated" not in paths["/api/v1/chat"]["post"]


def test_task_stream_preserves_sse_event_contract():
    executor = SimpleNamespace(
        get_status=AsyncMock(return_value={
            "status": "completed",
            "result": "task result",
        }),
    )
    test_app = FastAPI()
    test_app.include_router(agent_router, prefix="/api/v1")
    test_app.state.container = ApplicationContainer(
        model_gateway=ModelGateway(),
        task_executor=executor,
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/agent/task/stream",
            json={"taskId": "task_contract"},
            headers={"X-Anonymous-Id": "sse-contract"},
        )

    data = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    events = [json.loads(item) for item in data if item != "[DONE]"]
    assert events == [
        {"type": "text", "content": "task result"},
        {"type": "complete"},
    ]
    assert data[-1] == "[DONE]"


def test_document_module_keeps_legacy_imports_compatible():
    legacy_service = importlib.import_module("app.service.file_service")
    module_service = importlib.import_module("app.modules.documents.service")
    legacy_status = importlib.import_module("app.document.document_status")
    module_status = importlib.import_module("app.modules.documents.status")
    legacy_retrieval = importlib.import_module("app.rag.retrieval_pipeline")
    module_retrieval = importlib.import_module("app.modules.documents.retrieval")

    assert legacy_service.FileService is module_service.FileService
    assert legacy_status.DocumentStatus is module_status.DocumentStatus
    assert legacy_retrieval.RetrievalPipeline is module_retrieval.RetrievalPipeline
