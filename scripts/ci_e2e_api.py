"""CI e2e API 服务：带审核能力的 FastAPI（后台运行）。

用法：nohup uv run python scripts/ci_e2e_api.py > /tmp/api.log 2>&1 &
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    import uvicorn
    from apps.api.app import create_app
    from src.core.config import Settings

    settings = Settings(
        postgres_host="127.0.0.1",
        postgres_port=5432,
        postgres_db="postgres",
        postgres_user="postgres",
        postgres_password="postgres",
        neo4j_uri="bolt://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password="ci-neo4j-password",
        environment="ci",
        review_write_enabled=True,
        review_api_key_hashes=hashlib.sha256(b"ci-reviewer-key").hexdigest(),
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8111,
                log_level="warning")


if __name__ == "__main__":
    main()
