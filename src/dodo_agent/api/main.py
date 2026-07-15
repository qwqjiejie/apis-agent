import os
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.dodo_agent.api.routes.agent import router as agent_router
from src.dodo_agent.api.routes.session import router as session_router
from src.dodo_agent.api.routes.file import router as file_router
from src.dodo_agent.common.exceptions import DodoAgentError, InfrastructureError, ValidationError
from src.dodo_agent.common.logger import logger

app = FastAPI(title="Dodo Agent Python")

# ---- CORS ----

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- HTTP 请求日志 ----

@app.middleware("http")
async def log_requests(request: Request, call_next):
    url = unquote(str(request.url))
    logger.info(f"{request.method} {url}")
    response = await call_next(request)
    return response

# ---- 全局异常处理 — 参考 Java 版 BaseResult 统一响应格式 ----
# 注意：FastAPI/Starlette 按注册顺序匹配处理器，必须从最具体到最通用

@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """输入验证异常 — 返回实际 HTTP 错误码，message 直接展示给用户。"""
    logger.info(f"输入验证失败: {exc.message}")
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "data": None, "message": exc.message},
    )

@app.exception_handler(InfrastructureError)
async def infra_error_handler(request: Request, exc: InfrastructureError):
    """基础设施异常 — 返回 503，隐藏内部细节。"""
    logger.error(f"基础设施不可用 [{exc.service}]: {exc.message}")
    return JSONResponse(
        status_code=503,
        content={"code": 503, "data": None, "message": "服务暂时不可用，请稍后再试"},
    )

@app.exception_handler(DodoAgentError)
async def dodo_error_handler(request: Request, exc: DodoAgentError):
    """兜底：其他未归类的业务异常。"""
    logger.warning(f"业务异常 [{exc.code}]: {exc.message}")
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "data": None, "message": exc.message},
    )

@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """兜底：未预期的异常记录完整 traceback，对外隐藏细节。"""
    logger.exception(f"未处理异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "data": None, "message": "服务器内部错误"},
    )

# ---- 路由 ----

app.include_router(agent_router)
app.include_router(session_router)
app.include_router(file_router)

# ---- 静态文件 ----

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
