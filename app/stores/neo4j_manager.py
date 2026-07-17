"""兼容导出；新代码使用 Neo4j infrastructure。"""

from app.infrastructure.neo4j.manager import Neo4jManager, neo4j_manager

__all__ = ["Neo4jManager", "neo4j_manager"]
