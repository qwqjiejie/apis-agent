import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings


def _get_env_file() -> Path:
    env_file = os.getenv("APIS_ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    # LLM 配置
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    fallback_model: str = ""  # 降级模型名（留空则不启用降级）

    # 视觉模型配置（图片识别）
    vision_model: str = "qwen3.7-plus"

    # 搜索配置
    tavily_api_key: str = ""

    # 服务配置
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    log_level: str = "INFO"

    # Agent 配置
    max_agent_iterations: int = 5
    max_history_rounds: int = 20
    max_query_length: int = 10000

    # PostgreSQL 配置 — 业务数据库
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "apis_agent"

    # PostgreSQL — LangGraph 专用连接（checkpointer + store）
    # 留空则自动从 pg_* 拼接
    langgraph_db_url: str = ""

    # MinIO 配置
    minio_host: str = ""
    minio_port: int = 9000
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "apis"
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 20

    # Milvus 配置
    milvus_host: str = ""
    milvus_port: int = 19530
    milvus_user: str = ""
    milvus_pass: str = ""
    milvus_db: str = "default"

    # Redis 配置
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # Embedding 配置
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024

    # 任务控制配置
    task_lock_timeout_seconds: int = 300

    # 深度研究配置
    deep_research_max_concurrency: int = 3
    deep_research_max_iterations: int = 3
    deep_research_max_sub_tasks: int = 6

    # 模型网关配置
    gateway_health_probe_interval_sec: int = 30
    gateway_circuit_breaker_threshold: int = 5
    gateway_circuit_breaker_cooldown_sec: int = 30

    # 限流配置
    rate_limit_enabled: bool = True
    rate_limit_user_per_min: int = 60
    rate_limit_ip_per_min: int = 300
    rate_limit_window_sec: int = 60

    # Langfuse 追踪配置（留空则禁用）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # 上下文压缩配置
    compression_enabled: bool = True
    compression_layer_1_keep_recent_rounds: int = 2
    compression_layer_2_threshold_ratio: float = 0.75
    max_context_tokens: int = 128000

    model_config = {"env_file": str(_get_env_file()), "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def validate_critical_config(self) -> "Settings":
        if not self.llm_api_key:
            raise ValueError("llm_api_key 未配置，LLM API 密钥为必填项")
        parsed = urlparse(self.llm_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"llm_base_url 格式无效: {self.llm_base_url}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
