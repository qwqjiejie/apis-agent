import logging
import sys

import uvicorn

from app.common.logger import logger
from app.config.settings import get_settings

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s [%(name)s] [%(trace_id)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def _check_infrastructure():
    """启动时强制检查基础设施，任一不可用则退出进程。"""
    from app.infrastructure.milvus.vector_store import vector_store
    from app.infrastructure.postgres.database import check_db
    from app.infrastructure.redis.client import check_redis

    services = [
        ("PostgreSQL", check_db),
        ("Redis", check_redis),
        ("Milvus", vector_store.check_milvus),
    ]

    failed = False
    for name, checker in services:
        try:
            checker()
        except Exception as e:
            logger.error(f"[启动检查] {name}: {e}")
            failed = True

    if failed:
        logger.error("基础设施检查失败，服务无法启动")
        sys.exit(1)

    logger.info("基础设施检查全部通过")


def main():
    _check_infrastructure()

    # 预热 Langfuse 连接（若未配置则静默跳过）
    from app.common.langfuse_client import get_langfuse
    lf = get_langfuse()
    if lf is not None:
        logger.info("Langfuse 追踪已启用")

    uvicorn.run(
        "app.api.main:app",
        host=get_settings().server_host,
        port=get_settings().server_port,
        log_level=get_settings().log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
