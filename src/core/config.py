"""应用配置：必填项缺失快速失败，不提供危险默认值。

安全边界：
- 连接密码等敏感字段使用 SecretStr，禁止被日志/序列化泄露；
- TuShare Token、LLM Key 为可选能力项，缺失时能力标记为不可用（DEGRADED），
  不得伪装成可用。

分层：
- PostgresConnectionSettings：仅需 PostgreSQL 连接的入口（Alembic 迁移、
  DB 测试）使用，避免迁移步骤被迫提供 Neo4j 配置；
- Settings：API/Worker 运行时完整配置。
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class PostgresConnectionSettings(BaseSettings):
    """PostgreSQL 连接配置（迁移与测试的最小必需集）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    postgres_host: str
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_port: int = 5432

    @field_validator("postgres_host", "postgres_db", "postgres_user")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @property
    def postgres_dsn(self) -> str:
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql://{self.postgres_user}:{quote_plus(password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class Settings(PostgresConnectionSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---- 运行时必填（缺失时启动即失败，逐项列出缺失字段） ----
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr

    # ---- 非敏感、安全默认值 ----
    environment: Literal["local", "ci", "production"] = "local"
    log_level: LogLevel = "INFO"
    ready_connect_timeout_seconds: float = 2.0

    # ---- TuShare 客户端（非敏感参数） ----
    tushare_base_url: str = "https://api.tushare.pro"
    tushare_timeout_seconds: float = 10.0
    tushare_max_retries: int = 3
    tushare_rate_per_minute: int = 60
    tushare_backoff_base_seconds: float = 1.0
    tushare_backoff_cap_seconds: float = 30.0
    ingest_lease_ttl_seconds: int = 600

    # ---- Claim 自动接受边界（issue #7 回滚要求：可配置关闭，全部转人工）----
    claim_auto_accept: bool = True

    @field_validator("tushare_base_url")
    @classmethod
    def _tushare_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("tushare_base_url must start with http:// or https://")
        return value

    # ---- 可选能力项：缺失 → 能力不可用 ----
    tushare_token: SecretStr | None = None
    llm_api_key: SecretStr | None = None

    @field_validator("tushare_token", "llm_api_key", mode="before")
    @classmethod
    def _empty_optional_secret(cls, value: object) -> object:
        # 环境注入的空字符串（如 compose 的 ${TUSHARE_TOKEN:-}）视为缺失，不得伪装可用
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("neo4j_user")
    @classmethod
    def _neo4j_user_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("neo4j_uri")
    @classmethod
    def _neo4j_scheme(cls, value: str) -> str:
        if not (value.startswith("bolt://") or value.startswith("neo4j://")):
            raise ValueError("neo4j_uri must start with bolt:// or neo4j://")
        return value

    def capability_states(self) -> dict[str, bool]:
        """可选能力可用性；缺失对应 Secret 时为 False，不伪装可用。"""
        return {
            "tushare": self.tushare_token is not None,
            "llm": self.llm_api_key is not None,
        }

    def safe_summary(self) -> dict[str, object]:
        """启动日志可安全输出的配置摘要（不含任何 Secret/密码）。"""
        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "postgres_host": self.postgres_host,
            "postgres_db": self.postgres_db,
            "neo4j_uri": self.neo4j_uri,
            "capabilities": self.capability_states(),
        }
