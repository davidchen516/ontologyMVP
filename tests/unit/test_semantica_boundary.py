"""ADR-0003 边界强制：业务模块禁止导入 Semantica（AST 静态检查）。

唯一豁免：src/semantic/semantica_adapter.py（本阶段尚不存在）及其测试。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src", "apps")
ALLOWED_RELATIVE = {Path("src/semantic/semantica_adapter.py")}


def semantica_imports(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "semantica" for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "semantica":
                lines.append(node.lineno)
    return lines


def test_no_business_module_imports_semantica():
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT)
            if relative in ALLOWED_RELATIVE:
                continue
            for lineno in semantica_imports(path):
                violations.append(f"{relative}:{lineno}")
    assert not violations, (
        f"违反 ADR-0003：以下模块直接导入了 Semantica（只允许 {ALLOWED_RELATIVE}）："
        f"{violations}"
    )


def test_empty_semantic_runtime_port_exists():
    """空端口 SemanticRuntime 存在且不含 Semantica 依赖。"""
    ports = REPO_ROOT / "src/semantic/ports.py"
    assert ports.exists()
    assert not semantica_imports(ports)


def test_semantica_is_declared_and_locked():
    """Semantica 0.7.0 必须已声明为精确锁版依赖。"""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"semantica==0.7.0"' in pyproject
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "semantica"' in lock
    assert 'version = "0.7.0"' in lock
