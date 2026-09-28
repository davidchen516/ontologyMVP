"""共享测试助手（tests 内可直接 import）。"""

from __future__ import annotations

from src.core.config import Settings

# 统一的测试配置基线：必填项齐全；密码仅用于断言“不得泄露”
BASE: dict[str, object] = {
    "postgres_host": "127.0.0.1",
    "postgres_port": 5432,
    "postgres_db": "ontology",
    "postgres_user": "ontology",
    "postgres_password": "unit-test-password",
    "neo4j_uri": "bolt://127.0.0.1:7687",
    "neo4j_user": "neo4j",
    "neo4j_password": "unit-test-password",
    "environment": "ci",
    "log_level": "INFO",
}

ENV_NAMES = {
    "postgres_host": "POSTGRES_HOST",
    "postgres_port": "POSTGRES_PORT",
    "postgres_db": "POSTGRES_DB",
    "postgres_user": "POSTGRES_USER",
    "postgres_password": "POSTGRES_PASSWORD",
    "neo4j_uri": "NEO4J_URI",
    "neo4j_user": "NEO4J_USER",
    "neo4j_password": "NEO4J_PASSWORD",
}


def make_settings(**overrides: object) -> Settings:
    """直接构造 Settings（init 参数优先于环境变量），测试互不污染。"""
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]
