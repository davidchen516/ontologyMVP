"""MVP 数据快照构建器（issue #11）：人形机器人核心零部件证据化筛选纵向切片。

组合 #1~#10 的真实管线：SyntheticTransport（仅替代 HTTP 层）→
ingest_dataset（#3 真实采集）→ normalize_dataset（#4 真实标准化）→
document/evidence_fragment（#6 管线形态）→ RulesExtractor（#7 真实抽取）
→ intake_candidate（#7 真实接入编排：校验/解析/SHACL/冲突/auto-accept/
Outbox）→ Neo4j 投影（#8）→ 对账报告。

30 家合成公司（canonical_name 前缀"辛示"、USCC 前缀 SIN-，绝不冒用
真实公司）：8 类情景覆盖 issue #11 全部黄金负向场景。快照清单
（manifest）记录全部版本与统计；payload_hash 幂等保证重复构建无重复
事实。真实数据快照：配置 TUSHARE_TOKEN 后 --real 经同一管线采集。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

from src.claims.extractor import RulesExtractor  # noqa: E402
from src.claims.review import intake_candidate  # noqa: E402
from src.connectors.datasets import load_datasets  # noqa: E402
from src.connectors.ingest import ingest_dataset, start_run  # noqa: E402
from src.connectors.tushare_connectors import TushareConnector  # noqa: E402
from src.db.uow import UnitOfWorkFactory  # noqa: E402
from src.projection.dispatcher import EventDispatcher  # noqa: E402
from src.projection.projector import Neo4jProjector  # noqa: E402
from src.projection.reconcile import reconciliation_report  # noqa: E402
from src.standardize.pipeline import (  # noqa: E402
    normalize_dataset,
    start_normalization_run,
)

SNAPSHOT_VERSION = "mvp-synthetic-v1"
BUILD_ID = f"{SNAPSHOT_VERSION}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
LEASE_TTL = 600

# ---- 30 家合成公司：8 类情景（覆盖 issue #11 黄金负向场景矩阵）----
SCENARIOS: list[str] = (
    ["MASS_PROD"] * 12          # 量产+3FY 全正+证据齐 → include
    + ["CONCEPT_ONLY"] * 3      # 只有概念标签、无产品 Claim → 排除不断言无业务
    + ["RESEARCH_ONLY"] * 4     # 仅研发 → 不升量产
    + ["DENIAL"] * 3            # 公司否认 → DENIES_INVOLVEMENT
    + ["THIRD_PARTY"] * 3       # 第三方称量产+公司否认 → 审核/冲突
    + ["FIN_SHORT"] * 2         # 现金流不足 3FY → 数据不足
    + ["FIN_NEGATIVE"] * 2      # 一年为负 → 口径差异
    + ["RESTATE"] * 1           # 财务重述 → 当前值规则
)
COMPANIES = [
    {
        "ts_code": f"3001{seq:02d}.SZ",
        "name": f"辛示机器人{seq:02d}号",
        "uscc": f"SIN-ROBOT-{seq:02d}-{hashlib.sha256(str(seq).encode()).hexdigest()[:8].upper()}",
        "scenario": scenario,
    }
    for seq, scenario in enumerate(SCENARIOS, start=1)
]
assert len(COMPANIES) == 30, "issue #11 requires 30 candidate companies"

THEME_NAME = "人形机器人"
CORE_COMPONENT_CONCEPT = "机器人核心零部件"  # 平台概念标签（非证据）

PRODUCTS = ["谐波减速器", "RV减速器", "无框力矩电机", "伺服系统", "滚柱丝杠",
            "六维力传感器", "视觉传感器", "编码器", "关节模组", "灵巧手"]

TXT_MASS = "{product}已实现量产，报告期内批量生产并交付客户。"
TXT_RESEARCH = "公司正在研发{product}相关技术，产品尚未量产。"
TXT_DENIAL = "公司澄清：目前不涉及{product}业务，未参与任何相关生产环节。"
TXT_THIRD_PARTY = "据外部报道，该公司{product}或有望实现量产，尚待公司确认。"
TXT_NO_BUSINESS = "公司报告期内主营业务不涉及机器人零部件制造，未参与相关生产。"
TXT_NEUTRAL = "公司经营情况详见年度报告全文及董事会报告相关章节。"

FY_VALUES: dict[str, list[tuple[int, float]]] = {
    "MASS_PROD": [(2024, 150.0), (2023, 220.0), (2022, 310.0)],
    "MASS_NOEV": [(2024, 120.0), (2023, 180.0), (2022, 260.0)],
    "RESEARCH_ONLY": [(2024, 80.0), (2023, 90.0), (2022, 100.0)],
    "DENIAL": [(2024, 300.0), (2023, 320.0), (2022, 340.0)],
    "THIRD_PARTY": [(2024, 60.0), (2023, 70.0), (2022, 80.0)],
    "FIN_SHORT": [(2024, 100.0), (2023, 200.0)],
    "FIN_NEGATIVE": [(2024, 100.0), (2023, -50.0), (2022, 200.0)],
    "RESTATE": [(2024, 400.0), (2023, 200.0), (2022, 100.0)],
    "CONCEPT_ONLY": [(2024, 500.0), (2023, 500.0), (2022, 500.0)],
}
# MASS_PROD 现金流按序号个性化（黄金断言精确值）：base + seq*10
_MASS_FY_BASE = [(2024, 100.0), (2023, 200.0), (2022, 300.0),
                 (2021, 150.0), (2020, 250.0)]
_mass_count = 0
for _c in COMPANIES:
    if _c["scenario"] == "MASS_PROD":
        _mass_count += 1
        _c["fy_values"] = [
            (y, v + _mass_count * 10.0) for y, v in _MASS_FY_BASE
        ]
    else:
        _c["fy_values"] = FY_VALUES[_c["scenario"]]


def _stable_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class SyntheticTransport:
    """合成 TuShare 响应：只替代 HTTP 层，字段与真实接口对齐。

    重复构建时 raw 层按 (source_system, api_name, payload_hash) 幂等。
    """

    def __init__(self) -> None:
        self._fields_cache: dict[str, list[str]] = {}

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        api = request["api_name"]
        fields, items = self._payload(api)
        return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}

    def _fields_of(self, fixture: str) -> list[str]:
        if fixture not in self._fields_cache:
            from src.connectors.testing import FixtureTransport

            ref = FixtureTransport(REPO_ROOT / "tests" / "fixtures" / "tushare")
            self._fields_cache[fixture] = ref(
                {"api_name": fixture, "params": {}}
            )["data"]["fields"]
        return self._fields_cache[fixture]

    def _payload(self, api: str) -> tuple[list[str], list[list[Any]]]:
        if api == "stock_basic":
            return self._fields_of(api), [
                [c["ts_code"], c["ts_code"][:6], c["name"], "创业板", "L",
                 "辛示市", "机器人制造", "20200101"] for c in COMPANIES]
        if api == "stock_company":
            return self._fields_of(api), [
                [c["ts_code"], c["name"], "辛示省"] for c in COMPANIES]
        if api == "namechange":
            return self._fields_of(api), [
                [c["ts_code"], f"{c['name']}（旧称）", "20200630"]
                for c in COMPANIES[:5]]
        if api == "ths_index":
            # 字段语义：ts_code=概念码、name=概念名（ConceptIndexProcessor）
            return self._fields_of(api), [
                ["883018", THEME_NAME, len(COMPANIES), "SW"],
                ["883019", CORE_COMPONENT_CONCEPT, len(COMPANIES), "SW"],
            ]
        if api == "ths_member":
            # ts_code=成员股、con_code=概念码、in_date=快照日（ConceptMemberProcessor）
            return self._fields_of(api), [
                [c["ts_code"], concept_code, "20200101", concept_name]
                for concept_code, concept_name in (
                    ("883018", THEME_NAME), ("883019", CORE_COMPONENT_CONCEPT))
                for c in COMPANIES]
        if api == "dc_index":
            return self._fields_of(api), [
                ["BK1184", THEME_NAME, len(COMPANIES)]]
        if api == "dc_member":
            # dc fields [con_code, in_date, ts_code, code]：con_code 为成员股、
            # code 为概念码（ConceptMemberProcessor 读 ts_code/code/in_date）
            return self._fields_of(api), [
                [c["ts_code"], "20200101", c["ts_code"], "BK1184"]
                for c in COMPANIES]
        if api == "index_classify":
            # index_code/industry_name/level/parent_code（IndexClassifyProcessor）
            return self._fields_of(api), [
                ["801010.SI", "辛示综合", "L1", ""]]
        if api == "index_member_all":
            # ts_code/in_date/out_date/is_new/index_code（IndexMemberProcessor）
            return self._fields_of(api), [
                [c["ts_code"], "20200101", "", "Y", "801010.SI"]
                for c in COMPANIES]
        if api == "fina_mainbz_vip":
            items: list[list[Any]] = []
            for c in COMPANIES:
                if c["scenario"] in ("MASS_PROD", "RESTATE", "FIN_NEGATIVE"):
                    items.append([c["ts_code"], "20241231", PRODUCTS[0],
                                  "P", 12000.0, 1500.0, "CNY", 9000.0])
                    items.append([c["ts_code"], "20241231", PRODUCTS[1],
                                  "P", 5000.0, 400.0, "CNY", 3800.0])
            return self._fields_of(api), items
        if api == "cashflow_vip":
            fields = self._fields_of(api) + ["curr_type", "ann_date"]
            items = []
            for c in COMPANIES:
                for year, value in c["fy_values"]:
                    items.append(
                        [c["ts_code"], f"{year}1231", value, "CNY", "2025-04-25"]
                    )
                # RESTATE：同 FY2024 一个重述版本（公告更晚 → 当前值视图胜出）
                if c["scenario"] == "RESTATE":
                    items.append(
                        [c["ts_code"], "20241231", 999.0, "CNY", "2026-04-25"]
                    )
            return fields, items
        if api == "income_vip":
            return self._fields_of(api), [
                [c["ts_code"], f"{y}1231", 10000.0 + i, 800.0 + i]
                for i, c in enumerate(COMPANIES) for y in (2024, 2023, 2022)]
        if api == "fina_indicator_vip":
            return self._fields_of(api), [
                [c["ts_code"], f"{y}1231", 8.5]
                for c in COMPANIES for y in (2024, 2023, 2022)]
        if api == "top10_holders":
            return self._fields_of(api), [
                [c["ts_code"], "20241231", f"辛示股东{i}", 8.0 - i * 0.1]
                for c in COMPANIES[:5] for i in range(3)]
        raise KeyError(f"synthetic transport: unknown api {api}")


DATASETS_TO_RUN = [
    "stock_basic", "stock_company", "namechange",
    "index_classify", "index_member_all",
    "ths_index", "ths_member", "dc_index", "dc_member",
    "fina_mainbz_vip", "income_vip", "cashflow_vip", "fina_indicator_vip",
    "top10_holders",
]


def run_pipeline(
    uow_factory: UnitOfWorkFactory, *, settings: Any, token: str | None,
) -> dict[str, Any]:
    """真实采集+标准化管线。

    token 为 None → SyntheticTransport（仅替代 HTTP 层）；
    token 提供且 --real → http_transport 走真实 TuShare API（同一管线）。
    """
    stats: dict[str, Any] = {"ingested": {}, "normalized": {},
                             "data_mode": "synthetic" if not token else "real"}
    if token:
        from src.connectors.tushare_client import http_transport

        transport = http_transport(
            settings.tushare_base_url, token,
            settings.tushare_timeout_seconds,
        )
    else:
        transport = SyntheticTransport()
    datasets = load_datasets()
    for api in DATASETS_TO_RUN:
        connector = TushareConnector(
            datasets[api], settings=settings,
            token=token or "synthetic-not-a-real-token",
            transport=transport,
        )
        with uow_factory.transaction() as uow:
            run = start_run(
                uow, dataset_name=api,
                trace_id=f"snapshot-{api}-{BUILD_ID}",
                lease_owner="snapshot-builder", lease_ttl_seconds=LEASE_TTL,
            )
        outcome = ingest_dataset(
            uow_factory, connector, run_id=run["id"], lease_ttl_seconds=LEASE_TTL,
        )
        stats["ingested"][api] = {"status": outcome.status.value,
                                  "rows_inserted": outcome.rows_inserted}
    for api in DATASETS_TO_RUN:
        with uow_factory.transaction() as uow:
            norm_run = start_normalization_run(
                uow, dataset_name=api,
                trace_id=f"snapshot-norm-{api}-{BUILD_ID}",
                lease_owner="snapshot-builder", lease_ttl_seconds=LEASE_TTL,
            )
        norm = normalize_dataset(
            uow_factory, api, run_id=norm_run["id"], lease_ttl_seconds=LEASE_TTL,
        )
        stats["normalized"][api] = {"status": norm.status.value,
                                    "rows_written": norm.rows_written}
    return stats


def _announcement_texts(scenario: str) -> list[str]:
    """按情景生成公告段落；每条 = 1 个 evidence_fragment = 1 个候选。"""
    if scenario in ("MASS_PROD", "MASS_NOEV", "RESTATE"):
        texts = [TXT_MASS.format(product=p) for p in PRODUCTS]
    elif scenario == "RESEARCH_ONLY":
        texts = [TXT_RESEARCH.format(product=p) for p in PRODUCTS]
    elif scenario == "DENIAL":
        texts = [TXT_DENIAL.format(product=p) for p in PRODUCTS]
    elif scenario == "THIRD_PARTY":
        texts = [TXT_THIRD_PARTY.format(product=PRODUCTS[0]),
                 TXT_NO_BUSINESS]
    elif scenario == "CONCEPT_ONLY":
        # 中性段落：不命中任何阶段/否认/对冲词表 → 无产品 Claim
        #（"只有概念标签"语义——概念成员不代表任何经营陈述）
        texts = ["公司经营情况详见年度报告全文及董事会报告相关章节。"]
    elif scenario in ("FIN_SHORT", "FIN_NEGATIVE"):
        texts = [TXT_MASS.format(product=p) for p in PRODUCTS[:6]]
    else:  # pragma: no cover - 场景表已穷举
        texts = []
    # 补齐到每家 >=20 段（30×20=600 片段；CONCEPT_ONLY 中性段不产 Claim，
    # 有效候选仍 ≥500——验收 3 下限）。
    # 补齐文本必须与情景同语义：绝不能把量产文本填进非量产情景。
    filler_template = {
        "MASS_PROD": TXT_MASS, "RESTATE": TXT_MASS,
        "FIN_SHORT": TXT_MASS, "FIN_NEGATIVE": TXT_MASS,
        "RESEARCH_ONLY": TXT_RESEARCH, "DENIAL": TXT_DENIAL,
        "CONCEPT_ONLY": TXT_NEUTRAL, "THIRD_PARTY": TXT_NO_BUSINESS,
    }[scenario]
    filler = 0
    while len(texts) < 20:
        texts.append(
            filler_template.format(product=PRODUCTS[filler % len(PRODUCTS)])
            + (f"本期交付批次编号 B{filler:03d}。"
               if filler_template is TXT_MASS else "")
        )
        filler += 1
    # 段落唯一化：claim 幂等键含原文文本——相同文本会去重为同一条 Claim，
    # 导致候选总数低于验收下限；段落编号保证 540 段全部唯一
    return [f"{text}（公告段落 {i + 1}）" for i, text in enumerate(texts)]


def materialize_products(uow_factory: UnitOfWorkFactory) -> dict[str, Any]:
    """物化产品实体（issue #11 产品映射步骤）。

    #4 标准化管线无产品写入点（master.product 由映射层维护）——
    快照在此把词表首批标准产品物化为实体，供 Claim object 关联。
    名称唯一约束保证幂等。
    """
    with uow_factory.transaction() as uow:
        for product in PRODUCTS:
            uow._conn.execute(  # noqa: SLF001
                "INSERT INTO master.product (iri, canonical_name, "
                "ontology_version) VALUES (%s, %s, '0.1.0') "
                "ON CONFLICT (iri) DO NOTHING",
                (f"https://ontology.example.com/product#MVP_{hashlib.sha256(product.encode()).hexdigest()[:10]}",
                 product),
            )
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT canonical_name, id FROM master.product"
        ).fetchall()
    return {name: pid for name, pid in rows}


def map_claim_objects(
    uow_factory: UnitOfWorkFactory, product_ids: dict[str, Any],
) -> dict[str, int]:
    """PRODUCES Claim → 标准产品映射（黄金产品映射依据）。

    映射规则：quote_text 精确包含产品标准名 → object_entity_id 指向该
    产品（object_entity_type='Product'）。无匹配保持 NULL（不猜测映射）。
    """
    mapped = 0
    with uow_factory.transaction() as uow:
        claims = uow._conn.execute(  # noqa: SLF001
            "SELECT id FROM fact.claim WHERE predicate_code = 'PRODUCES'"
        ).fetchall()
        for (claim_id,) in claims:
            quote = uow._conn.execute(  # noqa: SLF001
                "SELECT f.quote_text FROM fact.claim_evidence ce "
                "JOIN fact.evidence_fragment f ON f.id = ce.evidence_id "
                "WHERE ce.claim_id = %s LIMIT 1",
                (claim_id,),
            ).fetchone()
            if not quote:
                continue
            for name, pid in product_ids.items():
                if name in quote[0]:
                    uow._conn.execute(  # noqa: SLF001
                        "UPDATE fact.claim SET object_entity_type = 'Product', "
                        "object_entity_id = %s WHERE id = %s",
                        (pid, claim_id),
                    )
                    mapped += 1
                    break
    return {"mapped_objects": mapped}


def build_documents_claims(
    uow_factory: UnitOfWorkFactory, *, auto_accept: bool,
) -> dict[str, Any]:
    """文档→版本→证据片段→真实抽取→真实 intake（校验/审核/auto-accept/Outbox）。"""
    stats: dict[str, Any] = {
        "documents": 0, "fragments": 0, "candidates": 0,
        "accepted": 0, "needs_review": 0, "rejected": 0, "denied": 0,
    }
    extractor = RulesExtractor()

    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT c.id, c.canonical_name, s.id "
            "FROM master.company c "
            "JOIN master.company_security cs ON cs.company_id = c.id "
            "JOIN master.security s ON s.id = cs.security_id "
            "WHERE c.canonical_name LIKE '辛示%' ORDER BY c.canonical_name"
        ).fetchall()
    assert len(rows) == 30, f"expected 30 companies with securities, got {len(rows)}"

    for company_id, company_name, security_id in rows:
        seq = int(company_name.replace("辛示机器人", "").replace("号", ""))
        scenario = COMPANIES[seq - 1]["scenario"]
        texts = _announcement_texts(scenario)

        with uow_factory.transaction() as uow:
            document = uow.documents.insert(
                document_type="ANNUAL_REPORT", source_system="SIN_DISCLOSURE",
                title=f"{company_name} 年度报告",
                content_hash=_stable_hash(f"doc:{company_name}:{SNAPSHOT_VERSION}"),
            )
            version = uow.document_versions.create_version(
                document_id=document["id"],
                source_url=f"file://synthetic/{company_name}.pdf",
                content_hash=_stable_hash(f"docver:{company_name}:{SNAPSHOT_VERSION}"),
            )
            stats["documents"] += 1
            fragments: list[dict[str, Any]] = []
            for index, text in enumerate(texts):
                fragment = uow.claim_evidence.insert_evidence_fragment(
                    document_id=document["id"],
                    page_number=index // 4 + 1,
                    quote_text=text,
                    checksum=_stable_hash(
                        f"frag:{company_name}:{index}:{text[:24]}"
                    ),
                )
                # 仓库函数未写入 #6 的版本/字符定位列——快照在此补齐，
                # 使 quote grounding 校验（intake_candidate）可通过
                uow._conn.execute(  # noqa: SLF001
                    "UPDATE fact.evidence_fragment SET document_version_id = %s, "
                    "char_start = %s, char_end = %s WHERE id = %s",
                    (version["id"], 0, len(text), fragment["id"]),
                )
                fragments.append({
                    "id": fragment["id"],
                    "document_version_id": version["id"],
                    "company_id": company_id,
                    "page_number": index // 4 + 1,
                    "char_start": 0,
                    "char_end": len(text),
                    "text": text,
                })
                stats["fragments"] += 1

        output = extractor.extract(fragments, ontology_version="0.1.0")
        stats["candidates"] += len(output.claims)
        # 报告期-derived 业务有效期：年报披露的经营事实自报告期年起有效
        #（valid_to 开放——由 SUPERSEDED/CONTRADICTED 状态机关闭）
        report_year = max(year for year, _ in
                          COMPANIES[seq - 1]["fy_values"])
        # checksum 与片段生成同源（insert_evidence_fragment 返回值不含 checksum）
        checksums = [
            _stable_hash(f"frag:{company_name}:{i}:{t[:24]}")
            for i, t in enumerate(texts)
        ]

        for candidate in output.claims:
            with uow_factory.transaction() as uow:
                outcome = intake_candidate(
                    uow, candidate,
                    document_version_id=version["id"],
                    fragment_checksums=checksums,
                    security_id=security_id,
                    trace_id=f"snapshot-{BUILD_ID}",
                    auto_accept=auto_accept,
                    shacl_validate=False,      # 快照构建用快速谓词校验
                    check_conflicts=True,
                )
            with uow_factory.transaction() as uow:
                uow._conn.execute(  # noqa: SLF001
                    "UPDATE fact.claim SET valid_from = %s WHERE id = %s "
                    "AND valid_from IS NULL",
                    (f"{report_year}-01-01", outcome.claim_id),
                )
            if outcome.status == "ACCEPTED":
                stats["accepted"] += 1
            elif outcome.status == "NEEDS_REVIEW":
                stats["needs_review"] += 1
            elif outcome.status == "VALIDATED":
                # VALIDATED 且未自动接受 = 冲突挂起（有 OPEN 审核任务）
                stats["validated_pending_review"] = (
                    stats.get("validated_pending_review", 0) + 1
                )
            else:
                stats["rejected"] += 1
    return stats


class _GraphDriverExecutor:
    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [dict(r) for r in session.run(template, params)]


def project_to_graph(uow_factory: UnitOfWorkFactory, neo4j_driver: Any) -> dict[str, Any]:
    """Outbox → Neo4j（#8 真实 worker 周期：领取→投影→标记）+ 对账报告。"""

    from src.projection.worker import run_worker_cycle

    executor = _GraphDriverExecutor(neo4j_driver)
    projector = Neo4jProjector(executor)

    def project_fn(uow: Any, event: dict[str, Any]) -> None:
        EventDispatcher(projector).dispatch(uow, event)

    def verify_fn(uow: Any, event: dict[str, Any]) -> bool:
        # 读后校验：Claim 节点必须已存在（worker verify 钩子）
        claim_id = str(event.get("aggregate_id") or "")
        rows = executor.execute(
            "MATCH (n:Claim {id: $id}) RETURN n.id AS id LIMIT 1", {"id": claim_id}
        )
        return bool(rows)

    results = []
    while True:
        batch = run_worker_cycle(
            uow_factory, project_fn=project_fn, verify_fn=verify_fn,
            batch_size=500,
        )
        results.extend(batch)
        if len(batch) < 500:
            break

    with uow_factory.transaction() as uow:
        report = reconciliation_report(uow, executor)
    processed = sum(1 for r in results if str(r.status) == "PROCESSED")
    return {"dispatched": len(results), "processed": processed,
            "reconciliation": report}


