"""迁移验收：空库升级、上一版本升级不丢数据、回滚、重复执行、并发锁。"""

from __future__ import annotations

import subprocess

import psycopg
from src.db.testing import REPO_ROOT, dsn_env, run_alembic

from tests.db.conftest import drop_db, fresh_db_dsn

ALL_TABLES_SQL = """
SELECT count(*) FROM information_schema.tables
WHERE table_schema IN ('raw', 'master', 'fact', 'finance', 'ops')
  AND table_type = 'BASE TABLE'
"""


def table_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(ALL_TABLES_SQL).fetchone()[0]


def alembic_version(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_upgrade_head_from_empty_database(admin_dsn) -> None:
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        assert table_count(dsn) == 0
        run_alembic("upgrade", "head", dsn)
        assert alembic_version(dsn) == "0009_query_audit"
        assert table_count(dsn) == 31
        # 关键原生枚举存在
        with psycopg.connect(dsn) as conn:
            for type_name in (
                "ingest_run_status",
                "claim_status",
                "review_task_status",
                "graph_outbox_status",
                "document_parse_status",
            ):
                exists = conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = %s)", (type_name,)
                ).fetchone()[0]
                assert exists, f"native enum {type_name} missing"
    finally:
        drop_db(admin_dsn, dbname)


def test_upgrade_from_previous_version_preserves_data(admin_dsn) -> None:
    """受支持的上一版本（0001）上重复升级成功且无数据丢失。"""
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        run_alembic("upgrade", "0001_base", dsn)
        with psycopg.connect(dsn) as conn:
            company_id = conn.execute(
                "INSERT INTO master.company (canonical_name) "
                "VALUES ('先行升级测试公司') RETURNING id"
            ).fetchone()[0]
            conn.commit()

        run_alembic("upgrade", "head", dsn)

        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT canonical_name FROM master.company WHERE id = %s", (company_id,)
            ).fetchone()
            assert row is not None and row[0] == "先行升级测试公司"
            # fact 层可用于继续写入
            conn.execute(
                "INSERT INTO finance.financial_metric "
                "(metric_code, name, statement_type, unit_type, default_aggregation) "
                "VALUES ('TEST_METRIC', '测试指标', 'INCOME', 'CNY', 'SUM')"
            )
            conn.commit()
    finally:
        drop_db(admin_dsn, dbname)


def test_rerun_upgrade_head_is_noop(admin_dsn) -> None:
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        run_alembic("upgrade", "head", dsn)
        run_alembic("upgrade", "head", dsn)  # 重复执行：幂等成功
        assert table_count(dsn) == 31
        assert alembic_version(dsn) == "0009_query_audit"
    finally:
        drop_db(admin_dsn, dbname)


def test_downgrade_cycle(admin_dsn) -> None:
    """head → 0001（raw/master 数据保留）→ base（全清）→ head。"""
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        run_alembic("upgrade", "head", dsn)
        with psycopg.connect(dsn) as conn:
            company_id = conn.execute(
                "INSERT INTO master.company (canonical_name) VALUES ('回滚保留公司') RETURNING id"
            ).fetchone()[0]
            conn.commit()

        run_alembic("downgrade", "0001_base", dsn)
        assert table_count(dsn) == 14  # ops 2 + raw 1 + master 11
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT canonical_name FROM master.company WHERE id = %s", (company_id,)
            ).fetchone()
            assert row is not None and row[0] == "回滚保留公司"

        run_alembic("downgrade", "base", dsn)
        assert table_count(dsn) == 0

        run_alembic("upgrade", "head", dsn)
        assert table_count(dsn) == 31
    finally:
        drop_db(admin_dsn, dbname)


def test_concurrent_upgrade_head_is_serialized_by_advisory_lock(admin_dsn) -> None:
    """并发迁移依赖 advisory lock：两个进程都成功，Schema 一致无损坏。"""
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        env = dsn_env(dsn)
        procs = [
            subprocess.Popen(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        for proc in procs:
            out, err = proc.communicate(timeout=300)
            assert proc.returncode == 0, f"并发升级失败:\n{out}\n{err}"

        assert table_count(dsn) == 31
        assert alembic_version(dsn) == "0009_query_audit"
    finally:
        drop_db(admin_dsn, dbname)


def test_schema_version_consistency(admin_dsn) -> None:
    """版本与结构一致性：head 版本的 claim.claim_status 必须是原生枚举列。"""
    dsn, dbname = fresh_db_dsn(admin_dsn)
    try:
        run_alembic("upgrade", "head", dsn)
        with psycopg.connect(dsn) as conn:
            col = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='fact' AND table_name='claim' AND column_name='claim_status'"
            ).fetchone()
            assert col is not None and col[0] == "USER-DEFINED"
    finally:
        drop_db(admin_dsn, dbname)
