"""真实低层级 TuShare 账户一键端到端冒烟（issue #45）。

一次有界运行完成：能力探针 → 采集（stock_basic + stock_company + 财务三表
各 1 批）→ 标准化 → 真实档案文本证据/Claim → 查询 API /api/v1/screen
返回真实公司，并产出脱敏数据质量报告 + 额度消耗记录。

安全约束（issue #45）：
- 真实 token 只经环境变量注入（TUSHARE_TOKEN），不进 CLI 参数/日志/报告；
- 写入一次性数据库（SMOKE_DB，默认 real_smoke），不触碰共享演示库；
- 报告只记字段名/字段顺序/计数/比率，不落全量证券数据。

用法（compose 栈，推荐——环境与 .env 注入一致）：
  docker compose run --rm api /app/.venv/bin/python scripts/real_account_smoke.py

退出码：0 = 全部端到端不变量满足；2 = 配置/前置失败；3 = 不变量未满足
（如实报告，绝不假装成功）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 冒烟数据集（issue #45 范围：主数据 + 财务三表；fina_indicator_vip 为
# 财务补充探针）。probe/采集各 1 次请求，额度有界。
SMOKE_DATASETS = [
    "stock_basic", "stock_company",
    "income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip",
]
# balancesheet_vip 无标准化处理器（processors.py 注册表无此名——与
# build_mvp_snapshot DATASETS_TO_RUN 一致），只采到 raw 供形态/质量分析。
NORMALIZE_ORDER = [
    "stock_basic", "stock_company", "income_vip", "cashflow_vip",
    "fina_indicator_vip",
]
LEASE_TTL = 300
SCREEN_PORT = 8077

# Claim 候选公司（真实证券，研发密集型档案文本——规则抽取器阶段词表
# 的现实命中目标）。逐个尝试（每家 stock_basic+stock_company 各 1 请求），
# 首家档案文本可抽取者胜出；全部未命中则如实报告不变量失败。
CANDIDATE_TS_CODES = ["002230.SZ", "000063.SZ", "000333.SZ"]


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def _create_disposable_db(settings: Any, smoke_db: str) -> None:
    """DROP + CREATE 一次性库（复跑幂等）；共享库零写入。"""
    import psycopg

    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{smoke_db}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{smoke_db}"')


def _run_migrations(smoke_db: str) -> None:
    """alembic upgrade head（env POSTGRES_* 已指向一次性库）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # alembic.ini 的 script_location 相对路径以 cwd 解析
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def _build_connectors(
    settings: Any, transport: Any,
) -> dict[str, Any]:
    from src.connectors.datasets import load_datasets
    from src.connectors.tushare_connectors import TushareConnector

    datasets = load_datasets()
    token = (
        settings.tushare_token.get_secret_value()
        if settings.tushare_token is not None else None
    )
    return {
        api: TushareConnector(
            datasets[api], settings=settings, token=token, transport=transport,
        )
        for api in SMOKE_DATASETS
    }


def _phase_probe(uow_factory: Any, connectors: dict[str, Any]) -> dict[str, Any]:
    """6 数据集能力探针（各 1 请求）：状态 + expected 缺失层级如实记录。"""
    from src.connectors.probes import probe_connector

    section: dict[str, Any] = {"requests": 0, "datasets": {}}
    for api, connector in connectors.items():
        result = probe_connector(uow_factory, connector)
        section["requests"] += 1
        entry: dict[str, Any] = {
            "status": result.status.value,
            "missing_expected_fields": result.metadata.get(
                "missing_expected_fields", []),
            "schema_signature": result.schema_signature,
        }
        if result.error_code:
            entry["error_code"] = result.error_code
        section["datasets"][api] = entry
    return section


