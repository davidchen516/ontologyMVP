"""连接构造与数据库工具。"""

from __future__ import annotations

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

from src.core.config import PostgresConnectionSettings


def psycopg_dsn(settings: PostgresConnectionSettings) -> str:
    return settings.postgres_dsn


def sqlalchemy_url(settings: PostgresConnectionSettings) -> URL:
    """Alembic（SQLAlchemy 引擎）使用的 psycopg3 驱动 URL。"""
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def database_exists(admin_dsn: str, dbname: str) -> bool:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
    return row is not None


def create_database(admin_dsn: str, dbname: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def drop_database(admin_dsn: str, dbname: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()"
            ),
            (dbname,),
        )
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))
