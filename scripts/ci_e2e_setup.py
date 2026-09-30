"""CI e2e 环境搭建：迁移 + MVP 快照（PG + Neo4j 投影）。

用法（CI e2e job）：uv run python scripts/ci_e2e_setup.py
  --dsn <DSN> --neo4j-uri <uri> --neo4j-password <pw>
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--neo4j-uri", default=None)
    parser.add_argument("--neo4j-password", default=None)
    args = parser.parse_args()

    from src.db.testing import run_alembic

    run_alembic("upgrade", "head", args.dsn)
    print("migrations OK")

    from scripts.build_mvp_snapshot import build_snapshot

    build_snapshot(
        args.dsn,
        neo4j_uri=args.neo4j_uri,
        neo4j_password=args.neo4j_password,
        output_dir=Path("/tmp/snap"),
    )
    print("snapshot OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
