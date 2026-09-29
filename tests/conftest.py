"""pytest fixtures 与共享测试助手。"""

from __future__ import annotations

from pathlib import Path

import pytest

# 分层标记自动注入（issue #10）：按顶层目录为测试模块打默认 marker；
# 模块内显式 pytestmark 优先。CI/本地可用 `-m "not performance"` 按层
# 选择；默认全套运行。
_DIR_MARKERS: dict[str, str] = {
    "unit": "unit",
    "semantic": "contract",
    "integration": "integration",
    "db": "integration",
    "golden": "golden",
    "mvp": "integration",
}


def pytest_collection_modifyitems(config: object, items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).resolve().parent
    for item in items:
        path = Path(item.path)
        try:
            relative = path.relative_to(tests_root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        marker_name = _DIR_MARKERS.get(relative.parts[0])
        if marker_name is None:
            continue
        if marker_name not in {m.name for m in item.iter_markers()}:
            item.add_marker(getattr(pytest.mark, marker_name))