def _pinned_connector(
    settings: Any, transport: Any, api_name: str, params: dict[str, Any],
) -> Any:
    """按冒烟参数钉住的数据集连接器（smoke 本地覆写，不改主链路）。

    探针参数注册表是探针导向的（如 stock_basic limit=1、stock_company
    固定 000001.SZ）；真实端到端需要主数据-公司-财务在同一 ts_code 上
    可连接——这里用 dataclasses.replace 在冒烟脚本内钉住参数。
    """
    import dataclasses

    from src.connectors.datasets import load_datasets
    from src.connectors.tushare_connectors import TushareConnector

    base = load_datasets()[api_name]
    config = dataclasses.replace(base, probe_params=params)
    token = (
        settings.tushare_token.get_secret_value()
        if settings.tushare_token is not None else None
    )
    return TushareConnector(
        config, settings=settings, token=token, transport=transport,
    )


def _text_yields_claim(texts: list[str]) -> bool:
    """轻量预检：真实档案文本是否命中规则抽取器（不写库）。"""
    import uuid as _uuid

    from src.claims.extractor import RulesExtractor

    fragments = [
        {"id": _uuid.uuid4(), "document_version_id": _uuid.uuid4(),
         "company_id": _uuid.uuid4(), "page_number": index + 1,
         "char_start": 0, "char_end": len(text), "text": text}
        for index, text in enumerate(texts)
    ]
    return bool(RulesExtractor().extract(fragments, ontology_version="0.1.0").claims)


def _raw_texts(uow_factory: Any, api_name: str, ts_code: str) -> list[str]:
    """读 raw 层某证券的档案文本字段（真实 TuShare 数据）。"""
    fields = ("introduction", "main_business")
    with uow_factory.transaction() as uow:
        row = uow._conn.execute(  # noqa: SLF001
            "SELECT raw_payload FROM raw.source_record "
            "WHERE api_name = %s AND raw_payload->>'ts_code' = %s LIMIT 1",
            (api_name, ts_code),
        ).fetchone()
    if row is None:
        return []
    return [
        str(row[0][f]) for f in fields
        if str(row[0].get(f) or "").strip()
    ]


def _ingest_one(
    uow_factory: Any, connector: Any, dataset_name: str,
) -> dict[str, Any]:
    """单数据集单批采集（有界：max_batches=1）。"""
    from src.connectors.ingest import ingest_dataset, start_run

    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name=dataset_name,
            trace_id=f"real-smoke-{dataset_name}",
            lease_owner="real-account-smoke", lease_ttl_seconds=LEASE_TTL,
        )
    outcome = ingest_dataset(
        uow_factory, connector, run_id=run["id"],
        lease_ttl_seconds=LEASE_TTL, max_batches=1,
    )
    return {
        "status": outcome.status.value,
        "rows_received": outcome.rows_received,
        "rows_inserted": outcome.rows_inserted,
        "rows_rejected": outcome.rows_rejected,
        "request_count": outcome.request_count,
    }


def _phase_ingest(
    uow_factory: Any, connectors: dict[str, Any],
    probe_section: dict[str, Any], settings: Any, transport: Any,
) -> dict[str, Any]:
    """有界采集：默认参数路径 + 候选公司钉住路径 + 胜出者财务。

    财务 vip 接口按 period 全市场分页——冒烟把 ts_code 钉住到胜出候选，
    使主数据/公司/财务/Claim/screen 在同一真实公司上闭环。
    """
    section: dict[str, Any] = {
        "requests": 0, "datasets": {}, "skipped": [],
        "candidates_tried": [], "winner": None,
    }

    def available(api: str) -> bool:
        probe = probe_section["datasets"].get(api, {})
        if probe.get("status") != "AVAILABLE":
            section["skipped"].append(
                {"dataset": api, "reason": f"probe status {probe.get('status')}"}
            )
            return False
        return True

    def merge(api: str, entry: dict[str, Any]) -> None:
        merged = section["datasets"].setdefault(api, {
            "status": entry["status"], "rows_received": 0,
            "rows_inserted": 0, "rows_rejected": 0, "request_count": 0,
        })
        merged["status"] = entry["status"]  # 最新一批状态
        for key in ("rows_received", "rows_inserted", "rows_rejected",
                    "request_count"):
            merged[key] = merged.get(key, 0) + entry[key]
        section["requests"] += entry["request_count"]

    # 1. 默认参数路径（探针参数原样采集——证明默认链路真实可用）
    for api in ("stock_basic", "stock_company"):
        if not available(api):
            continue
        merge(api, _ingest_one(uow_factory, connectors[api], api))

    # 2. 候选公司（钉住 ts_code）：证券 + 档案；档案可抽取者胜出
    for ts_code in CANDIDATE_TS_CODES:
        if not available("stock_basic") or not available("stock_company"):
            break
        section["candidates_tried"].append(ts_code)
        for api in ("stock_basic", "stock_company"):
            connector = _pinned_connector(
                settings, transport, api, {"ts_code": ts_code},
            )
            merge(api, _ingest_one(uow_factory, connector, api))
        if _text_yields_claim(_raw_texts(uow_factory, "stock_company", ts_code)):
            section["winner"] = ts_code
            break

    # 3. 胜出者财务（钉住 period+ts_code：单批即该公司全量行）
    financials = ["income_vip", "balancesheet_vip", "cashflow_vip",
                  "fina_indicator_vip"]
    if section["winner"] is not None:
        for api in financials:
            if not available(api):
                continue
            connector = _pinned_connector(
                settings, transport, api,
                {"period": "20251231", "ts_code": section["winner"]},
            )
            merge(api, _ingest_one(uow_factory, connector, api))
    else:
        for api in financials:
            section["skipped"].append(
                {"dataset": api, "reason": "no claim candidate matched"}
            )
    return section


