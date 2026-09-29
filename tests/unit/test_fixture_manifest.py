"""Fixture 清单守护（issue #10：固定版本、内容 Hash、更新审查流程）。

tests/fixtures 下任何文件被修改而未同步 MANIFEST.yaml → 测试失败。
这保证 Fixture 变更必须走受控流程（MANIFEST.md 更新流程章节），
防止"改 Fixture 让测试通过"的静默漂移。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load_manifest() -> dict:
    # 内容为 JSON（YAML 子集），统一按 YAML 读取
    return yaml.safe_load((FIXTURES_DIR / "MANIFEST.yaml").read_text("utf-8"))


def test_manifest_exists_and_complete() -> None:
    manifest_path = FIXTURES_DIR / "MANIFEST.yaml"
    assert manifest_path.exists(), "MANIFEST.yaml missing"
    manifest = _load_manifest()
    assert manifest.get("version"), "manifest version required"
    assert isinstance(manifest.get("files"), dict)

    # 实际文件 ↔ 清单逐一对齐（双向）
    actual: dict[str, str] = {}
    for path in sorted(FIXTURES_DIR.rglob("*")):
        if not path.is_file() or path.name.startswith("MANIFEST"):
            continue
        relative = str(path.relative_to(FIXTURES_DIR))
        actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    listed = manifest["files"]
    unlisted = set(actual) - set(listed)
    stale = set(listed) - set(actual)
    assert not unlisted, f"fixtures missing from manifest: {sorted(unlisted)}"
    assert not stale, f"manifest entries without files: {sorted(stale)}"


def test_fixture_hashes_unchanged() -> None:
    """内容 Hash 校验：静默修改 Fixture 立即失败（更新须走受控流程）。"""
    manifest = _load_manifest()
    for relative, expected_hash in manifest["files"].items():
        path = FIXTURES_DIR / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected_hash, (
            f"fixture {relative} changed without manifest update; "
            "see tests/fixtures/MANIFEST.md update process"
        )


def test_manifest_yaml_version_matches_md() -> None:
    """MANIFEST.yaml 的版本号必须在 MANIFEST.md 中登记（同步维护守护）。"""
    manifest = _load_manifest()
    md = (FIXTURES_DIR / "MANIFEST.md").read_text("utf-8")
    assert f"清单版本：{manifest['version']}" in md, (
        f"MANIFEST.md must register manifest version {manifest['version']}"
    )


def test_no_secrets_in_fixtures() -> None:
    """Fixture 安全约束：禁止真实 Token/密码形态的字符串（验收 2/9）。"""
    dangerous = ("tushare_token=", "api_key=", "Bearer ", "password\": \"")
    for path in sorted(FIXTURES_DIR.rglob("*.json")):
        text = path.read_text("utf-8", errors="ignore").lower()
        for pattern in dangerous:
            assert pattern.lower() not in text, (
                f"{path.name} may contain a secret-like string ({pattern!r})"
            )
