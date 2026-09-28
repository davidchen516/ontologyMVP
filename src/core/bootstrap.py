"""进程入口引导：配置加载失败时输出脱敏、可操作的错误。

为什么存在：pydantic 的 ValidationError 发生在 configure_logging 之前，
traceback 会把被拒绝的原始输入值直接回显到 stderr（可能内嵌凭据）；
入口必须先经 sanitize_text 脱敏再输出，并以非零退出码快速失败。
"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from src.core.config import Settings
from src.core.logging import sanitize_text


def load_settings_or_fail(**overrides: object) -> Settings:
    """生产入口使用：缺失/非法必填配置 → SystemExit(2)，stderr 输出脱敏错误。"""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        sanitized = sanitize_text(str(exc), limit=2000)
        print("startup failed: invalid configuration\n" + sanitized, file=sys.stderr)
        raise SystemExit(2) from None