def _phase_normalize(uow_factory: Any) -> dict[str, Any]:
    """标准化（顺序：证券 → 公司 → 财务）。balancesheet_vip 无处理器跳过。"""
    from src.standardize.pipeline import (
        normalize_dataset,
        start_normalization_run,
    )

    section: dict[str, Any] = {"datasets": {}, "skipped": ["balancesheet_vip"]}
    for api in NORMALIZE_ORDER:
        with uow_factory.transaction() as uow:
            norm_run = start_normalization_run(
                uow, dataset_name=api,
                trace_id=f"real-smoke-norm-{api}",
                lease_owner="real-account-smoke", lease_ttl_seconds=LEASE_TTL,
            )
        norm = normalize_dataset(
            uow_factory, api, run_id=norm_run["id"], lease_ttl_seconds=LEASE_TTL,
        )
        section["datasets"][api] = {
            "status": norm.status.value,
            "rows_written": norm.rows_written,
            "rows_rejected": norm.rows_rejected,
        }
    return section


def _phase_claims(uow_factory: Any) -> dict[str, Any]:
    """真实档案文本 → 证据片段 → 规则抽取 → 真实 intake（auto-accept）。

    Claim 证据 = stock_company 返回的公司简介/主营文本（真实 TuShare 数据，
    非 Fabricated INSERT）；抽取与 intake 走与合成快照完全相同的生产路径
    （校验/幂等/冲突检测/Outbox）。
    """
    from src.claims.extractor import RulesExtractor
    from src.claims.review import intake_candidate

    section: dict[str, Any] = {
        "documents": 0, "fragments": 0, "candidates": 0, "accepted": 0,
    }
    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT c.id AS company_id, c.canonical_name, s.id AS security_id, "
            "s.ts_code FROM master.company c "
            "JOIN master.company_security cs ON cs.company_id = c.id "
            "JOIN master.security s ON s.id = cs.security_id "
            "ORDER BY c.canonical_name LIMIT 10"
        ).fetchall()
    if not rows:
        return section

    for company_id, company_name, security_id, ts_code in rows:
        with uow_factory.transaction() as uow:
            raw = uow._conn.execute(  # noqa: SLF001
                "SELECT raw_payload FROM raw.source_record "
                "WHERE api_name = 'stock_company' "
                "AND raw_payload->>'ts_code' = %s LIMIT 1", (ts_code,),
            ).fetchone()
        if raw is None:
            continue
        payload = raw[0]
        texts = [
            str(payload[field])
            for field in ("introduction", "main_business")
            if str(payload.get(field) or "").strip()
        ]
        if not texts:
            continue

        with uow_factory.transaction() as uow:
            document = uow.documents.insert(
                document_type="COMPANY_PROFILE", source_system="TUSHARE",
                title=f"{company_name} 公司档案（stock_company）",
                content_hash=_sha256(f"tushare:stock_company:{ts_code}"),
            )
            version = uow.document_versions.create_version(
                document_id=document["id"],
                source_url="https://api.tushare.pro/data/v1/get?api_name=stock_company",
                content_hash=_sha256(f"tushare:stock_company:{ts_code}:v1"),
            )
            section["documents"] += 1
            fragments: list[dict[str, Any]] = []
            for index, text in enumerate(texts):
                fragment = uow.claim_evidence.insert_evidence_fragment(
                    document_id=document["id"], page_number=index + 1,
                    quote_text=text,
                    checksum=_sha256(f"frag:{ts_code}:{index}:{text[:32]}"),
                )
                uow._conn.execute(  # noqa: SLF001
                    "UPDATE fact.evidence_fragment SET document_version_id = %s, "
                    "char_start = %s, char_end = %s WHERE id = %s",
                    (version["id"], 0, len(text), fragment["id"]),
                )
                fragments.append({
                    "id": fragment["id"], "document_version_id": version["id"],
                    "company_id": company_id, "page_number": index + 1,
                    "char_start": 0, "char_end": len(text), "text": text,
                })
                section["fragments"] += 1

        output = RulesExtractor().extract(fragments, ontology_version="0.1.0")
        section["candidates"] += len(output.claims)
        checksums = [
            _sha256(f"frag:{ts_code}:{i}:{t[:32]}")
            for i, t in enumerate(texts)
        ]
        for candidate in output.claims:
            with uow_factory.transaction() as uow:
                outcome = intake_candidate(
                    uow, candidate,
                    document_version_id=version["id"],
                    fragment_checksums=checksums,
                    security_id=security_id,
                    trace_id="real-account-smoke",
                    auto_accept=True,
                    shacl_validate=False,
                    check_conflicts=True,
                )
            if outcome.status == "ACCEPTED":
                section["accepted"] += 1
    return section


