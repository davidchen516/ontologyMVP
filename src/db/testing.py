"""迁移测试工具：以子进程运行 alembic（与生产 CLI 同路径）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parents[2]


def dsn_env(dsn: str, *, dbname: str | None = None) -> dict[str, str]:
    """把目标 DSN 转成 alembic env.py 读取的环境变量（POSTGRES_*）。"""
    params = conninfo_to_dict(dsn)
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": params["host"] or "127.0.0.1",
            "POSTGRES_PORT": str(params.get("port") or 5432),
            "POSTGRES_USER": params.get("user") or "",
            "POSTGRES_PASSWORD": params.get("password") or "",
            "POSTGRES_DB": dbname or (params.get("dbname") or "postgres"),
        }
    )
    return env


def run_alembic(command: str, target: str, dsn: str, *, dbname: str | None = None) -> None:
    """运行 alembic <command> <target>；失败抛 CalledProcessError（stderr 保留）。"""
    result = subprocess.run(
        ["uv", "run", "alembic", command, target],
        cwd=REPO_ROOT,
        env=dsn_env(dsn, dbname=dbname),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {command} {target} failed (rc={result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
