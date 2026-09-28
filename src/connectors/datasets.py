"""TuShare 数据集注册表：以 ontology/mappings/tushare.yaml 为唯一事实源。

required_fields 用于：
- Fixture 契约测试（fixtures 字段必须覆盖注册表要求）；
- Schema 签名熔断（响应字段布局变化 → SCHEMA_CHANGED）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_FILE = REPO_ROOT / "ontology" / "mappings" / "tushare.yaml"

# 能力探针的最小请求参数（每接口一次最小调用）；None = 需要独立权限的接口，
# 只做权限探测、不假设可用（issue #3）
PROBE_PARAMS: dict[str, dict[str, Any] | None] = {
    "stock_basic": {"list_status": "L", "limit": "1"},
    "stock_company": {"ts_code": "000001.SZ"},
    "namechange": {"ts_code": "000001.SZ"},
    "trade_cal": {"start_date": "20260101", "end_date": "20260102"},
    "index_classify": {"level": "L1", "src": "SW2021"},
    "index_member_all": {"ts_code": "000001.SZ"},
    "ths_index": None,
    "ths_member": None,
    "dc_index": None,
    "dc_member": None,
    "fina_mainbz": {"ts_code": "000001.SZ"},
    "fina_mainbz_vip": {"period": "20251231", "limit": "1"},
    "income_vip": {"period": "20251231", "limit": "1"},
    "balancesheet_vip": {"period": "20251231", "limit": "1"},
    "cashflow_vip": {"period": "20251231", "limit": "1"},
    "fina_indicator_vip": {"period": "20251231", "limit": "1"},
    "top10_holders": {"ts_code": "000001.SZ"},
    "stk_surv": {"ts_code": "000001.SZ"},
    "anns_d": None,  # 可能需要独立权限：只探测
    "irm_qa_sh": None,  # 互动接口：只探测
    "irm_qa_sz": None,  # 互动接口：只探测
}

# Raw 采集的分页配置：True = offset/limit 分页；False = 单次全量
PAGINATED: dict[str, bool] = {
    "fina_mainbz_vip": True,
    "income_vip": True,
    "balancesheet_vip": True,
    "cashflow_vip": True,
    "fina_indicator_vip": True,
    "anns_d": True,
}
DEFAULT_PAGE_SIZE = 1000


@dataclass(frozen=True)
class DatasetConfig:
    api_name: str
    target: str
    required_fields: tuple[str, ...]
    probe_params: dict[str, Any] | None
    paginated: bool
    page_size: int = DEFAULT_PAGE_SIZE
    probe_only: bool = field(default=False)

    @property
    def ingested(self) -> bool:
        """probe_only 接口只做权限探测，不做 Raw 采集。"""
        return not self.probe_only


@lru_cache(maxsize=1)
def load_datasets() -> dict[str, DatasetConfig]:
    """从映射 YAML 加载数据集注册表（进程内缓存；测试可用 reload_datasets 失效）。"""
    raw = yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8"))
    datasets: dict[str, DatasetConfig] = {}
    for api_name, spec in raw["datasets"].items():
        probe_params = PROBE_PARAMS.get(api_name, None)
        probe_only = probe_params is None
        datasets[api_name] = DatasetConfig(
            api_name=api_name,
            target=str(spec.get("target", "")),
            required_fields=tuple(spec.get("required_fields", [])),
            probe_params=probe_params,
            paginated=PAGINATED.get(api_name, False),
            probe_only=probe_only,
        )
    return datasets


def reload_datasets() -> dict[str, DatasetConfig]:
    load_datasets.cache_clear()
    return load_datasets()


def schema_signature_for_fields(fields: list[str]) -> str:
    """字段布局签名：对有序去重字段名做 sha256（前 64 位十六进制）。"""
    seen: list[str] = []
    for f in fields:
        if f not in seen:
            seen.append(f)
    canonical = json.dumps(seen, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payload_hash_for_row(row: dict[str, Any]) -> str:
    """行级 Payload Hash：sha256(canonical JSON)。空值与字符串保持原样语义。"""
    canonical = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