def _phase_master_stats(uow_factory: Any) -> dict[str, Any]:
    """主数据计数 + list_status 降级分布（#43 UNKNOWN 语义可观测）。"""
    with uow_factory.transaction() as uow:
        securities = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*), count(*) FILTER (WHERE status = 'UNKNOWN'), "
            "count(*) FILTER (WHERE status = 'ACTIVE') FROM master.security"
        ).fetchone()
        companies = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM master.company"
        ).fetchone()
        financials = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM finance.financial_observation"
        ).fetchone()
        # 脱敏形态记录：字段名集合（无序，无值——jsonb 不保序，仅记存在性）
        field_shapes = uow._conn.execute(  # noqa: SLF001
            "SELECT api_name, array_agg(DISTINCT k) FROM raw.source_record, "
            "jsonb_object_keys(raw_payload) AS k GROUP BY api_name"
        ).fetchall()
    status_unknown = securities[1]
    total_securities = securities[0]
    return {
        "master_security_total": total_securities,
        "master_security_status_unknown": status_unknown,
        "master_security_status_active": securities[2],
        "master_company_total": companies[0],
        "financial_observation_total": financials[0],
        # 脱敏形态记录：字段名集合（无值、无序——jsonb 不保留响应字段顺序）
        "raw_field_shapes": {row[0]: list(row[1]) for row in field_shapes},
    }


