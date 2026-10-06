"""真实全市场数据管道（issue #56）：探针 → 全量采集 → 标准化 → Neo4j 全量投影。

与 build_mvp_snapshot（演示切片工具：合成 30 家"辛示"公司，claim/证据为
演示专属）分流——本脚本只跑真实 TuShare 数据（token 经环境变量注入），
claim/证据为空是已知代价（issue #56 非目标）；公司-产品经营边从真实
fina_mainbz_vip 主营构成直接物化（非 claim 驱动——projector 的经营边
以 claim_id 为幂等键，真实数据无 claim，故走与演示快照一致的直接物化）。

用法（compose 栈，宿主机挂载；镜像不含 scripts/）：
  docker compose run --rm -v "$PWD:/host" api \
    /app/.venv/bin/python /host/scripts/real_data_pipeline.py \
    [--company-limit 300] [--fresh] [--report-out /host/real_pipeline_report.md]

--company-limit：stock_company 逐只采集的证券数（按 ts_code 序，默认 300，
0=全量 ~5000 只 @限频≈85 分钟）；其余数据集（stock_basic/财务三表/主营
构成）均为按期全市场分页（每表 ~⌈N/1000⌉ 请求）。
--fresh：DROP+CREATE 一次性库（默认复跑幂等：不重建，直接增量采集）。

退出码：0 全部不变量满足；2 配置失败；3 不变量未满足（如实列出，不假装成功）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PIPELINE_DATASETS = [
    "stock_basic", "stock_company",
    "income_vip", "cashflow_vip", "fina_indicator_vip", "fina_mainbz_vip",
]
# 全市场分页数据集（默认配置即按 period 全市场；采集页数由数据量决定）
BATCH_DATASETS = [
    "stock_basic", "income_vip", "cashflow_vip", "fina_indicator_vip",
    "fina_mainbz_vip",
]
NORMALIZE_ORDER = [
    "stock_basic", "stock_company", "income_vip", "cashflow_vip",
    "fina_indicator_vip", "fina_mainbz_vip",
]
LEASE_TTL = 300


def _ingest_one(
    uow_factory: Any, connector: Any, dataset_name: str, *,
    max_batches: int = 1000,
) -> dict[str, Any]:
    from src.connectors.ingest import ingest_dataset, start_run

    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name=dataset_name,
            trace_id=f"real-pipeline-{dataset_name}-{time.strftime('%H%M%S')}",
            lease_owner="real-data-pipeline", lease_ttl_seconds=LEASE_TTL,
        )
    outcome = ingest_dataset(
        uow_factory, connector, run_id=run["id"],
        lease_ttl_seconds=LEASE_TTL, max_batches=max_batches,
    )
    return {
        "status": outcome.status.value,
        "rows_received": outcome.rows_received,
        "rows_inserted": outcome.rows_inserted,
        "rows_rejected": outcome.rows_rejected,
        "request_count": outcome.request_count,
    }


def _connector(
    settings: Any, transport: Any, api_name: str, *,
    params: dict[str, Any] | None = None,
    rate_limiter: Any = None,
) -> Any:
    import dataclasses

    from src.connectors.datasets import load_datasets
    from src.connectors.tushare_connectors import TushareConnector

    config = load_datasets()[api_name]
    if params is not None:
        config = dataclasses.replace(config, probe_params=params)
    token = (
        settings.tushare_token.get_secret_value()
        if settings.tushare_token is not None else None
    )
    return TushareConnector(
        config, settings=settings, token=token, transport=transport,
        rate_limiter=rate_limiter,
    )


def _normalize(uow_factory: Any, api_name: str) -> dict[str, Any]:
    from src.standardize.pipeline import normalize_dataset, start_normalization_run

    with uow_factory.transaction() as uow:
        norm_run = start_normalization_run(
            uow, dataset_name=api_name,
            trace_id=f"real-pipeline-norm-{api_name}",
            lease_owner="real-data-pipeline", lease_ttl_seconds=LEASE_TTL,
        )
    norm = normalize_dataset(
        uow_factory, api_name, run_id=norm_run["id"], lease_ttl_seconds=LEASE_TTL,
    )
    return {
        "status": norm.status.value,
        "rows_read": norm.rows_read,
        "rows_written": norm.rows_written,
        "rows_rejected": norm.rows_rejected,
    }


def _phase_probe(uow_factory: Any, settings: Any, transport: Any) -> dict[str, Any]:
    from src.connectors.probes import probe_connector

    section: dict[str, Any] = {"requests": 0, "datasets": {}}
    for api in PIPELINE_DATASETS:
        connector = _connector(settings, transport, api)
        result = probe_connector(uow_factory, connector)
        section["requests"] += 1
        entry: dict[str, Any] = {"status": result.status.value}
        if result.metadata.get("missing_expected_fields"):
            entry["missing_expected_fields"] = result.metadata[
                "missing_expected_fields"]
        if result.error_code:
            entry["error_code"] = result.error_code
        section["datasets"][api] = entry
    return section


def _materialize_products(uow_factory: Any) -> dict[str, Any]:
    """真实主营构成 → master.product 物化（演示快照同模式，词表来自真实数据）。

    P 类（产品构成）business_segment 的去重名称即真实产品词表；
    IRI 前缀 REAL_ 与演示词表（MVP_）区分。名称唯一约束保证幂等。
    """
    import hashlib

    with uow_factory.transaction() as uow:
        segments = uow._conn.execute(  # noqa: SLF001
            "SELECT DISTINCT bso.company_id, bso.raw_segment_name "
            "FROM finance.business_segment_observation bso "
            "WHERE bso.segment_type = 'PRODUCT' AND bso.raw_segment_name IS NOT NULL"
        ).fetchall()
        names = sorted({row[1] for row in segments})
        for name in names:
            iri = ("https://ontology.example.com/product#REAL_"
                   + hashlib.sha256(name.encode()).hexdigest()[:10])
            uow._conn.execute(  # noqa: SLF001
                "INSERT INTO master.product (iri, canonical_name, "
                "ontology_version) VALUES (%s, %s, '0.1.0') "
                "ON CONFLICT (iri) DO NOTHING", (iri, name),
            )
        product_rows = uow._conn.execute(  # noqa: SLF001
            "SELECT canonical_name, id FROM master.product"
        ).fetchall()
        # PG 侧映射闭环：段名与产品名确定性一致（名称即来源，非猜测）——
        # mapped_product_id + mapping_status=ACCEPTED（schema 预留的映射列）
        for name, pid in product_rows:
            uow._conn.execute(  # noqa: SLF001
                "UPDATE finance.business_segment_observation "
                "SET mapped_product_id = %s, mapping_status = 'ACCEPTED' "
                "WHERE segment_type = 'PRODUCT' AND raw_segment_name = %s "
                "AND mapped_product_id IS NULL", (pid, name),
            )
    product_ids = {name: pid for name, pid in product_rows}
    return {
        "product_names": len(names),
        "segments_total": len(segments),
        "product_ids": product_ids,
    }


def _project(
    uow_factory: Any, graph_executor: Any, product_ids: dict[str, Any],
) -> dict[str, Any]:
    """全量投影：清图 → full_rebuild（实体+Claim+claim 边）→ 真实主营构成边。

    经营边物化：projector 的经营边以 claim_id 为幂等键（缺失即拒绝），
    真实数据无 Claim——与演示快照一致走直接物化：
    (Company)-[:PRODUCES {source:'business_segment'}]->(Product)。
    """
    import structlog
    from src.projection.projector import Neo4jProjector
    from src.projection.reconcile import full_rebuild

    log = structlog.get_logger(__name__)

    graph_executor.execute("MATCH (n) DETACH DELETE n", {})
    projector = Neo4jProjector(graph_executor)

    def project_entity(label: str, eid: str, props: dict[str, Any]) -> None:
        projector.project_entity(label, eid, props)

    def project_claim(cd: dict[str, Any]) -> bool:
        return projector.project_claim_node(cd)

    rebuild = full_rebuild(
        uow_factory, graph_executor=graph_executor,
        project_entity_fn=project_entity, project_claim_fn=project_claim,
    )

    with uow_factory.transaction() as uow:
        segments = uow._conn.execute(  # noqa: SLF001
            "SELECT DISTINCT bso.company_id, bso.raw_segment_name, "
            "max(bso.period_end) AS period_end "
            "FROM finance.business_segment_observation bso "
            "WHERE bso.segment_type = 'PRODUCT' AND bso.raw_segment_name IS NOT NULL "
            "GROUP BY bso.company_id, bso.raw_segment_name"
        ).fetchall()
    edges = 0
    for company_id, name, period_end in segments:
        product_id = product_ids.get(name)
        if product_id is None:
            continue
        graph_executor.execute(
            "MATCH (c:Company {id: $source_id}) "
            "MATCH (p:Product {id: $target_id}) "
            "MERGE (c)-[r:PRODUCES {source: 'business_segment'}]->(p) "
            "SET r.active = true, r.period_end = $period_end",
            {"source_id": str(company_id), "target_id": str(product_id),
             "period_end": str(period_end)},
        )
        edges += 1
    log.info("real_pipeline_edges_materialized", merge_attempts=edges)
    # N2：MERGE 尝试数只是上界——以图内实测计数为准（fake 执行器无此
    # 查询分支时回退尝试数）
    counted = graph_executor.execute(
        "MATCH ()-[r:PRODUCES {source: 'business_segment'}]->() "
        "RETURN count(r) AS n", {},
    )
    rebuild["mainbz_edges"] = (
        counted[0]["n"] if counted and "n" in counted[0] else edges
    )
    return rebuild


def _master_stats(uow_factory: Any) -> dict[str, Any]:
    with uow_factory.transaction() as uow:
        securities = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM master.security"
        ).fetchone()[0]
        companies = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM master.company"
        ).fetchone()[0]
        claims = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0]
        observations = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM finance.financial_observation"
        ).fetchone()[0]
        segments = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM finance.business_segment_observation"
        ).fetchone()[0]
        products = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM master.product"
        ).fetchone()[0]
        company_security = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM master.company_security"
        ).fetchone()[0]
    return {
        "master_security_total": securities,
        "master_company_total": companies,
        "company_security_total": company_security,
        "financial_observation_total": observations,
        "business_segment_total": segments,
        "master_product_total": products,
        "claim_total": claims,  # 真实数据预期为 0（#56 非目标）
    }


def _check_invariants(report: dict[str, Any], company_limit: int) -> list[str]:
    failures: list[str] = []
    master = report["master"]
    if master["master_security_total"] < 4000:
        failures.append(
            f"master.security = {master['master_security_total']} (< 4000 全市场)"
        )
    attempted = report["meta"].get("company_attempted", 0)
    company_floor = max(1, int(attempted * 0.9))  # 逐只采集容忍 10% 失败
    if master["master_company_total"] < company_floor:
        failures.append(
            f"master.company = {master['master_company_total']} "
            f"(< {company_floor}，即逐只采集的 90%)"
        )
    if master["financial_observation_total"] < 10000:
        failures.append(
            f"finance.financial_observation = "
            f"{master['financial_observation_total']} (< 10000)"
        )
    if master["master_product_total"] <= 0:
        failures.append("master.product == 0（真实主营构成未物化）")
    if master["business_segment_total"] <= 0:
        failures.append("finance.business_segment == 0")
    projection = report.get("projection", {})
    if not projection.get("mainbz_edges"):
        failures.append("公司-产品经营边为 0（图谱页将无真实边）")
    if projection.get("error"):
        failures.append(f"投影失败：{projection['error']}")
    # 对账子句：重建实体计数 = PG master 计数（键名回归锁——渲染/机器双通道）
    rebuilt = projection.get("entities_rebuilt") or {}
    if rebuilt:
        for label, master_key in (("Company", "master_company_total"),
                                  ("Security", "master_security_total"),
                                  ("Product", "master_product_total")):
            if label in rebuilt and rebuilt[label] != master[master_key]:
                failures.append(
                    f"投影对账不一致：{label} 重建 {rebuilt[label]} != "
                    f"PG {master[master_key]}"
                )
    return failures


def _render_markdown(report: dict[str, Any], failures: list[str]) -> str:
    lines = [
        "# 真实全市场管道报告（自动生成，脱敏）", "",
        f"- 运行时间：{report['meta']['started_at']}",
        f"- 一次性库：{report['meta']['pipeline_db']}",
        f"- company-limit：{report['meta']['company_limit']}",
        f"- 请求数：探针 {report['probe']['requests']} + 采集 "
        f"{report['ingest']['requests']}",
        f"- 运行时长：{report['meta']['elapsed_seconds']:.0f}s", "",
        "## 探针", "", "| 数据集 | 状态 |", "|---|---|",
    ]
    for api, entry in report["probe"]["datasets"].items():
        lines.append(f"| {api} | {entry['status']} |")
    lines += ["", "## 采集", "",
              "| 数据集 | 状态 | 行数(收/入/拒) | 请求数 |", "|---|---|---|---|"]
    for api, entry in report["ingest"]["datasets"].items():
        lines.append(
            f"| {api} | {entry['status']} | {entry['rows_received']}/"
            f"{entry['rows_inserted']}/{entry['rows_rejected']} | "
            f"{entry['request_count']} |"
        )
    lines += ["", "## 标准化", "", "| 数据集 | 读 | 写 | 拒 |", "|---|---|---|---|"]
    for api, entry in report["normalize"].items():
        lines.append(
            f"| {api} | {entry['rows_read']} | {entry['rows_written']} | "
            f"{entry['rows_rejected']} |"
        )
    lines += ["", "## 主数据（真实）", ""]
    for key, value in report["master"].items():
        lines.append(f"- {key}: {value}")
    projection = report.get("projection", {})
    lines += [
        "", "## 投影（full_rebuild + 主营构成边）", "",
        f"- 实体（entities_rebuilt）：{projection.get('entities_rebuilt')}",
        f"- 主营构成 PRODUCES 边（实测数）：{projection.get('mainbz_edges')}",
        f"- claim（真实数据预期 0）：{report['master']['claim_total']}",
        f"- 逐只采集：尝试 {report['meta'].get('company_attempted', 0)} /"
        f" 失败 {report['meta'].get('company_failed', 0)}", "",
    ]
    if projection.get("error"):
        lines.append(f"❌ 投影失败：{projection['error']}")
    if failures:
        lines += ["## 不变量失败项", ""] + [f"❌ {f}" for f in failures]
    else:
        lines += ["## 不变量校验", "",
                  "✅ 全部满足（security≥4000 / company≥90% 目标 / 财务观测"
                  "≥10000 / 真实产品+经营边>0）"]
    return "\n".join(lines) + "\n"


def run(
    *,
    company_limit: int = 300,
    fresh: bool = False,
    uow_factory: Any | None = None,
    transport: Any | None = None,
    graph_executor: Any | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """执行真实全市场管道，返回 (报告, 不变量失败项, 退出码)。

    uow_factory/transport/graph_executor 为测试注入点（CI 无网络/无
    token/无 Neo4j；生产不传）。
    """
    from src.core.bootstrap import load_settings_or_fail

    started = time.time()
    settings = load_settings_or_fail()
    token = (
        settings.tushare_token.get_secret_value()
        if settings.tushare_token is not None else None
    )
    injected = uow_factory is not None and transport is not None

    if not injected and not token:
        report = {"meta": {"started_at": dt.datetime.now(dt.UTC).isoformat(),
                           "data_mode": "no-token"}}
        return report, ["TUSHARE_TOKEN not set"], 2

    pipeline_db = os.environ.get("PIPELINE_DB", "real_market")
    if uow_factory is None:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        # B1 守护（审查）：--fresh 重建不得作用于当前配置库（切换后
        # real_market 即线上库——重建须先回切 .env 或停栈）与维护库
        if fresh:
            configured = conninfo_to_dict(settings.postgres_dsn).get("dbname")
            if pipeline_db == configured:
                return (
                    {"meta": {
                        "started_at": dt.datetime.now(dt.UTC).isoformat(),
                        "data_mode": "refused", "pipeline_db": pipeline_db,
                    }},
                    [f"--fresh refuses to rebuild the currently configured "
                     f"database ({configured}) — switch .env POSTGRES_DB back "
                     "or stop the stack first (non-fresh rerun stays available)"],
                    2,
                )
            if pipeline_db == "postgres":
                return (
                    {"meta": {
                        "started_at": dt.datetime.now(dt.UTC).isoformat(),
                        "data_mode": "refused", "pipeline_db": pipeline_db,
                    }},
                    ["PIPELINE_DB must not be the maintenance database "
                     "('postgres')"],
                    2,
                )
            # DROP/CREATE 一律经维护库连接——绝不 DROP 当前所连库（避免 PG 55006 自删错误）
            admin_params = conninfo_to_dict(settings.postgres_dsn)
            admin_params["dbname"] = "postgres"
            admin_dsn = make_conninfo(**admin_params)
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(
                    f'DROP DATABASE IF EXISTS "{pipeline_db}" WITH (FORCE)')
                conn.execute(f'CREATE DATABASE "{pipeline_db}"')
        os.environ["POSTGRES_DB"] = pipeline_db
        settings = load_settings_or_fail()
        _run_migrations()
        from src.db.uow import UnitOfWorkFactory

        uow_factory = UnitOfWorkFactory(settings.postgres_dsn)
    if transport is None:
        from src.connectors.tushare_client import http_transport

        transport = http_transport(
            settings.tushare_base_url, token or "",
            settings.tushare_timeout_seconds,
        )

    report: dict[str, Any] = {"meta": {
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "data_mode": "real" if not injected else "stub",
        "pipeline_db": pipeline_db,
        "company_limit": company_limit,
        "elapsed_seconds": 0.0,
    }}

    # 1. 探针
    report["probe"] = _phase_probe(uow_factory, settings, transport)

    # 2. 全市场批量采集 + 标准化（stock_basic 先行——证券锚定）
    report["ingest"] = {"requests": 0, "datasets": {}}
    report["normalize"] = {}
    available = {
        api: entry["status"] == "AVAILABLE"
        for api, entry in report["probe"]["datasets"].items()
    }
    for api in BATCH_DATASETS:
        if not available.get(api):
            continue
        entry = _ingest_one(uow_factory, _connector(settings, transport, api), api)
        report["ingest"]["requests"] += entry["request_count"]
        report["ingest"]["datasets"][api] = entry
    report["normalize"]["stock_basic"] = _normalize(uow_factory, "stock_basic")

    # 3. stock_company 逐只采集（共享限流器——跨调用统一节流）
    from src.connectors.tushare_client import RateLimiter

    limit_clause = "" if company_limit == 0 else f" LIMIT {int(company_limit)}"
    with uow_factory.transaction() as uow:
        ts_codes = [
            row[0] for row in uow._conn.execute(  # noqa: SLF001
                "SELECT ts_code FROM master.security ORDER BY ts_code"
                f"{limit_clause}"
            ).fetchall()
        ]
    shared_limiter = RateLimiter(settings.tushare_rate_per_minute)
    company_results: dict[str, Any] = {
        "rows_received": 0, "rows_inserted": 0,
        "rows_rejected": 0, "request_count": 0,
    }
    failed_companies = 0
    from src.connectors.ingest import ActiveRunExistsError

    for index, ts_code in enumerate(ts_codes):
        if not available.get("stock_company"):
            break
        try:
            entry = _ingest_one(
                uow_factory,
                _connector(settings, transport, "stock_company",
                           params={"ts_code": ts_code},
                           rate_limiter=shared_limiter),
                "stock_company", max_batches=1,
            )
        except ActiveRunExistsError:
            failed_companies += 1
            continue
        company_results["request_count"] += entry["request_count"]
        company_results["rows_received"] += entry["rows_received"]
        company_results["rows_inserted"] += entry["rows_inserted"]
        company_results["rows_rejected"] += entry["rows_rejected"]
        if entry["status"] != "SUCCEEDED":
            failed_companies += 1
        if (index + 1) % 50 == 0:
            print(f"stock_company {index + 1}/{len(ts_codes)}", flush=True)
    company_results["status"] = (
        "SUCCEEDED" if failed_companies == 0 else "PARTIAL"
    )
    report["ingest"]["requests"] += company_results["request_count"]
    report["ingest"]["datasets"]["stock_company"] = company_results
    report["meta"]["company_attempted"] = len(ts_codes)
    report["meta"]["company_failed"] = failed_companies

    # 4. 其余标准化（证券 → 公司 → 财务 → 主营构成）
    for api in ("stock_company", "income_vip", "cashflow_vip",
                "fina_indicator_vip", "fina_mainbz_vip"):
        if api in ("stock_company",) and not company_results["rows_inserted"]:
            report["normalize"][api] = {"status": "SKIPPED", "rows_read": 0,
                                        "rows_written": 0, "rows_rejected": 0}
            continue
        report["normalize"][api] = _normalize(uow_factory, api)

    # 5. 真实主营构成 → 产品物化
    products = _materialize_products(uow_factory)
    report["products_materialized"] = {
        "product_names": products["product_names"],
        "segments_total": products["segments_total"],
    }

    # 6. 全量投影（不可达/异常 → 如实入报告：数据已保全，退出码走 3）
    try:
        if graph_executor is None:
            graph_executor = _build_graph_executor()
        report["projection"] = _project(
            uow_factory, graph_executor, products["product_ids"],
        )
    except Exception as exc:  # noqa: BLE001
        report["projection"] = {
            "error": f"{type(exc).__name__}: {str(exc)[:200]}"
        }

    # 7. 主数据统计 + 不变量
    report["master"] = _master_stats(uow_factory)
    report["meta"]["elapsed_seconds"] = time.time() - started
    failures = _check_invariants(report, company_limit)
    return report, failures, (0 if not failures else 3)


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def _build_graph_executor() -> Any:
    """真实 Neo4j 执行器（NEO4J_* 环境变量；compose 栈内即 bolt://neo4j:7687）。"""
    from src.core.bootstrap import load_settings_or_fail

    settings = load_settings_or_fail()
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=10,
    )

    class _DriverExecutor:
        def __init__(self, drv: Any) -> None:
            self._driver = drv

        def execute(self, template: str, params: dict[str, Any]) -> list[dict]:
            with self._driver.session() as session:
                return [dict(r) for r in session.run(template, params)]

    return _DriverExecutor(driver)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-limit", type=int, default=300,
                        help="stock_company 逐只采集证券数（0=全量）")
    parser.add_argument("--fresh", action="store_true",
                        help="DROP+CREATE 一次性库（默认复跑幂等）")
    parser.add_argument("--report-out", default=None,
                        help="报告输出路径（写宿主挂载路径，如 /host/...）")
    args = parser.parse_args()

    report, failures, exit_code = run(
        company_limit=args.company_limit, fresh=args.fresh,
    )
    print(json.dumps({
        "meta": report.get("meta"),
        "master": report.get("master"),
        "requests": {
            "probe": report.get("probe", {}).get("requests"),
            "ingest": report.get("ingest", {}).get("requests"),
        },
        "failures": failures,
    }, ensure_ascii=False))
    if exit_code == 2:
        return 2
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