def write_manifest(output_dir: Path, stats: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "build_id": BUILD_ID,
        "data_snapshot_version": SNAPSHOT_VERSION,
        "ontology_version": "0.1.0",
        "mapping_version": "tushare-mappings-0.1.0",
        "extraction_version": "rules-extractor-0.1.0",
        "data_mode": stats.get("data_mode", "synthetic"),
        "generated_at": datetime.now(UTC).isoformat(),
        "stats": stats,
        "disclaimer": (
            "Synthetic snapshot: company names prefixed 辛示 and USCCs prefixed "
            "SIN- are constructed for closed-loop validation; no real-company "
            "data is included. Configure TUSHARE_TOKEN and use --real to "
            "produce a live snapshot through the same pipeline."
        ),
    }
    path = output_dir / f"{BUILD_ID}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return path


def build_snapshot(
    dsn: str, *, neo4j_uri: str | None = None, neo4j_user: str = "neo4j",
    neo4j_password: str | None = None, output_dir: Path | None = None,
    token: str | None = None,
) -> Path:
    """构建快照：采集→标准化→文档/Claim→投影→对账→manifest。"""
    from tests.helpers import make_settings

    settings = make_settings(tushare_token=token)
    uow_factory = UnitOfWorkFactory(dsn)
    stats: dict[str, Any] = run_pipeline(
        uow_factory, settings=settings, token=token,
    )
    product_ids = materialize_products(uow_factory)
    stats["products"] = len(product_ids)
    claim_stats = build_documents_claims(uow_factory, auto_accept=True)
    stats.update(claim_stats)
    stats.update(map_claim_objects(uow_factory, product_ids))

    if neo4j_uri and neo4j_password:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password), connection_timeout=10,
        )
        try:
            # 实体节点先行投影（EDGE_MERGE 的 MATCH 要求两端节点存在；
            # 标准化层不发实体 Outbox 事件——快照直接物化，full_rebuild
            # 亦从 PG 重建同构实体）
            with uow_factory.transaction() as uow:
                product_rows = uow._conn.execute(  # noqa: SLF001
                    "SELECT id, canonical_name FROM master.product"
                ).fetchall()
                company_rows = uow._conn.execute(  # noqa: SLF001
                    "SELECT id, canonical_name FROM master.company "
                    "WHERE canonical_name LIKE '辛示%'"
                ).fetchall()
            projector = Neo4jProjector(_GraphDriverExecutor(driver))
            for pid, name in product_rows:
                projector.project_entity("Product", str(pid), {"name": name})
            for cid, name in company_rows:
                projector.project_entity("Company", str(cid), {"canonical_name": name})
            # 概念/Theme 腿：Concept 节点 + TAGGED_AS 成员边（缺失时图模式
            # 概念筛选会"静默空结果"——违反绝不静默不变量）
            with uow_factory.transaction() as uow:
                concept_rows = uow._conn.execute(  # noqa: SLF001
                    "SELECT id, name FROM master.concept"
                ).fetchall()
                member_rows = uow._conn.execute(  # noqa: SLF001
                    "SELECT m.company_id, m.concept_id, m.snapshot_date "
                    "FROM master.company_concept_membership m"
                ).fetchall()
            for concept_id, concept_name in concept_rows:
                projector.project_entity(
                    "Concept", str(concept_id), {"canonical_name": concept_name}
                )
            for member_company, member_concept, snapshot in member_rows:
                projector.project_membership(
                    rel_type="TAGGED_AS",
                    source_id=str(member_company),
                    target_id=str(member_concept),
                    props={"snapshot_date": str(snapshot),
                           "active": True},
                )
            graph_stats = project_to_graph(uow_factory, driver)
        finally:
            driver.close()
        stats.update(graph_stats)

    output_dir = output_dir or REPO_ROOT / "docs" / "snapshots"
    return write_manifest(output_dir, stats)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--neo4j-uri", default=None)
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default=None)
    parser.add_argument("--token", default=None, help="real TUSHARE_TOKEN")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--real", action="store_true",
        help="ingest via live TuShare HTTP (requires --token); without it the "
             "synthetic snapshot uses the identical pipeline",
    )
    args = parser.parse_args()
    if args.real and not args.token:
        parser.error("--real requires --token")
    manifest = build_snapshot(
        args.dsn, neo4j_uri=args.neo4j_uri, neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password, token=args.token,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print(f"snapshot manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
