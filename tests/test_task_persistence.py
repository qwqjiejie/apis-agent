import asyncio
import json
import re
import time
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.executor_agent import ExecutorAgent, _build_resume_command
from app.api.routes import agent as agent_routes
from app.harness.event_bus import EventBus
from app.harness.task_context import PgTaskStore, TaskSnapshot, TaskStatus
from app.harness import task_executor as task_executor_module
from app.harness.task_executor import TaskExecutor
from app.tool.task_tools import create_background_task


class FakeLangGraphStore:
    def __init__(self):
        self.data = {}

    async def aput(self, namespace, key, value, index=None):
        self.data[(namespace, key)] = value

    async def aget(self, namespace, key, **kwargs):
        value = self.data.get((namespace, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def asearch(self, namespace, *, filter=None, limit=10, **kwargs):
        values = [
            (key, value)
            for (item_namespace, key), value in self.data.items()
            if item_namespace[:len(namespace)] == namespace
        ]
        if filter:
            values = [
                (key, value)
                for key, value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return [
            SimpleNamespace(key=key, value=value)
            for key, value in values[:limit]
        ]

    async def adelete(self, namespace, key):
        self.data.pop((namespace, key), None)


class BlockingExecutorGraph:
    async def astream_events(self, inputs, *, version, config):
        await asyncio.Event().wait()
        if False:
            yield inputs, version, config


class BackgroundTaskTriageAgent:
    async def astream_events(self, inputs, *, version, config):
        yield {"event": "on_tool_start", "name": "create_background_task"}
        result = await create_background_task.ainvoke({
            "goal": "生成跨部门研究报告",
            "plan": "检索、分析、汇总",
        })
        yield {"event": "on_tool_end", "name": "create_background_task"}
        yield {
            "event": "on_chat_model_stream",
            "data": {
                "chunk": SimpleNamespace(content=result, additional_kwargs={}),
            },
        }


@pytest.mark.asyncio
async def test_pg_task_store_persists_across_repository_instances():
    backend = FakeLangGraphStore()
    writer = PgTaskStore(backend)
    reader = PgTaskStore(backend)
    snapshot = TaskSnapshot(
        task_id="task_pg_roundtrip",
        conversation_id="session_pg",
        query="持久化任务",
        status=TaskStatus.EXECUTING,
    )

    await writer.save(snapshot)

    loaded = await reader.get(snapshot.task_id)
    assert loaded is not None
    assert loaded.to_dict() == snapshot.to_dict()
    assert [task.task_id for task in await reader.list_by_status(TaskStatus.EXECUTING)] == [snapshot.task_id]
    assert [task.task_id for task in await reader.list_by_session("session_pg")] == [snapshot.task_id]

    await reader.delete(snapshot.task_id)
    assert await writer.get(snapshot.task_id) is None


@pytest.mark.asyncio
async def test_v2_background_task_is_queryable_and_persisted():
    backend = FakeLangGraphStore()
    repository = PgTaskStore(backend)
    executor = TaskExecutor(store=repository, event_bus_instance=EventBus())
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(_snapshot):
        started.set()
        await release.wait()
        yield {
            "event": "message",
            "data": json.dumps({"type": "text", "content": "后台任务完成"}, ensure_ascii=False),
        }

    task_id = await executor.submit("session_v2", "复杂任务", execute)
    await asyncio.wait_for(started.wait(), timeout=1)

    executing = await executor.get_status(task_id)
    assert executing is not None
    assert executing["status"] == TaskStatus.EXECUTING.value

    release.set()
    for _ in range(100):
        completed = await executor.get_status(task_id)
        if completed and completed["status"] == TaskStatus.COMPLETED.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("后台任务未在预期时间内完成")

    assert completed["result"] == "后台任务完成"
    persisted = await PgTaskStore(backend).get(task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.COMPLETED
    assert persisted.result == "后台任务完成"


@pytest.mark.asyncio
async def test_v3_langgraph_interrupt_is_persisted_as_waiting_human(monkeypatch):
    class ApprovalState(TypedDict):
        messages: list

    def request_approval(state):
        interrupt({
            "action_requests": [{
                "name": "request_approval",
                "args": {
                    "approval_id": "approval_v3",
                    "description": "批准发布报告",
                },
                "description": "批准发布报告",
            }],
            "review_configs": [{
                "action_name": "request_approval",
                "allowed_decisions": ["approve", "reject"],
            }],
        })
        return {"messages": state["messages"]}

    builder = StateGraph(ApprovalState)
    builder.add_node("request_approval", request_approval)
    builder.add_edge(START, "request_approval")
    builder.add_edge("request_approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    backend = FakeLangGraphStore()
    repository = PgTaskStore(backend)
    executor = TaskExecutor(store=repository, event_bus_instance=EventBus())
    executor.executor_agent = graph
    monkeypatch.setattr(task_executor_module, "task_executor", executor)

    async def execute(snapshot):
        wrapper = ExecutorAgent(snapshot)
        async for event in wrapper.run():
            yield event

    task_id = await executor.submit("session_v3", "需要审批的任务", execute)

    for _ in range(100):
        waiting = await executor.get_status(task_id)
        if waiting and waiting["status"] == TaskStatus.WAITING_HUMAN.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("LangGraph interrupt 未持久化为 waiting_human")

    assert waiting["approvalId"] == "approval_v3"
    assert waiting["interruptInfo"]["action_requests"][0]["name"] == "request_approval"
    persisted = await PgTaskStore(backend).get(task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.WAITING_HUMAN
    assert persisted.approval_id == "approval_v3"
    assert persisted.interrupt_info is not None
    assert persisted.interrupt_info["action_requests"][0]["name"] == "request_approval"
    assert persisted.interrupt_info["interrupts"][0]["id"]


@pytest.mark.asyncio
async def test_v4_resume_endpoint_continues_same_checkpoint(monkeypatch):
    class ApprovalState(TypedDict):
        messages: list

    resumed_values = []
    release = asyncio.Event()

    async def request_approval(state):
        decision = interrupt({
            "action_requests": [{
                "name": "request_approval",
                "args": {
                    "approval_id": "approval_v4",
                    "description": "批准发布报告",
                },
                "description": "批准发布报告",
            }],
            "review_configs": [{
                "action_name": "request_approval",
                "allowed_decisions": ["approve", "reject"],
            }],
        })
        resumed_values.append(decision)
        await release.wait()
        return {"messages": state["messages"]}

    builder = StateGraph(ApprovalState)
    builder.add_node("request_approval", request_approval)
    builder.add_edge(START, "request_approval")
    builder.add_edge("request_approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    repository = PgTaskStore(FakeLangGraphStore())
    executor = TaskExecutor(store=repository, event_bus_instance=EventBus())
    executor.executor_agent = graph
    monkeypatch.setattr(task_executor_module, "task_executor", executor)
    monkeypatch.setattr(agent_routes, "task_executor", executor)

    async def execute(snapshot):
        wrapper = ExecutorAgent(snapshot)
        async for event in wrapper.run():
            yield event

    task_id = await executor.submit(
        "session_v4",
        "需要审批后继续的任务",
        execute,
        user_id="anon_v4-test-user",
    )

    for _ in range(100):
        waiting = await executor.get_status(task_id)
        if waiting and waiting["status"] == TaskStatus.WAITING_HUMAN.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("任务未进入 waiting_human")

    response = await agent_routes.task_resume(
        agent_routes.TaskResumeRequest(taskId=task_id, action="approved"),
        Request({
            "type": "http",
            "headers": [(b"x-anonymous-id", b"v4-test-user")],
        }),
    )
    assert json.loads(response.body)["code"] == 200

    executing = await executor.get_status(task_id)
    assert executing is not None
    assert executing["status"] == TaskStatus.EXECUTING.value
    assert executing["approvalId"] == ""
    assert executing["interruptInfo"] is None

    for _ in range(100):
        if resumed_values:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("checkpoint 未收到 resume 数据")

    assert resumed_values == [{"decisions": [{"type": "approve"}]}]
    release.set()

    for _ in range(100):
        completed = await executor.get_status(task_id)
        if completed and completed["status"] == TaskStatus.COMPLETED.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("审批后任务未完成")

    persisted = await repository.get(task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.COMPLETED
    assert persisted.approval_id == ""
    assert persisted.interrupt_info is None


def test_v4_rejection_maps_to_deepagents_decision():
    command = _build_resume_command({
        "action": "rejected",
        "comment": "审批材料不完整",
    })

    assert command.resume == {
        "decisions": [{
            "type": "reject",
            "message": "审批材料不完整",
        }],
    }


@pytest.mark.asyncio
async def test_v6_waiting_task_resumes_after_executor_restart(monkeypatch):
    class ApprovalState(TypedDict):
        messages: list

    resumed_values = []

    def request_approval(state):
        decision = interrupt({
            "action_requests": [{
                "name": "request_approval",
                "args": {"approval_id": "approval_v6"},
                "description": "重启恢复审批",
            }],
            "review_configs": [{
                "action_name": "request_approval",
                "allowed_decisions": ["approve", "reject"],
            }],
        })
        resumed_values.append(decision)
        return {"messages": state["messages"]}

    builder = StateGraph(ApprovalState)
    builder.add_node("request_approval", request_approval)
    builder.add_edge(START, "request_approval")
    builder.add_edge("request_approval", END)
    checkpointer = InMemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    backend = FakeLangGraphStore()
    repository = PgTaskStore(backend)
    first_executor = TaskExecutor(store=repository, event_bus_instance=EventBus())
    first_executor.executor_agent = graph
    monkeypatch.setattr(task_executor_module, "task_executor", first_executor)

    async def execute(snapshot):
        wrapper = ExecutorAgent(snapshot)
        async for event in wrapper.run():
            yield event

    task_id = await first_executor.submit(
        "session_v6",
        "重启后继续审批",
        execute,
        user_id="user_v6",
    )
    for _ in range(100):
        waiting = await first_executor.get_status(task_id, user_id="user_v6")
        if waiting and waiting["status"] == TaskStatus.WAITING_HUMAN.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("重启前任务未挂起")

    restarted_executor = TaskExecutor(
        store=PgTaskStore(backend),
        event_bus_instance=EventBus(),
    )
    restarted_executor.executor_agent = graph
    monkeypatch.setattr(task_executor_module, "task_executor", restarted_executor)

    assert await restarted_executor.recover_tasks() == 1
    assert not await restarted_executor.resume(
        task_id,
        {"action": "approved"},
        user_id="other_user",
    )
    assert await restarted_executor.resume(
        task_id,
        {"action": "approved"},
        user_id="user_v6",
    )

    for _ in range(100):
        completed = await restarted_executor.get_status(task_id, user_id="user_v6")
        if completed and completed["status"] == TaskStatus.COMPLETED.value:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("重启后的 waiting_human 任务未完成")

    assert resumed_values == [{"decisions": [{"type": "approve"}]}]
    journal = await restarted_executor.read_journal(task_id)
    assert [entry["event"] for entry in journal] == [
        "created",
        "executing",
        "approval_requested",
        "decision",
        "completed",
    ]


def test_v2_chat_creates_background_task_and_status_is_queryable(monkeypatch):
    repository = PgTaskStore(FakeLangGraphStore())
    executor = TaskExecutor(store=repository, event_bus_instance=EventBus())
    executor.executor_agent = BlockingExecutorGraph()
    monkeypatch.setattr(task_executor_module, "task_executor", executor)
    monkeypatch.setattr(agent_routes, "task_executor", executor)

    test_app = FastAPI()
    test_app.include_router(agent_routes.router, prefix="/api/v1")
    test_app.state.agent = BackgroundTaskTriageAgent()

    monkeypatch.setattr(agent_routes, "_save_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_routes, "_generate_title", AsyncMock(return_value="研究报告"))
    monkeypatch.setattr(agent_routes, "_update_session_title", lambda *args: None)
    monkeypatch.setattr(agent_routes.semantic_memory, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_routes.semantic_memory, "add", AsyncMock())
    monkeypatch.setattr(agent_routes.online_eval, "record", lambda record: None)

    with TestClient(test_app) as client:
        headers = {"X-Anonymous-Id": "v2-test-user"}
        response = client.post(
            "/api/v1/agent/chat",
            json={"message": "请完成一份需要多个专家协作的复杂研究报告"},
            headers=headers,
        )
        match = re.search(r"task_[0-9a-f]{12}", response.text)
        assert match is not None
        task_id = match.group()

        status_response = client.post(
            "/api/v1/agent/task/status",
            json={"taskId": task_id},
            headers=headers,
        )
        assert status_response.json()["data"]["status"] == TaskStatus.EXECUTING.value

        cancel_response = client.post(
            "/api/v1/agent/task/cancel",
            json={"taskId": task_id},
            headers=headers,
        )
        assert cancel_response.json()["code"] == 200

        for _ in range(100):
            status_response = client.post(
                "/api/v1/agent/task/status",
                json={"taskId": task_id},
                headers=headers,
            )
            if status_response.json()["data"]["status"] == TaskStatus.CANCELLED.value:
                break
            time.sleep(0.01)
        else:
            pytest.fail("后台任务取消状态未持久化")
