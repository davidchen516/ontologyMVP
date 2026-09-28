"""Alembic 迁移环境。

并发安全（issue #2）：迁移以 PostgreSQL advisory lock 串行化；应用进程
不执行迁移，迁移是独立步骤（如 `docker compose run --rm api alembic upgrade head`）。
连接信息来自应用 Settings（环境变量 / .env），不落任何凭证文件。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import PostgresConnectionSettings  # noqa: E402
from src.db.connection import sqlalchemy_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 本仓库不使用 SQLAlchemy 模型自动生成，迁移手写并经 tests/db/test_migrations.py 守护。
target_metadata = None

# 0x4F4E544F = "ONTO"：ontologyMVP 迁移专属 advisory lock 键
MIGRATION_ADVISORY_LOCK_ID = 0x4F4E544F


def _url_from_settings() -> str:
    # 迁移只依赖 PostgreSQL 连接配置（独立步骤，不要求 Neo4j 等运行时配置）
    settings = PostgresConnectionSettings()
    return sqlalchemy_url(settings).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=_url_from_settings(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url_from_settings())
    with engine.connect() as connection:
        # advisory lock：并发 alembic 进程串行化，后到者等待前序完成
        connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
        )
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
            )
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
