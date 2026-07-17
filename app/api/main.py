import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.routes.agent import chat_router, router as agent_router
from app.api.routes.session import router as session_router
from app.api.routes.file import router as file_router
from app.api.routes.skill_routes import router as skill_router
from app.api.routes.auth_routes import router as auth_router
from app.common.exceptions import ApisAgentError, InfrastructureError, ValidationError
from app.common.logger import logger
from app.common.trace_context import generate_trace_id, set_trace_context
from app.config.settings import get_settings

_rebuild_lock = asyncio.Lock()
_last_rebuild_time: float = 0.0
_REBUILD_COOLDOWN: float = 3.0


async def _rebuild_agents(app: FastAPI):
    global _last_rebuild_time
    async with _rebuild_lock:
        now = time.monotonic()
        if now - _last_rebuild_time < _REBUILD_COOLDOWN:
            return
        await _do_rebuild(app)


async def _do_rebuild(app: FastAPI):
    from app.agent.agent_factory import (
        create_triage_agent, create_executor_agent,
        _build_subagents_from_specialists, _build_external_tools,
    )
    from app.prompt.triage_prompt import build_triage_prompt
    from app.prompt.executor_prompt import build_executor_prompt
    from app.tool import TOOL_REGISTRY

    subagents = _build_subagents_from_specialists()
    triage_prompt = build_triage_prompt()
    executor_prompt = build_executor_prompt()

    executor_tools = [TOOL_REGISTRY[n] for n in ("request_approval", "read_task_journal") if n in TOOL_REGISTRY]
    gateway = getattr(app.state, "model_gateway", None)

    new_triage = await create_triage_agent(
        system_prompt=triage_prompt, gateway=gateway, subagents=subagents,
        checkpointer=getattr(app.state, "checkpointer", None),
        store=getattr(app.state, "store", None),
    )
    new_executor = await create_executor_agent(
        system_prompt=executor_prompt, gateway=gateway, subagents=subagents,
        checkpointer=getattr(app.state, "checkpointer", None),
        store=getattr(app.state, "store", None),
        interrupt_on={"request_approval": True},
    )

    app.state.agent = new_triage
    app.state.executor_agent = new_executor
    app.state.specialist_subagents = subagents
    if hasattr(app.state, "task_executor"):
        app.state.task_executor.executor_agent = new_executor
    _last_rebuild_time = time.monotonic()
    logger.info("[HotReload] Agent 重建完成 (subagents=%d)", len(subagents))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()

    # ── 1. 模型网关 ────────────────────────────────
    from app.gateway.model_gateway import ModelGateway
    from app.common.llm import _create_raw_llm

    gateway = ModelGateway()
    llm = _create_raw_llm()
    await gateway.register(s.llm_model, llm, is_primary=True)
    if s.fallback_model:
        fl = _create_raw_llm()
        fl.model_name = s.fallback_model
        await gateway.register(s.fallback_model, fl, is_primary=False)
        gateway.set_fallback([s.fallback_model])
    await gateway.start_probe(s.gateway_health_probe_interval_sec)
    app.state.model_gateway = gateway

    # ── 2. PG Store (checkpointer + store) ─────────
    from app.stores.pg_store import pg_store_manager
    app.state.checkpointer = None
    app.state.store = None
    if await pg_store_manager.initialize():
        app.state.checkpointer = pg_store_manager.checkpointer
        app.state.store = pg_store_manager.store

    # ── 4. MinIO ────────────────────────────────────
    app.state.minio_client = None
    if s.minio_host:
        try:
            from minio import Minio
            minio = Minio(f"{s.minio_host}:{s.minio_port}",
                          access_key=s.minio_access_key,
                          secret_key=s.minio_secret_key, secure=False)
            if not minio.bucket_exists(s.minio_bucket):
                minio.make_bucket(s.minio_bucket)
            app.state.minio_client = minio
            logger.info(f"[main] MinIO 已连接: {s.minio_host}")
        except Exception as e:
            logger.warning(f"[main] MinIO 不可用: {e}")

    # ── 3. SubAgent 发现 ────────────────────────────
    from app.agent.agent_factory import _build_subagents_from_specialists
    subagents = _build_subagents_from_specialists()
    app.state.specialist_subagents = subagents

    # ── 4. 创建 Triage DeepAgent ────────────────────
    from app.agent.agent_factory import create_triage_agent, create_executor_agent
    from app.prompt.triage_prompt import build_triage_prompt
    from app.prompt.executor_prompt import build_executor_prompt
    from app.tool import TOOL_REGISTRY

    app.state.agent = await create_triage_agent(
        system_prompt=build_triage_prompt(), gateway=gateway, subagents=subagents,
        checkpointer=getattr(app.state, "checkpointer", None),
        store=getattr(app.state, "store", None),
    )
    logger.info(f"[main] Triage DeepAgent 创建完成 (subagents=%d)", len(subagents))

    # ── 5. 创建 Executor DeepAgent ──────────────────
    executor_tools = [TOOL_REGISTRY[n] for n in ("request_approval", "read_task_journal") if n in TOOL_REGISTRY]
    app.state.executor_agent = await create_executor_agent(
        system_prompt=build_executor_prompt(), gateway=gateway, subagents=subagents,
        checkpointer=getattr(app.state, "checkpointer", None),
        store=getattr(app.state, "store", None),
        interrupt_on={"request_approval": True},
    )
    logger.info("[main] Executor DeepAgent 创建完成")

    # ── 6. TaskExecutor（注入 executor_agent）────────
    from app.harness.task_executor import task_executor
    from app.harness.task_context import (
        MemoryTaskStore,
        PgTaskStore,
        TaskSnapshot,
        task_context_manager,
    )
    if getattr(app.state, "store", None) is not None:
        task_repository = PgTaskStore(app.state.store)
        logger.info("[main] TaskExecutor 使用 PostgreSQL 持久化仓储")
    else:
        task_repository = MemoryTaskStore()
        logger.warning("[main] PG Store 不可用，TaskExecutor 降级为内存仓储")
    task_executor.configure(
        store=task_repository,
        executor_agent=app.state.executor_agent,
    )
    app.state.task_executor = task_executor
    app.state.context_manager = task_context_manager

    # 关键持久化失败通过 PG DeadLetter 重试；PG 不可用时队列自动退回内存。
    from app.harness.dead_letter import dead_letter_queue
    dead_letter_queue.configure(getattr(app.state, "store", None))

    async def _retry_snapshot(args):
        await task_repository.save(TaskSnapshot.from_dict(args["snapshot"]))

    async def _retry_journal(args):
        await task_repository.append_journal(
            args["task_id"],
            args["event"],
            args["description"],
            args.get("detail"),
        )

    dead_letter_queue.register_retry_handler("task_snapshot_save", _retry_snapshot)
    dead_letter_queue.register_retry_handler("task_journal_append", _retry_journal)

    from app.memory.semantic_memory import semantic_memory
    semantic_memory.configure(getattr(app.state, "store", None))

    # ── 7. SkillManager ─────────────────────────────
    from app.skill.skill_manager import skill_manager
    from app.storage.db import new_session
    try:
        skill_manager.init_db(new_session())
    except Exception as e:
        logger.warning(f"SkillManager 初始化跳过: {e}")

    # ── 8. Neo4j（可选）──────────────────────────────
    from app.stores.neo4j_manager import neo4j_manager
    try:
        await neo4j_manager.initialize(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    except Exception as e:
        logger.warning(f"Neo4j 初始化跳过: {e}")

    # ── 9. 事件总线 ─────────────────────────────────
    from app.harness.event_bus import event_bus
    from app.common.redis import get_redis
    app.state.event_bus = event_bus  # 与 task_executor 引用的同一单例
    try:
        redis_client = await get_redis()
        event_bus.set_redis(redis_client)
        logger.info("[main] EventBus 已接入 Redis Pub/Sub")
    except Exception as e:
        logger.warning(f"[main] Redis 不可用，EventBus 降级为纯内存模式: {e}")

    # ── 10. 死信扫描 ────────────────────────────────
    await dead_letter_queue.start_scanner(120.0)

    # ── 11. 工具热加载 ──────────────────────────────
    from app.harness.tool_hot_reloader import ToolHotReloader
    tools_dir = Path(__file__).resolve().parent.parent / "tool"
    hot_reloader = ToolHotReloader(tools_dir, on_reload=lambda: _rebuild_agents(app))
    await hot_reloader.start()
    app.state.tool_hot_reloader = hot_reloader

    # ── 12. SubAgent 热加载 ──────────────────────────
    from app.harness.subagent_hot_reloader import SubAgentHotReloader
    specialists_dir = Path(__file__).resolve().parent.parent / "subagents"
    sa_reloader = SubAgentHotReloader(specialists_dir, on_reload=lambda: _rebuild_agents(app))
    await sa_reloader.start()
    app.state.subagent_hot_reloader = sa_reloader

    # ── 13. 恢复后台任务 ────────────────────────────
    await task_executor.recover_tasks()

    logger.info("[main] 启动完成")

    yield

    # ── 优雅关闭 ────────────────────────────────────
    await task_executor.shutdown()
    await hot_reloader.stop()
    await sa_reloader.stop()
    await dead_letter_queue.stop_scanner()
    await gateway.stop_probe()
    if pg_store_manager.available:
        await pg_store_manager.close()
    if neo4j_manager.available:
        await neo4j_manager.close()


app = FastAPI(title="APIs Agent", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RateLimitMiddleware)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id", "") or generate_trace_id()
    session_id = request.headers.get("X-Session-Id", "")
    set_trace_context(trace_id=trace_id, session_id=session_id)
    url = unquote(str(request.url))
    logger.info(f"{request.method} {url}")
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "data": None, "message": exc.message})


@app.exception_handler(InfrastructureError)
async def infra_error_handler(request: Request, exc: InfrastructureError):
    return JSONResponse(status_code=503, content={"code": 503, "data": None, "message": "服务暂时不可用，请稍后再试"})


@app.exception_handler(ApisAgentError)
async def apis_error_handler(request: Request, exc: ApisAgentError):
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "data": None, "message": exc.message})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception(f"未处理异常: {exc}")
    return JSONResponse(status_code=500, content={"code": 500, "data": None, "message": "服务器内部错误"})


app.include_router(agent_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(session_router, prefix="/api/v1")
app.include_router(file_router, prefix="/api/v1")
app.include_router(skill_router)
app.include_router(auth_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
