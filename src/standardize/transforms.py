"""共享值转换：与 ontology/mappings/tushare.yaml common 的口径一致。

安全边界（issue #4 不变量）：
- null_values 视为缺失 → None；但财务缺失值保持 None，绝不转 0；
- 日期解析失败抛 TransformError（进拒绝队列，不静默纠正）；
- 币种未知保持 None，不得默认 CNY。
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml

from src.connectors.datasets import MAPPING_FILE

NULL_VALUES = frozenset({None, "", "None", "null", "NULL"})
DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d")
TS_CODE_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")

_MAPPINGS = None


class TransformError(Exception):
    """无法解析的值：调用方必须记入拒绝/审核事件，不得静默纠正。"""


def mapping_version() -> str:
    """映射版本号来自 tushare.yaml 的 version 字段（单一事实源）。"""
    global _MAPPINGS
    if _MAPPINGS is None:
        _MAPPINGS = yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8"))
    return f"tushare-mappings-{_MAPPINGS['version']}"


def is_null(value: Any) -> bool:
    return value in NULL_VALUES or (isinstance(value, str) and value.strip() in NULL_VALUES)


def clean_str(value: Any) -> str | None:
    if is_null(value):
        return None
    return str(value).strip()


def upper_trim(value: Any) -> str | None:
    cleaned = clean_str(value)
    return cleaned.upper() if cleaned is not None else None


def parse_date(value: Any) -> dt.date | None:
    """解析 TuShare 日期；空值 None；无法解析抛 TransformError。"""
    if is_null(value):
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    raise TransformError(f"unparseable date: {value!r}")


def decimal_or_null(value: Any) -> Decimal | None:
    if is_null(value):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise TransformError(f"unparseable decimal: {value!r}") from exc


def validate_ts_code(value: Any) -> str:
    code = upper_trim(value)
    if code is None or not TS_CODE_PATTERN.fullmatch(code):
        raise TransformError(f"invalid ts_code: {value!r}")
    return code
