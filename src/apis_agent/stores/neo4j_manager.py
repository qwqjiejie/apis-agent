"""Neo4jManager — Neo4j 知识图谱连接管理。

Neo4j 为可选依赖，未配置或初始化失败时自动降级。
所有图谱操作通过此管理器访问，业务层不直接依赖 Neo4j 驱动。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("apis")


class Neo4jManager:
    """Neo4j 连接管理器。未配置时 available=False，所有操作静默跳过。"""

    def __init__(self):
        self._driver = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def initialize(self, uri: str = "", user: str = "", password: str = ""):
        """初始化 Neo4j 连接。任一参数为空则跳过。"""
        if not uri or not user or not password:
            logger.info("[Neo4j] 未配置，知识图谱功能降级")
            return

        try:
            from neo4j import AsyncGraphDatabase
            self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
            # 连通性验证
            async with self._driver.session() as session:
                await session.run("RETURN 1")
            self._available = True
            logger.info(f"[Neo4j] 连接成功: {uri}")
        except ImportError:
            logger.warning("[Neo4j] neo4j 驱动未安装，知识图谱功能不可用。pip install neo4j")
        except Exception as e:
            logger.warning(f"[Neo4j] 初始化失败（降级，不影响核心功能）: {e}")

    async def close(self):
        if self._driver:
            await self._driver.close()
            self._available = False

    async def run_cypher(self, query: str, params: dict | None = None) -> list[dict[str, Any]]:
        """执行 Cypher 查询，返回记录列表。"""
        if not self._available or not self._driver:
            return []
        try:
            async with self._driver.session() as session:
                result = await session.run(query, params or {})
                records = await result.data()
                return records
        except Exception as e:
            logger.warning(f"[Neo4j] 查询失败: {e}")
            return []

    async def upsert_entity(self, label: str, name: str, properties: dict | None = None):
        """创建或更新实体节点。"""
        props = properties or {}
        props["name"] = name
        props_str = ", ".join(f"{k}: ${k}" for k in props)
        await self.run_cypher(
            f"MERGE (n:{label} {{name: $name}}) SET {props_str}",
            props,
        )

    async def upsert_relation(self, from_label: str, from_name: str,
                               rel_type: str, to_label: str, to_name: str):
        """创建或更新实体间的关系。"""
        await self.run_cypher(
            f"""MERGE (a:{from_label} {{name: $from_name}})
                MERGE (b:{to_label} {{name: $to_name}})
                MERGE (a)-[:{rel_type}]->(b)""",
            {"from_name": from_name, "to_name": to_name},
        )

    async def search_related(self, keyword: str, max_hops: int = 2, limit: int = 20) -> list[dict]:
        """搜索与关键词相关的实体和关系。"""
        return await self.run_cypher(
            f"""MATCH (n)
                WHERE n.name CONTAINS $keyword
                OPTIONAL MATCH path=(n)-[*1..{max_hops}]-(related)
                RETURN n.name AS entity, labels(n) AS labels,
                       related.name AS related_entity, labels(related) AS related_labels
                LIMIT $limit""",
            {"keyword": keyword, "limit": limit},
        )


neo4j_manager = Neo4jManager()
