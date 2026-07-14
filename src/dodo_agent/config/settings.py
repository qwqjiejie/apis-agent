import os
from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    # LLM 配置
    llm_api_key: str = ""  # LLM API 密钥
    llm_base_url: str = "https://api.openai.com/v1"  # LLM API 地址（兼容 OpenAI 协议）
    llm_model: str = "gpt-4o"  # 默认模型名称

    # 搜索配置
    tavily_api_key: str = ""  # Tavily 搜索 API 密钥

    # 服务配置
    server_host: str = "0.0.0.0"  # 服务监听地址
    server_port: int = 8080  # 服务监听端口
    log_level: str = "INFO"  # 日志级别

    # Agent 配置
    max_agent_iterations: int = 5  # Agent 最大推理轮次
    max_history_rounds: int = 20  # 加载历史对话最大轮数

    # MySQL 配置
    mysql_host: str = "127.0.0.1"  # MySQL 主机
    mysql_port: int = 3306  # MySQL 端口
    mysql_user: str = "root"  # MySQL 用户名
    mysql_pass: str = ""  # MySQL 密码
    mysql_db: str = "dodo"  # MySQL 数据库名

    # MinIO 配置
    minio_endpoint: str = ""  # MinIO 服务地址
    minio_access_key: str = ""  # MinIO 访问密钥
    minio_secret_key: str = ""  # MinIO 密钥
    minio_bucket: str = "dodo"  # MinIO 存储桶名称
    upload_dir: str = "uploads"  # 本地文件上传目录

    # Milvus 配置
    milvus_host: str = ""  # Milvus 向量数据库地址
    milvus_port: int = 19530  # Milvus 端口
    milvus_user: str = ""  # Milvus 用户名
    milvus_pass: str = ""  # Milvus 密码
    milvus_db: str = "default"  # Milvus 数据库名

    # Redis 配置
    redis_host: str = "127.0.0.1"  # Redis 主机
    redis_port: int = 6379  # Redis 端口
    redis_db: int = 0  # Redis 数据库编号
    redis_password: str = ""  # Redis 密码

    # Embedding 配置
    embedding_model: str = "text-embedding-v4"  # 向量嵌入模型名称
    embedding_dim: int = 1024  # 向量维度

    # 任务控制配置
    task_lock_timeout_seconds: int = 300  # 会话级分布式锁 TTL，防止同会话并发执行

    # 上下文压缩配置
    compression_enabled: bool = True  # 是否启用上下文压缩
    compression_layer_1_keep_recent_rounds: int = 2  # Layer 1 压缩保留最近 N 轮完整内容
    compression_layer_2_threshold_ratio: float = 0.75  # Layer 2 LLM 摘要触发阈值（占 max_context_tokens 的比例）
    max_context_tokens: int = 128000  # 上下文窗口 token 上限

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
