"""MinIO 客户端工厂、超时和健康检查。"""

from __future__ import annotations

import urllib3
from minio import Minio
from urllib3.util import Retry, Timeout

from app.config.settings import Settings, get_settings
from app.infrastructure.reliability import HealthCheckResult, retry_sync


def create_minio_client(settings: Settings | None = None) -> Minio | None:
    settings = settings or get_settings()
    if not settings.minio_host:
        return None
    attempts = max(1, settings.external_retry_attempts)
    http_client = urllib3.PoolManager(
        timeout=Timeout(
            connect=settings.external_connect_timeout_seconds,
            read=settings.external_operation_timeout_seconds,
        ),
        retries=Retry(
            total=attempts - 1,
            connect=attempts - 1,
            read=attempts - 1,
            backoff_factor=0.1,
        ),
    )
    return Minio(
        f"{settings.minio_host}:{settings.minio_port}",
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
        http_client=http_client,
    )


def ensure_bucket(client: Minio, settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    def operation() -> None:
        if not client.bucket_exists(settings.minio_bucket):
            client.make_bucket(settings.minio_bucket)

    retry_sync(operation, attempts=settings.external_retry_attempts)


def check_minio(
    client: Minio | None,
    settings: Settings | None = None,
) -> HealthCheckResult:
    settings = settings or get_settings()
    if client is None:
        return HealthCheckResult("MinIO", False, "not configured")
    try:
        ensure_bucket(client, settings)
        return HealthCheckResult("MinIO", True)
    except Exception as exc:
        return HealthCheckResult("MinIO", False, str(exc))
