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
from app.api.routes.artifact_routes import router as artifact_router
from app.api.routes.chat_routes import router as chat_router
from app.api.routes.feedback_routes import router as feedback_router
from app.api.routes.gateway_routes import router as gateway_router
from app.api.routes.task_routes import router as task_router
from app.api.routes.session import router as session_router
from app.api.routes.file import router as file_router
from app.api.routes.skill_routes import router as skill_router
from app.api.routes.auth_routes import router as auth_router
from app.bootstrap.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
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
        _build_subagents_from_specialists,
    )
    from app.prompt.triage_prompt import build_triage_prompt
    from app.prompt.executor_prompt import build_executor_prompt

    container: ApplicationContainer = app.state.container

    subagents = _build_subagents_from_specialists()
    triage_prompt = build_triage_prompt()
    executor_prompt = build_executor_prompt()

    new_triage = await create_triage_agent(
        system_prompt=triage_prompt,
        gateway=container.model_gateway,
        subagents=subagents,
        checkpointer=container.checkpointer,
        store=container.store,
    )
    new_executor = await create_executor_agent(
        system_prompt=executor_prompt,
        gateway=container.model_gateway,
        subagents=subagents,
        checkpointer=container.checkpointer,
        store=container.store,
        interrupt_on={"request_approval": True},
    )

    container.agent = new_triage
    container.executor_agent = new_executor
    container.specialist_subagents = subagents
    if container.task_executor is not None:
        container.task_executor.executor_agent = new_executor
    _last_rebuild_time = time.monotonic()
    logger.info("[HotReload] Agent 重建完成 (subagents=%d)", len(subagents))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    for runtime_dir in (
        s.upload_path,
        s.managed_skills_path,
        s.artifacts_path,
        s.evaluation_results_path,
    ):
        runtime_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 模型网关 ────────────────────────────────
    from app.gateway.model_gateway import ModelGateway
    from app.common.llm import _create_raw_llm

    gateway = ModelGateway()
    container = ApplicationContainer(model_gateway=gateway)
    set_application_container(container)
    app.state.container = container
    llm = _create_raw_llm()
    await gateway.register(s.llm_model, llm, is_primary=True)
    if s.fallback_model:
        fl = _create_raw_llm()
        fl.model_name = s.fallback_model
        await gateway.register(s.fallback_model, fl, is_primary=False)
        gateway.set_fallback([s.fallback_model])
    await gateway.start_probe(s.gateway_health_probe_interval_sec)

    # ── 2. PG Store (checkpointer + store) ─────────
    from app.infrastructure.postgres.langgraph_store import pg_store_manager
    if await pg_store_manager.initialize():
        container.checkpointer = pg_store_manager.checkpointer
        container.store = pg_store_manager.store

    # ── 4. MinIO ────────────────────────────────────
    minio_client = None
    if s.minio_host:
        try:
            from app.infrastructure.minio.client import (
                check_minio,
                create_minio_client,
            )
            minio = create_minio_client(s)
            health = await asyncio.to_thread(check_minio, minio, s)
            if not health.available:
                raise RuntimeError(health.detail)
            minio_client = minio
            logger.info(f"[main] MinIO 已连接: {s.minio_host}")
        except Exception as e:
            logger.warning(f"[main] MinIO 不可用: {e}")
    container.minio_client = minio_client

    # ── 3. 文档基础设施 ────────────────────────────────
    from app.modules.documents.service import file_service
    from app.infrastructure.milvus.vector_store import vector_store

    vector_store.connect()
    file_service.configure(
        minio_client=container.minio_client,
        db_available=True,
    )
    container.vector_store = vector_store
    container.file_service = file_service

    # ── 3. SubAgent 发现 ────────────────────────────
    from app.agent.agent_factory import _build_subagents_from_specialists
    subagents = _build_subagents_from_specialists()
    container.specialist_subagents = subagents

    # ── 4. 创建 Triage DeepAgent ────────────────────
    from app.agent.agent_factory import create_triage_agent, create_executor_agent
    from app.prompt.triage_prompt import build_triage_prompt
    from app.prompt.executor_prompt import build_executor_prompt
    container.agent = await create_triage_agent(
        system_prompt=build_triage_prompt(), gateway=gateway, subagents=subagents,
        checkpointer=container.checkpointer,
        store=container.store,
    )
    logger.info("[main] Triage DeepAgent 创建完成 (subagents=%d)", len(subagents))

    # ── 5. 创建 Executor DeepAgent ──────────────────
    container.executor_agent = await create_executor_agent(
        system_prompt=build_executor_prompt(), gateway=gateway, subagents=subagents,
        checkpointer=container.checkpointer,
        store=container.store,
        interrupt_on={"request_approval": True},
    )
    logger.info("[main] Executor DeepAgent 创建完成")

    # ── 6. 任务运行时依赖 ────────────────────────────
    from app.modules.tasks.context import (
        MemoryTaskStore,
        PgTaskStore,
        TaskContextManager,
        TaskSnapshot,
    )
    from app.modules.tasks.dead_letter import DeadLetterQueue
    from app.modules.tasks.events import EventBus
    from app.modules.tasks.executor import TaskExecutor
    from app.memory.semantic_memory import SemanticMemoryStore

    if container.store is not None:
        task_repository = PgTaskStore(container.store)
        logger.info("[main] TaskExecutor 使用 PostgreSQL 持久化仓储")
    else:
        task_repository = MemoryTaskStore()
        logger.warning("[main] PG Store 不可用，TaskExecutor 降级为内存仓储")

    event_bus = EventBus()
    dead_letter_queue = DeadLetterQueue(container.store)
    semantic_memory = SemanticMemoryStore()
    semantic_memory.configure(container.store)
    task_context_manager = TaskContextManager()
    task_executor = TaskExecutor(
        store=task_repository,
        event_bus_instance=event_bus,
        context_manager_instance=task_context_manager,
        dead_letter_queue_instance=dead_letter_queue,
        executor_agent=container.executor_agent,
    )

    container.task_executor = task_executor
    container.context_manager = task_context_manager
    container.dead_letter_queue = dead_letter_queue
    container.semantic_memory = semantic_memory
    container.event_bus = event_bus

    # 关键持久化失败通过 PG DeadLetter 重试；PG 不可用时队列自动退回内存。
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

    # ── 7. SkillManager ─────────────────────────────
    from app.modules.skills.manager import SkillManager
    skill_manager = SkillManager()
    await asyncio.to_thread(skill_manager.initialize)
    container.skill_manager = skill_manager

    # ── 8. Neo4j（可选）──────────────────────────────
    from app.infrastructure.neo4j.manager import neo4j_manager
    try:
        await neo4j_manager.initialize(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    except Exception as e:
        logger.warning(f"Neo4j 初始化跳过: {e}")

    # ── 9. 事件总线 ─────────────────────────────────
    from app.infrastructure.redis.client import get_redis
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
    container.tool_hot_reloader = hot_reloader

    # ── 12. SubAgent 热加载 ──────────────────────────
    from app.harness.subagent_hot_reloader import SubAgentHotReloader
    specialists_dir = Path(__file__).resolve().parent.parent / "subagents"
    sa_reloader = SubAgentHotReloader(specialists_dir, on_reload=lambda: _rebuild_agents(app))
    await sa_reloader.start()
    container.subagent_hot_reloader = sa_reloader

    # ── 13. 恢复后台任务 ────────────────────────────
    await task_executor.recover_tasks()

    logger.info("[main] 启动完成")

    yield

    # ── 优雅关闭 ────────────────────────────────────
    await task_executor.shutdown()
    await hot_reloader.stop()
    await sa_reloader.stop()
    await dead_letter_queue.stop_scanner()
    skill_manager.close()
    await gateway.stop_probe()
    vector_store.close()
    if pg_store_manager.available:
        await pg_store_manager.close()
    if neo4j_manager.available:
        await neo4j_manager.close()
    from app.infrastructure.redis.client import close_redis
    await close_redis()
    from app.infrastructure.postgres.database import dispose_database
    dispose_database()
    clear_application_container(container)


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


app.include_router(chat_router, prefix="/api/v1")
app.include_router(task_router, prefix="/api/v1/agent")
app.include_router(gateway_router, prefix="/api/v1/agent")
app.include_router(artifact_router, prefix="/api/v1/agent")
app.include_router(feedback_router, prefix="/api/v1/agent")
app.include_router(session_router, prefix="/api/v1")
app.include_router(file_router, prefix="/api/v1")
app.include_router(skill_router)
app.include_router(auth_router)


@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    from app.infrastructure.milvus.vector_store import vector_store
    from app.infrastructure.postgres.database import check_db
    from app.infrastructure.redis.client import health_check as redis_health_check

    checks = []
    try:
        await asyncio.to_thread(check_db)
        checks.append({"service": "PostgreSQL", "available": True})
    except Exception as exc:
        checks.append({
            "service": "PostgreSQL",
            "available": False,
            "detail": str(exc),
        })

    redis_result = await redis_health_check()
    checks.append({
        "service": redis_result.service,
        "available": redis_result.available,
        "detail": redis_result.detail,
    })

    if get_settings().milvus_host:
        milvus_result = await asyncio.to_thread(vector_store.health_check)
        checks.append({
            "service": milvus_result.service,
            "available": milvus_result.available,
            "detail": milvus_result.detail,
        })

    ready = all(check["available"] for check in checks)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unavailable", "checks": checks},
    )


STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
