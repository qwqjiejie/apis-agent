import os
from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    tavily_api_key: str = ""
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    log_level: str = "INFO"
    max_agent_iterations: int = 10
    max_history_rounds: int = 20
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_pass: str = ""
    mysql_db: str = "dodo"
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "dodo"
    upload_dir: str = "uploads"
    milvus_host: str = ""
    milvus_port: int = 19530
    milvus_user: str = ""
    milvus_pass: str = ""
    milvus_db: str = "default"
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
