"""Fixture 录制/重放机制（issue #3）：CI 不依赖真实 TuShare 网络/Token。

- FixtureTransport：按 (api_name, offset) 回放固定 JSON；支持脚本化错误页。
- RecordingTransport：包裹真实 Transport，把响应原样录制为 Fixture 文件。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class FixtureTransport:
    """从目录读取 {api}.json 回放；参数中带 offset=K 时读取 {api}.pageK.json（若存在）。"""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(request)))  # 记录请求（含脱敏检查用）
        api_name = request["api_name"]
        params = request.get("params") or {}
        offset = params.get("offset")
        candidate = (
            self.fixture_dir / f"{api_name}.page{offset}.json"
            if offset is not None
            else None
        )
        if candidate is not None and candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
        path = self.fixture_dir / f"{api_name}.json"
        if not path.exists():
            raise FileNotFoundError(f"missing fixture for {api_name}: {path}")
        return json.loads(path.read_text(encoding="utf-8"))


class RecordingTransport:
    """录制真实响应为 Fixture（生产验证用）；不写入任何 Token。"""

    def __init__(self, inner: Transport, output_dir: Path) -> None:
        self.inner = inner
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self.inner(request)
        api_name = request["api_name"]
        params = request.get("params") or {}
        offset = params.get("offset")
        suffix = f".page{offset}" if offset is not None else ""
        path = self.output_dir / f"{api_name}{suffix}.json"
        path.write_text(
            json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return response
