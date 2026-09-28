"""入口引导测试：配置失败 → 脱敏 stderr + SystemExit(2)，字段可操作。"""

from __future__ import annotations

import pytest
from src.core.bootstrap import load_settings_or_fail

from tests.helpers import ENV_NAMES


def _clear_required_env(monkeypatch):
    for name in ENV_NAMES.values():
        monkeypatch.delenv(name, raising=False)


def test_missing_config_exits_2_with_sanitized_actionable_message(monkeypatch, capsys):
    _clear_required_env(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        load_settings_or_fail(_env_file=None)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "invalid configuration" in stderr
    for missing in ("postgres_host", "postgres_db", "postgres_password", "neo4j_uri"):
        assert missing in stderr  # 可操作：逐项列出缺失字段
    # 不再输出 pydantic traceback（原始输入值可能内嵌凭据）
    assert "Traceback" not in stderr


def test_rejected_value_with_embedded_credentials_is_masked_in_stderr(monkeypatch, capsys):
    _clear_required_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ontology")
    monkeypatch.setenv("POSTGRES_USER", "ontology")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw-123")
    # 非法 scheme → pydantic 会把原始值回显进错误文本；入口必须脱敏
    monkeypatch.setenv("NEO4J_URI", "http://user:pw-neo@host:7474")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-123")

    with pytest.raises(SystemExit) as excinfo:
        load_settings_or_fail(_env_file=None)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "neo4j_uri" in stderr
    assert "pw-neo" not in stderr
    assert "user:***@host" in stderr  # 凭据已掩码，主机名保留可操作性
