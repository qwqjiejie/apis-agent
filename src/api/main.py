import os
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes.agent import router as agent_router
from src.api.routes.session import router as session_router
from src.api.routes.file import router as file_router
from src.common.logger import logger

app = FastAPI(title="Dodo Agent Python")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    url = unquote(str(request.url))
    logger.info(f"{request.method} {url}")
    response = await call_next(request)
    return response


app.include_router(agent_router)
app.include_router(session_router)
app.include_router(file_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
