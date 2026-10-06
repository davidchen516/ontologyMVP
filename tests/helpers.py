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
    # 显式固定可选能力为缺失：init 参数优先级最高，保证测试不受
    # 开发机环境变量 / .env 中 Token 影响（独立审查遗留观察项）。
    # operator_* 同理钉住（2026-10-06：.env 启用熔断解除工具时曾使
    # 4 个测试泄漏失败）
    "tushare_token": None,
    "llm_api_key": None,
    "operator_write_enabled": False,
    "operator_api_key_hashes": None,
    # 对称补钉（#57 审查 N4）：REVIEW_* 在 .env 启用时同样会泄漏
    "review_write_enabled": False,
    "review_api_key_hashes": None,
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
