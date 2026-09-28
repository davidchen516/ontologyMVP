"""三类数据库角色权限测试：API 只读、Worker 受限写入、迁移专用。"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import InsufficientPrivilege

ROLES_SQL = Path(__file__).resolve().parents[2] / "scripts" / "db" / "roles.sql"

LOGIN_ROLES = {
    "test_api_login": "ontology_api_readonly",
    "test_worker_login": "ontology_worker_writer",
    "test_migrator_login": "ontology_migrator",
}


def _rebuild_admin_dsn(main_dsn: str) -> str:
    """回到维护库（postgres）执行角色/登录账号管理。"""
    params = conninfo_to_dict(main_dsn)
    params["dbname"] = "postgres"
    return psycopg.conninfo.make_conninfo(**params)


@pytest.fixture(scope="module")
def role_dsn(main_dsn) -> str:
    """应用 roles.sql 并创建三个临时登录账号；模块结束清理。"""
    admin = _rebuild_admin_dsn(main_dsn)
    roles_sql = ROLES_SQL.read_text(encoding="utf-8")

    # 角色与授权在目标库执行：ON ALL TABLES 覆盖既有表，DEFAULT PRIVILEGES
    # 覆盖后续新建表（幂等可重复）
    with psycopg.connect(main_dsn, autocommit=True) as conn:
        conn.execute(roles_sql)

    with psycopg.connect(admin, autocommit=True) as conn:
        for login, group in LOGIN_ROLES.items():
            conn.execute(f'DROP ROLE IF EXISTS "{login}"')
            conn.execute(
                f'CREATE ROLE "{login}" LOGIN PASSWORD \'role-test-pass\' IN ROLE "{group}"'
            )

    params = conninfo_to_dict(main_dsn)
    params["user"] = "test_api_login"
    params["password"] = "role-test-pass"
    api_dsn = psycopg.conninfo.make_conninfo(**params)
    yield api_dsn
    with psycopg.connect(admin, autocommit=True) as conn:
        for login in LOGIN_ROLES:
            conn.execute(f'DROP ROLE IF EXISTS "{login}"')


def _login_dsn(main_dsn: str, user: str, password: str = "role-test-pass") -> str:
    params = conninfo_to_dict(main_dsn)
    params["user"] = user
    params["password"] = password
    return psycopg.conninfo.make_conninfo(**params)


def test_api_readonly_can_select_but_cannot_write(role_dsn, main_dsn) -> None:
    api = _login_dsn(main_dsn, "test_api_login")
    with psycopg.connect(api) as conn:
        conn.execute("SELECT count(*) FROM fact.claim")
        conn.execute("SELECT version_num FROM alembic_version")

    with psycopg.connect(api) as conn:
        with pytest.raises(InsufficientPrivilege):
            conn.execute(
                "INSERT INTO master.company (canonical_name) VALUES ('越权写入')"
            )
    with psycopg.connect(api) as conn:
        with pytest.raises(InsufficientPrivilege):
            conn.execute("DELETE FROM fact.claim")
    with psycopg.connect(api) as conn:
        with pytest.raises(InsufficientPrivilege):
            conn.execute("CREATE TABLE ops.hack (id int)")


def test_worker_can_insert_business_rows_but_not_ddm_or_migrations(role_dsn, main_dsn) -> None:
    worker = _login_dsn(main_dsn, "test_worker_login")
    # autocommit：每个权限探针独立事务，失败互不干扰
    with psycopg.connect(worker, autocommit=True) as conn:
        run = conn.execute(
            "INSERT INTO ops.ingest_run (dataset_name, source_system, trace_id) "
            "VALUES ('tushare:x', 'TUSHARE', %s) RETURNING id",
            ("trace-role-test",),
        ).fetchone()
        conn.execute(
            "INSERT INTO raw.source_record (source_system, api_name, payload_hash, "
            "raw_payload, ingest_run_id) VALUES ('TUSHARE', 'x', %s, '{}', %s)",
            (uuid.uuid4().hex, run[0]),
        )

        with pytest.raises(InsufficientPrivilege):
            conn.execute("CREATE TABLE ops.hack (id int)")
        with pytest.raises(InsufficientPrivilege):
            conn.execute("DROP TABLE fact.claim")
        with pytest.raises(InsufficientPrivilege):
            conn.execute("UPDATE alembic_version SET version_num = '999'")
        with pytest.raises(InsufficientPrivilege):
            conn.execute("DELETE FROM fact.claim")  # 历史不物理删除


def test_migrator_can_manage_schema(role_dsn, main_dsn) -> None:
    migrator = _login_dsn(main_dsn, "test_migrator_login")
    with psycopg.connect(migrator, autocommit=True) as conn:
        conn.execute("CREATE TABLE ops.temp_migration_test (id int)")
        conn.execute("INSERT INTO ops.temp_migration_test VALUES (1)")
        conn.execute("DROP TABLE ops.temp_migration_test")
