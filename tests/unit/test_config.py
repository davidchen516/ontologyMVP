"""配置边界测试：必填快速失败、无危险默认值、Secret 不泄露、能力状态语义。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_core import PydanticUndefined
from src.core.config import Settings

from tests.helpers import BASE, ENV_NAMES


def test_missing_required_field_fails_fast_with_actionable_message(monkeypatch):
    for name in ENV_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")

    with pytest.raises(ValidationError) as excinfo:
        Settings()
    message = str(excinfo.value)
    # 可操作错误：逐项列出缺失字段名
    for missing in ("postgres_db", "postgres_user", "postgres_password", "neo4j_uri",
                    "neo4j_user", "neo4j_password"):
        assert missing in message


def test_no_dangerous_defaults_on_required_fields():
    """必填字段不允许默认值（防止静默连接到错误环境）。"""
    required = [f for f in Settings.model_fields if f in ENV_NAMES and f != "postgres_port"]
    assert required, "ENV_NAMES 与模型字段对齐失败"
    for field_name in required:
        field = Settings.model_fields[field_name]
        assert field.default is PydanticUndefined, f"{field_name} 不应有默认值"


def test_settings_load_from_environment(monkeypatch):
    for attr, env_name in ENV_NAMES.items():
        monkeypatch.setenv(env_name, str(BASE[attr]))
    monkeypatch.setenv("ENVIRONMENT", "ci")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings()
    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 5432
    assert settings.environment == "ci"
    assert settings.log_level == "WARNING"


def test_postgres_dsn_includes_url_quoted_password():
    from tests.helpers import make_settings

    settings = make_settings(
        postgres_password="p@ss word", postgres_host="h", postgres_port=5433
    )
    assert settings.postgres_dsn == "postgresql://ontology:p%40ss+word@h:5433/ontology"


def test_secrets_not_leaked_by_repr_or_dump():
    from tests.helpers import make_settings

    settings = make_settings(
        postgres_password="super-secret-pw", tushare_token=SecretStr("tok-123")
    )
    assert "super-secret-pw" not in repr(settings)
    assert "tok-123" not in repr(settings)
    assert "super-secret-pw" not in str(settings.model_dump())
    assert "tok-123" not in str(settings.model_dump())


def test_frozen_settings_reject_mutation():
    from tests.helpers import make_settings

    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


@pytest.mark.parametrize("bad", ["noisy", "trace", "invalid-level"])
def test_invalid_log_level_rejected(bad):
    from tests.helpers import make_settings

    with pytest.raises(ValidationError):
        make_settings(log_level=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["http://localhost:7474", "localhost:7687", ""])
def test_invalid_neo4j_uri_rejected(bad):
    from tests.helpers import make_settings

    with pytest.raises(ValidationError):
        make_settings(neo4j_uri=bad)


def test_blank_required_values_rejected():
    from tests.helpers import make_settings

    with pytest.raises(ValidationError):
        make_settings(postgres_host="   ")


def test_capabilities_missing_token_means_unavailable():
    from tests.helpers import make_settings

    without = make_settings()
    assert without.capability_states() == {"tushare": False, "llm": False}

    with_both = make_settings(
        tushare_token=SecretStr("t"), llm_api_key=SecretStr("k")
    )
    assert with_both.capability_states() == {"tushare": True, "llm": True}


def test_empty_string_token_is_unavailable():
    """compose 注入的空字符串 Token 必须视为缺失，不得伪装可用。"""
    from tests.helpers import make_settings

    settings = make_settings(tushare_token="", llm_api_key="")
    assert settings.capability_states() == {"tushare": False, "llm": False}
