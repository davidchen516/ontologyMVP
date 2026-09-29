"""生产图执行器：只执行白名单 Cypher 模板（issue #9）。

QueryOrchestrator 的 graph_executor 端口在此接上真实 Neo4j 驱动；
模板白名单由 ALLOWED_GRAPH_QUERIES 强制——任何未在白名单中的 Cypher
一律拒绝执行，杜绝自由拼接查询。
"""

from __future__ import annotations

from typing import Any

import structlog

from src.query.compiler import ALLOWED_GRAPH_QUERIES

log = structlog.get_logger(__name__)


class Neo4jQueryExecutor:
    """Neo4j 只读查询执行器（白名单模板 + 参数化）。"""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    @classmethod
    def from_settings(cls, settings: Any) -> Neo4jQueryExecutor:
        """从 Settings 构建（驱动惰性连接，不可达在 execute 时降级）。"""
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user,
                  settings.neo4j_password.get_secret_value()),
            connection_timeout=5,
        )
        return cls(driver)

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if template not in ALLOWED_GRAPH_QUERIES:
            raise ValueError("cypher template not in whitelist")
        with self._driver.session() as session:
            result = session.run(template, params)
            return [dict(record) for record in result]

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:  # noqa: BLE001
            log.warning("query_graph_executor_close_failed")


def build_query_graph_executor(settings: Any) -> Neo4jQueryExecutor | None:
    """构建生产图执行器；构建失败返回 None（查询链路进入明确降级）。"""
    try:
        return Neo4jQueryExecutor.from_settings(settings)
    except Exception:  # noqa: BLE001
        log.warning("query_graph_executor_build_failed")
        return None
