"""LangGraph PostgreSQL checkpointer 与 Store 生命周期。"""

import logging

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from app.config.settings import get_settings

logger = logging.getLogger("apis")


class PgStoreManager:
    """PostgreSQL Store 管理器 — 为 LangGraph 提供 checkpointer 和 store。"""

    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.store: AsyncPostgresStore | None = None

    @property
    def available(self) -> bool:
        return self.checkpointer is not None

    async def initialize(self) -> bool:
        s = get_settings()
        url = s.langgraph_db_url
        if not url:
            url = f"postgresql://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{s.pg_db}"

        try:
            self._pool = AsyncConnectionPool(
                url, min_size=2, max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                open=False,
                timeout=s.external_connect_timeout_seconds,
            )
            await self._pool.open()
            await self._pool.wait()

            self.checkpointer = AsyncPostgresSaver(conn=self._pool)
            await self.checkpointer.setup()

            self.store = AsyncPostgresStore(conn=self._pool)
            await self.store.setup()

            logger.info(f"[PgStore] checkpointer + store 初始化完成: {s.pg_host}:{s.pg_port}/{s.pg_db}")
            return True
        except Exception as e:
            logger.warning(f"[PgStore] 初始化失败（对话历史仅内存模式）: {e}")
            if self._pool:
                try:
                    await self._pool.close()
                except Exception:
                    pass
            self._pool = None
            self.checkpointer = None
            self.store = None
            return False

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
        self.checkpointer = None
        self.store = None


pg_store_manager = PgStoreManager()
