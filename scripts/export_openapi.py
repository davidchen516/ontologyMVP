"""导出 FastAPI OpenAPI 契约快照到 web/openapi.json（前端类型契约的锚点）。

用法：uv run python scripts/export_openapi.py [输出路径]
契约漂移检测：CI 的 contract job 重新导出并 diff（见 runtime-ci.yml）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.helpers import make_settings  # noqa: E402


def main() -> int:
    from apps.api.app import create_app

    app = create_app(make_settings())
    spec = app.openapi()
    default_target = REPO_ROOT / "web" / "openapi.json"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_target
    target.write_text(
        json.dumps(spec, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"openapi snapshot: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
