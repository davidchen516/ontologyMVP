"""数据库测试夹具。

运行前提：设置 TEST_DATABASE_DSN 指向一次性 PostgreSQL（15+ / pgvector）。
本地示例：

    docker run -d --name ontology-mvp-pgtest \\
      -e POSTGRES_USER=testuser -e POSTGRES_PASSWORD=testpass \\
      -p 55432:5432 pgvector/pgvector:pg16
    export TEST_DATABASE_DSN=postgresql://testuser:testpass@127.0.0.1:55432/postgres
    uv run pytest tests/db

CI 由 runtime-ci.yml 的 postgres service 提供。未设置该变量时跳过整套 DB 测试。
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

TEST_DATABASE_DSN = os.environ.get("TEST_DATABASE_DSN", "")

if not TEST_DATABASE_DSN:
    pytest.skip(
        "TEST_DATABASE_DSN not set; database tests need a disposable PostgreSQL+pgvector",
        allow_module_level=True,
    )

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_all_tables_sql = """
SELECT string_agg(format('%I.%I', table_schema, table_name), ', ')
FROM information_schema.tables
WHERE table_schema IN ('raw', 'master', 'fact', 'finance', 'ops')
  AND table_type = 'BASE TABLE'
"""


def _admin_params() -> dict[str, str]:
    params = conninfo_to_dict(TEST_DATABASE_DSN)
    params["dbname"] = "postgres"
    return params


def _create_database(admin_dsn: str, dbname: str) -> str:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    params = conninfo_to_dict(TEST_DATABASE_DSN)
    params["dbname"] = dbname
    return psycopg.conninfo.make_conninfo(**params)


def _drop_database(admin_dsn: str, dbname: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    return psycopg.conninfo.make_conninfo(**_admin_params())


@pytest.fixture(scope="session")
def main_dsn(admin_dsn: str) -> str:
    """功能测试主库：一次性创建 + alembic upgrade head，会话结束销毁。"""
    from src.db.testing import run_alembic

    dbname = "ontology_mvp_test"
    dsn = _create_database(admin_dsn, dbname)
    run_alembic("upgrade", "head", dsn)
    yield dsn
    _drop_database(admin_dsn, dbname)


@pytest.fixture()
def uow_factory(main_dsn: str):
    from src.db.uow import UnitOfWorkFactory

    return UnitOfWorkFactory(main_dsn)


@pytest.fixture(autouse=True)
def _clean_tables(request, main_dsn):
    """每个测试后清空业务表（保留 alembic_version），测试互不污染。

    声明 PRESERVE_STANDARDIZATION_DATA = True 的测试模块跳过清库
    （该模块自管一次性种子，跨多个断言测试复用）。
    """
    yield
    if getattr(request.module, "PRESERVE_STANDARDIZATION_DATA", False):
        return
    with psycopg.connect(main_dsn) as conn:
        row = conn.execute(_all_tables_sql).fetchone()
        if row and row[0]:
            conn.execute(f"TRUNCATE {row[0]} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session")
def _migrated_once_for_docs(main_dsn) -> None:  # pragma: no cover - 占位文档钩子
    yield None


def fresh_db_dsn(admin_dsn: str, prefix: str = "ontology_mvp_mig") -> tuple[str, str]:
    """为迁移测试创建独立一次性数据库，返回 (dsn, dbname)。"""
    dbname = f"{prefix}_{uuid.uuid4().hex[:12]}"
    return _create_database(admin_dsn, dbname), dbname


def drop_db(admin_dsn: str, dbname: str) -> None:
    _drop_database(admin_dsn, dbname)