def _phase_screen(smoke_db: str) -> dict[str, Any]:
    """真实 HTTP 查询 API：uvicorn 子进程 + POST /api/v1/screen。"""
    import httpx

    env = dict(os.environ)
    env["POSTGRES_DB"] = smoke_db
    uvicorn_bin = os.environ.get("SMOKE_UVICORN", "/app/.venv/bin/uvicorn")
    proc = subprocess.Popen(
        [
            uvicorn_bin, "apps.api.main:app",
            "--host", "127.0.0.1", "--port", str(SCREEN_PORT), "--no-access-log",
        ],
        env=env, cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{SCREEN_PORT}"
    try:
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            try:
                with httpx.Client(timeout=2) as client:
                    if client.get(f"{base}/healthz").status_code == 200:
                        ready = True
                        break
            except httpx.HTTPError:
                time.sleep(1)
        if not ready:
            return {"error": "api did not become healthy within 60s"}

        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{base}/api/v1/screen", json={"max_results": 10})
        body = resp.json()
        results = body.get("results", [])
        return {
            "http_status": resp.status_code,
            "query_status": body.get("status"),
            "result_count": len(results),
            "companies": [
                {
                    "company_name": r.get("company_name"),
                    "security_code": r.get("security_code"),
                    "claim_ids": len(r.get("claim_ids") or []),
                    "evidence_quotes": len(r.get("evidence_quotes") or []),
                    "business_stage": r.get("business_stage"),
                }
                for r in results[:5]
            ],
        }
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _check_invariants(report: dict[str, Any]) -> list[str]:
    """端到端不变量（issue #45 核心验收）——失败项如实列出。"""
    failures: list[str] = []
    master = report["master"]
    if master["master_security_total"] <= 0:
        failures.append("master.security == 0")
    if master["master_company_total"] <= 0:
        failures.append("master.company == 0")
    screen = report["screen"]
    if screen.get("http_status") not in (200, None):  # stub 路径无 http_status
        failures.append(f"/api/v1/screen http {screen.get('http_status')}")
    if screen.get("result_count", 0) < 1:
        failures.append(f"/api/v1/screen returned 0 real companies ({screen})")
    else:
        for company in screen["companies"]:
            if not company["claim_ids"]:
                failures.append(
                    f"screen result {company['company_name']} has no claims"
                )
    for api, entry in report["probe"]["datasets"].items():
        if entry["status"] == "SCHEMA_CHANGED":
            failures.append(f"dataset {api} fused SCHEMA_CHANGED (regression)")
    return failures


def _render_markdown(report: dict[str, Any], failures: list[str]) -> str:
    lines: list[str] = [
        "# 真实账户冒烟报告（自动生成，脱敏）", "",
        f"- 运行时间：{report['meta']['started_at']}",
        f"- 数据模式：{report['meta']['data_mode']}",
        f"- 额度消耗（请求数）：探针 {report['probe']['requests']} + "
        f"采集 {report['ingest']['requests']} = "
        f"{report['probe']['requests'] + report['ingest']['requests']}", "",
        "## 探针（能力矩阵）", "",
        "| 数据集 | 状态 | expected 缺失 |",
        "|---|---|---|",
    ]
    for api, entry in report["probe"]["datasets"].items():
        missing = ", ".join(entry["missing_expected_fields"]) or "-"
        lines.append(f"| {api} | {entry['status']} | {missing} |")
    lines += ["", "## 采集（默认路径 + 候选钉住路径，每批 1 请求）", "",
              "| 数据集 | 状态 | 行数(收/入/拒) | 请求数 |", "|---|---|---|---|"]
    for api, entry in report["ingest"]["datasets"].items():
        lines.append(
            f"| {api} | {entry['status']} | "
            f"{entry['rows_received']}/{entry['rows_inserted']}/"
            f"{entry['rows_rejected']} | {entry['request_count']} |"
        )
    for skip in report["ingest"]["skipped"]:
        lines.append(f"| {skip['dataset']} | SKIPPED | - | 0 |")
    candidates = report["ingest"].get("candidates_tried") or []
    winner = report["ingest"].get("winner")
    lines += ["", f"Claim 候选尝试：{'、'.join(candidates) or '-'}；"
              f"胜出（真实档案文本可抽取）：{winner or '无（不变量将如实失败）'}"]
    lines += ["", "## 标准化", "", "| 数据集 | 写入 | 拒绝 |", "|---|---|---|"]
    for api, entry in report["normalize"]["datasets"].items():
        lines.append(f"| {api} | {entry['rows_written']} | {entry['rows_rejected']} |")
    claims = report["claims"]
    lines += [
        "", "## 证据/Claim（真实档案文本 → 规则抽取 → intake）", "",
        f"文档 {claims['documents']} / 片段 {claims['fragments']} / "
        f"候选 {claims['candidates']} / 接受 {claims['accepted']}",
        "", "## 主数据与质量分布", "",
    ]
    for key, value in report["master"].items():
        if key != "raw_field_shapes":
            lines.append(f"- {key}: {value}")
    lines += ["", "### 真实响应字段形态（仅字段名，脱敏）", ""]
    for api, fields in report["master"]["raw_field_shapes"].items():
        lines.append(f"- `{api}`: {fields}")
    lines += ["", "## 查询 API", ""]
    lines.append(f"```json\n{json.dumps(report['screen'], ensure_ascii=False, indent=1)}\n```")
    lines += ["", "## 不变量校验", ""]
    if failures:
        lines += [f"❌ {f}" for f in failures]
    else:
        lines.append("✅ 全部端到端不变量满足（security>0 / company>0 / "
                     "screen≥1 真实公司 / 无 SCHEMA_CHANGED 熔断）")
    return "\n".join(lines) + "\n"


def run(
    *,
    transport: Any | None = None,
    uow_factory: Any | None = None,
    screen_runner: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """执行冒烟并返回 (报告, 不变量失败列表, 退出码)。

    transport/uow_factory/screen_runner 为测试注入点（CI 用 stub transport，
    无网络/token；生产调用方不传）。
    """
    import datetime as dt

    from src.core.bootstrap import load_settings_or_fail

    smoke_db = os.environ.get("SMOKE_DB", "real_smoke")
    settings = load_settings_or_fail()  # token 只经环境变量
    token = (
        settings.tushare_token.get_secret_value()
        if settings.tushare_token is not None else None
    )
    injected_transport = transport is not None  # 测试注入 → data_mode=stub

    # 负向场景前置检查：缺 token 时不得触碰任何数据库（明确报告退出）
    if not injected_transport and not token:
        report = {"meta": {
            "started_at": dt.datetime.now(dt.UTC).isoformat(),
            "data_mode": "no-token", "smoke_db": smoke_db,
        }}
        return report, ["TUSHARE_TOKEN not set"], 2

    if uow_factory is None:
        _create_disposable_db(settings, smoke_db)
        os.environ["POSTGRES_DB"] = smoke_db
        settings = load_settings_or_fail()
        _run_migrations(smoke_db)
        from src.db.uow import UnitOfWorkFactory

        uow_factory = UnitOfWorkFactory(settings.postgres_dsn)
    if transport is None:
        from src.connectors.tushare_client import http_transport

        transport = http_transport(
            settings.tushare_base_url, token or "",
            settings.tushare_timeout_seconds,
        )

    report: dict[str, Any] = {
        "meta": {
            "started_at": dt.datetime.now(dt.UTC).isoformat(),
            # 真实模式 = 本函数自建 http_transport（测试注入 stub 时为 stub）
            "data_mode": "real" if not injected_transport and token else "stub",
            "smoke_db": smoke_db,
        }
    }
    connectors = _build_connectors(settings, transport)
    report["probe"] = _phase_probe(uow_factory, connectors)
    report["ingest"] = _phase_ingest(
        uow_factory, connectors, report["probe"], settings, transport,
    )
    report["normalize"] = _phase_normalize(uow_factory)
    report["claims"] = _phase_claims(uow_factory)
    report["master"] = _phase_master_stats(uow_factory)
    report["screen"] = (
        screen_runner(smoke_db) if screen_runner is not None
        else _phase_screen(smoke_db)
    )
    failures = _check_invariants(report)
    exit_code = 0 if not failures else 3
    return report, failures, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-out", default=None,
        help="报告输出路径（markdown，默认 stdout 打印 JSON 摘要）",
    )
    args = parser.parse_args()

    report, failures, exit_code = run()
    print(json.dumps({
        "meta": report.get("meta"),
        "probe_requests": report.get("probe", {}).get("requests"),
        "ingest_requests": report.get("ingest", {}).get("requests"),
        "failures": failures,
    }, ensure_ascii=False))
    if args.report_out:
        Path(args.report_out).write_text(
            _render_markdown(report, failures), encoding="utf-8",
        )
        print(f"report written: {args.report_out}")
    else:
        print(_render_markdown(report, failures))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
