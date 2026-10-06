"""各数据集的标准化处理器：Raw 行 → 标准实体（issue #4 范围逐项）。

语义红线（不变量）：
- 概念成员只形成带 source_platform + snapshot_date 的 membership，
  绝不生成 PRODUCES/DEVELOPS/MASS_PRODUCTION 或任何经营 Claim；
- 股东数据只形成 HOLDS 观察候选，绝不因第一大股东形成 CONTROLS；
- bz_item 原样保留，标准 Product 映射是附加关系（后续语义层）；
- 无法解析的值进拒绝事件，不静默纠正；财务缺失保持 NULL，币种未知保持 NULL。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.standardize.repositories import (
    ConceptRepository,
    ExchangeRepository,
    FinancialRepository,
    HoldingRepository,
    IndustryRepository,
    StandardCompanyRepository,
    StandardSecurityRepository,
)
from src.standardize.transforms import (
    TransformError,
    clean_str,
    decimal_or_null,
    parse_date,
    validate_ts_code,
)

# TuShare 报表字段 → 财务指标编码（本 PR 注册进 finance.financial_metric）
CASHFLOW_METRICS = {
    "n_cashflow_act": ("NET_CF_OPERATING", "经营活动产生的现金流量净额", "CNY", "SUM")
}
INCOME_METRICS = {
    "revenue": ("TOTAL_REVENUE", "营业总收入", "CNY", "SUM"),
    "n_income": ("NET_INCOME", "净利润", "CNY", "SUM"),
}
INDICATOR_METRICS = {
    "roe": ("ROE", "净资产收益率", "RATIO", "LATEST"),
    "netprofit_yoy": ("NET_PROFIT_YOY", "净利润同比增长率", "RATIO", "LATEST"),
}

# 与 ontology/mappings/tushare.yaml（v0.1.2）bz_code 映射一致；
# 值域受 finance.business_segment_observation CHECK 约束
SEGMENT_TYPE_MAP = {"P": "PRODUCT", "I": "INDUSTRY", "D": "REGION", "M": "SALES_MODE"}


@dataclass
class ProcessedOutcome:
    written: int
    rejected: list[dict[str, Any]]


class StandardizeContext:
    """处理器共用句柄：uow + 事件 + 来源信息。"""

    def __init__(self, uow: Any, run_id: uuid.UUID) -> None:
        self.uow = uow
        self.run_id = run_id
        from src.standardize.repositories import NormalizationEventRepository

        self.events = NormalizationEventRepository(uow._conn)  # noqa: SLF001
        self.exchanges = ExchangeRepository(uow._conn)  # noqa: SLF001 - 同连接事务
        self.securities = StandardSecurityRepository(uow._conn)  # noqa: SLF001
        self.companies = StandardCompanyRepository(uow._conn)  # noqa: SLF001
        self.industries = IndustryRepository(uow._conn)  # noqa: SLF001
        self.concepts = ConceptRepository(uow._conn)  # noqa: SLF001
        self.financials = FinancialRepository(uow._conn)  # noqa: SLF001
        self.holdings = HoldingRepository(uow._conn)  # noqa: SLF001

    def reject(self, reason: str, source_record_id: uuid.UUID, ref: str | None) -> dict[str, Any]:
        return {
            "reason": reason,
            "source_record_id": source_record_id,
            "ref": ref,
        }


def _company_id_for_security(ctx: StandardizeContext, security_id: uuid.UUID) -> uuid.UUID | None:
    row = ctx.companies._fetchone(  # noqa: SLF001
        """
        SELECT c.id FROM master.company c
        JOIN master.company_security cs ON cs.company_id = c.id
        WHERE cs.security_id = %s ORDER BY cs.recorded_at DESC LIMIT 1
        """,
        (security_id,),
    )
    return row["id"] if row else None


class StockBasicProcessor:
    """stock_basic → Exchange / Security。"""

    dataset_name = "stock_basic"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        rejected: list[dict[str, Any]] = []
        source_id = record["source_record_id"]
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            symbol = clean_str(payload.get("symbol")) or ts_code.split(".")[0]
            name = clean_str(payload.get("name"))
            if name is None:
                raise TransformError("missing name")
            exchange_code = ts_code.split(".")[1]
            list_status = clean_str(payload.get("list_status"))
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        # issue #43 I2：list_status 缺失 → UNKNOWN（不静默默认 ACTIVE——
        # 已退市证券不得被标为在市；降级必须可识别）
        status = {"L": "ACTIVE", "D": "DELISTED", "P": "PAUSED"}.get(
            list_status or "", "UNKNOWN"
        )

        # issue #43：payload 的 exchange 字段优先（消除"字段从未被读"的
        # 死映射）；缺失时从 ts_code 后缀派生（既有行为）
        payload_exchange = clean_str(payload.get("exchange"))
        suffix_map = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
        derived_code = suffix_map[ts_code.split(".")[1]]
        exchange_code = (
            {"SSE": "SSE", "SZSE": "SZSE", "BSE": "BSE"}.get(
                (payload_exchange or "").upper(), derived_code
            )
            if payload_exchange
            else derived_code
        )
        # main 基线行为：exchange 行 name=原始后缀码（SH/SZ/BJ），保持
        # 全字段回归逐字段一致（issue #43 I3）——不放宽为 code 同名
        exchange = ctx.exchanges.upsert(
            code=exchange_code, name=ts_code.split(".")[1]
        )
        security = ctx.securities.upsert(
            ts_code=ts_code,
            symbol=symbol,
            name=name,
            exchange_id=exchange["id"],
            source_record_id=source_id,
            status=status,
        )
        written = 1 if security["inserted"] else 0
        return ProcessedOutcome(written, rejected)


class StockCompanyProcessor:
    """stock_company → Company + CompanySecurity + CompanyProfile。

    公司定位走"证券锚定"的受控复合键：已有发行关系则复用公司；
    否则以受控占位 uscc 新建（唯一、显式标注不确定），绝不按简称合并。
    """

    dataset_name = "stock_company"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            # 公司名解析顺序（issue #49）：name → com_name → fullname。
            # com_name 是 stock_company 接口的公司全称官方键——低层级账户
            # 实测仅有 com_name（name/fullname 均缺失）；fullname 保留为
            # 历史/高层级形态回退。三者全缺 → 拒绝（不建无名公司）。
            company_name = (
                clean_str(payload.get("name"))
                or clean_str(payload.get("com_name"))
                or clean_str(payload.get("fullname"))
            )
            if company_name is None:
                raise TransformError("missing company name")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        if security is None:
            rejected.append(ctx.reject(
                f"security {ts_code} not standardized yet (process stock_basic first)",
                source_id, ts_code,
            ))
            return ProcessedOutcome(0, rejected)

        company = ctx.companies.upsert_with_security_anchor(
            canonical_name=company_name,
            source_record_id=source_id,
            security_id=security["id"],
            ts_code=ts_code,
        )
        ctx.companies.link_security(
            company_id=company["id"],
            security_id=security["id"],
            recorded_at=record["retrieved_at"],
            source_record_id=source_id,
        )
        try:
            ctx.companies.upsert_profile(
                company_id=company["id"],
                source_record_id=source_id,
                chairman_name=clean_str(payload.get("chairman")),
                general_manager_name=clean_str(payload.get("manager")),
                board_secretary_name=clean_str(payload.get("secretary")),
                registered_capital=decimal_or_null(payload.get("reg_capital")),
                founded_date=parse_date(payload.get("setup_date")),
                province=clean_str(payload.get("province")),
                city=clean_str(payload.get("city")),
                employees=decimal_or_null(payload.get("employees")),
                main_part_business=clean_str(payload.get("main_part_business")),
            )
        except TransformError as exc:
            rejected.append(ctx.reject(
                f"profile field rejected: {exc}", source_id, ts_code
            ))

        if company.get("uscc_placeholder"):
            ctx.events.add(
                run_id=ctx.run_id,
                event_type="MAPPED",
                entity_type="COMPANY",
                entity_ref=ts_code,
                detail={
                    "uncertainty": "uscc_placeholder",
                    "note": "unified social credit code missing; "
                            "controlled placeholder key assigned",
                },
                source_record_id=source_id,
            )
        written = 1 if company.get("inserted") else 0
        return ProcessedOutcome(written, rejected)


class NamechangeProcessor:
    """namechange → master.security_name_history 历史别名。"""

    dataset_name = "namechange"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            name = clean_str(payload.get("name"))
            if name is None:
                raise TransformError("missing name")
            start_date = parse_date(payload.get("start_date"))
            if start_date is None:
                raise TransformError("namechange requires start_date")
            end_date = parse_date(payload.get("end_date"))
            ann_date = parse_date(payload.get("ann_date"))
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        if security is None:
            rejected.append(ctx.reject(
                f"security {ts_code} not standardized yet", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        ctx.securities.add_name_history(
            security_id=security["id"],
            name=name,
            start_date=start_date,
            end_date=end_date,
            ann_date=ann_date,
            source_record_id=source_id,
            recorded_at=record["retrieved_at"],
        )
        return ProcessedOutcome(1, rejected)


class IndexClassifyProcessor:
    """index_classify → 申万行业树（L1/L2/L3）。"""

    dataset_name = "index_classify"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            external_code = clean_str(payload.get("index_code"))
            name = clean_str(payload.get("industry_name")) or clean_str(payload.get("name"))
            level = clean_str(payload.get("level")) or "L1"
            if external_code is None or name is None:
                raise TransformError("index_classify requires index_code and name")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("index_code"))))
            return ProcessedOutcome(0, rejected)

        parent_code = clean_str(payload.get("parent_code"))
        parent_id = None
        if parent_code:
            parent = ctx.industries._fetchone(  # noqa: SLF001
                "SELECT id FROM master.industry WHERE external_code = %s", (parent_code,)
            )
            parent_id = parent["id"] if parent else None

        row = ctx.industries.upsert(
            taxonomy="SW",
            taxonomy_version="SW2021",
            external_code=external_code,
            level_code=level,
            name=name,
            parent_id=parent_id,
        )
        return ProcessedOutcome(1 if row["inserted"] else 0, rejected)


class IndexMemberProcessor:
    """index_member_all → 公司行业时态成员（保留 in/out/is_new）。"""

    dataset_name = "index_member_all"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            index_code = clean_str(payload.get("index_code"))
            if index_code is None:
                raise TransformError("missing index_code")
            in_date = parse_date(payload.get("in_date"))
            if in_date is None:
                raise TransformError("membership requires in_date")
            out_date = parse_date(payload.get("out_date"))
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        company_id = _company_id_for_security(ctx, security["id"]) if security else None
        if company_id is None:
            rejected.append(ctx.reject(
                f"no company anchored for {ts_code}", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        industry = ctx.industries._fetchone(  # noqa: SLF001
            "SELECT id FROM master.industry WHERE external_code = %s", (index_code,)
        )
        if industry is None:
            rejected.append(ctx.reject(
                f"industry {index_code} not standardized yet", source_id, index_code
            ))
            return ProcessedOutcome(0, rejected)

        ctx.industries.upsert_company_membership(
            company_id=company_id,
            industry_id=industry["id"],
            valid_from=in_date,
            valid_to=out_date,
            recorded_at=record["retrieved_at"],
            source_record_id=source_id,
        )
        return ProcessedOutcome(1, rejected)


class ConceptIndexProcessor:
    """ths_index / dc_index → master.concept（分平台）。"""

    def __init__(self, dataset_name: str, source_platform: str) -> None:
        self.dataset_name = dataset_name
        self.source_platform = source_platform

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            external_code = clean_str(payload.get("code")) or clean_str(payload.get("ts_code"))
            name = clean_str(payload.get("name"))
            if external_code is None or name is None:
                raise TransformError("concept requires code and name")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload)))
            return ProcessedOutcome(0, rejected)

        row = ctx.concepts.upsert(
            source_platform=self.source_platform,
            external_code=external_code,
            name=name,
        )
        return ProcessedOutcome(1 if row["inserted"] else 0, rejected)


class ConceptMemberProcessor:
    """ths_member / dc_member → 快照成员（source_platform + snapshot_date）。"""

    def __init__(self, dataset_name: str, source_platform: str) -> None:
        self.dataset_name = dataset_name
        self.source_platform = source_platform

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            concept_code = clean_str(payload.get("code")) or clean_str(
                payload.get("con_code")
            )
            snapshot_date = parse_date(payload.get("in_date")) or parse_date(
                payload.get("snapshot_date")
            )
            if concept_code is None or snapshot_date is None:
                raise TransformError("concept membership requires code and snapshot date")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        company_id = _company_id_for_security(ctx, security["id"]) if security else None
        if company_id is None:
            rejected.append(ctx.reject(
                f"no company anchored for {ts_code}", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        concept = ctx.concepts._fetchone(  # noqa: SLF001
            "SELECT id FROM master.concept "
            "WHERE source_platform = %s AND external_code = %s",
            (self.source_platform, concept_code),
        )
        if concept is None:
            rejected.append(ctx.reject(
                f"concept {self.source_platform}:{concept_code} not standardized yet",
                source_id, concept_code,
            ))
            return ProcessedOutcome(0, rejected)

        ctx.concepts.upsert_company_membership(
            company_id=company_id,
            concept_id=concept["id"],
            snapshot_date=snapshot_date,
            is_member=True,
            source_record_id=source_id,
            recorded_at=record["retrieved_at"],
        )
        return ProcessedOutcome(1, rejected)


class FinancialVipProcessor:
    """income_vip / cashflow_vip / fina_indicator_vip → FinancialObservation。

    全部口径/更新标志/公告日期保留；缺值 NULL 不转 0；币种未知 NULL。
    """

    def __init__(self, dataset_name: str, statement_type: str,
                 metrics: dict[str, tuple[str, str, str, str]]) -> None:
        self.dataset_name = dataset_name
        self.statement_type = statement_type
        self.metrics = metrics

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            period_end = parse_date(payload.get("end_date"))
            if period_end is None:
                raise TransformError("financial observation requires end_date")
            # 口径缺失绝不静默默认"合并"，使用 UNKNOWN 哨兵（视图按声明的偏好处理）
            report_type = clean_str(payload.get("report_type")) or "UNKNOWN"
            update_flag = clean_str(payload.get("update_flag"))
            announced = parse_date(payload.get("ann_date"))
            announced_at = (
                announced.isoformat() if announced else record["retrieved_at"].isoformat()
            )
            currency = clean_str(payload.get("curr_type"))  # 未知 → None，不默认 CNY
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        if security is None:
            rejected.append(ctx.reject(
                f"security {ts_code} not standardized yet", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        written = 0
        for field, (metric_code, name, unit, aggregation) in self.metrics.items():
            ctx.financials.upsert_metric(
                metric_code=metric_code,
                name=name,
                statement_type=self.statement_type,
                unit_type=unit,
                default_aggregation=aggregation,
                tushare_field=field,
            )
            if field not in payload:
                continue  # 字段缺失：不产生观察值，也不产生 0
            row = ctx.financials.upsert_observation(
                security_id=security["id"],
                metric_code=metric_code,
                period_end=period_end,
                report_type=report_type,
                value=decimal_or_null(payload.get(field)),  # 缺失保持 NULL
                currency=currency,
                announced_at=announced_at,
                update_flag=update_flag,
                source_record_id=source_id,
            )
            if row["inserted"]:
                written += 1
        return ProcessedOutcome(written, rejected)


class MainbzProcessor:
    """fina_mainbz_vip → BusinessSegment（bz_item 原样保留）。"""

    dataset_name = "fina_mainbz_vip"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            period_end = parse_date(payload.get("end_date"))
            if period_end is None:
                raise TransformError("segment requires end_date")
            raw_name = clean_str(payload.get("bz_item"))
            if raw_name is None:
                raise TransformError("segment requires bz_item")
            type_code = clean_str(payload.get("bz_code")) or "P"
            segment_type = SEGMENT_TYPE_MAP.get(type_code)
            if segment_type is None:
                raise TransformError(f"unknown bz_code {type_code}")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        company_id = _company_id_for_security(ctx, security["id"]) if security else None
        if company_id is None:
            rejected.append(ctx.reject(
                f"no company anchored for {ts_code}", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        row = ctx.financials.upsert_business_segment(
            company_id=company_id,
            period_end=period_end,
            segment_type=segment_type,
            raw_segment_name=raw_name,  # 原始披露口径，原样保留
            revenue=decimal_or_null(payload.get("bz_sales")),
            cost=decimal_or_null(payload.get("bz_cost")),
            profit=decimal_or_null(payload.get("bz_profit")),
            currency=clean_str(payload.get("curr_type")),
            source_record_id=source_id,
            recorded_at=record["retrieved_at"],
        )
        if not row["inserted"]:
            # 同键覆盖：审计事件留痕（历史在 Raw 层可重建）
            ctx.events.add(
                run_id=ctx.run_id,
                event_type="CORRECTION",
                entity_type="BUSINESS_SEGMENT",
                entity_ref=f"{ts_code}:{period_end}:{raw_name}",
                detail={"note": "segment snapshot overwritten by latest raw"},
                source_record_id=source_id,
            )
        return ProcessedOutcome(1 if row["inserted"] else 0, rejected)


class Top10HoldersProcessor:
    """top10_holders → fact.holding_observation（HOLDS 候选，无 CONTROLS）。"""

    dataset_name = "top10_holders"

    def process(self, ctx: StandardizeContext, record: dict[str, Any]) -> ProcessedOutcome:
        payload = record["payload"]
        source_id = record["source_record_id"]
        rejected: list[dict[str, Any]] = []
        try:
            ts_code = validate_ts_code(payload.get("ts_code"))
            holder_name = clean_str(payload.get("holder_name"))
            if holder_name is None:
                raise TransformError("holding requires holder_name")
            end_date = parse_date(payload.get("end_date"))
            if end_date is None:
                raise TransformError("holding requires end_date")
        except TransformError as exc:
            rejected.append(ctx.reject(str(exc), source_id, str(payload.get("ts_code"))))
            return ProcessedOutcome(0, rejected)

        security = ctx.securities.get_by_ts_code(ts_code)
        if security is None:
            rejected.append(ctx.reject(
                f"security {ts_code} not standardized yet", source_id, ts_code
            ))
            return ProcessedOutcome(0, rejected)

        row = ctx.holdings.upsert_observation(
            security_id=security["id"],
            holder_name=holder_name,
            holder_type=clean_str(payload.get("holder_type")),
            hold_amount=decimal_or_null(payload.get("hold_amount")),
            hold_ratio=decimal_or_null(payload.get("hold_ratio")),
            end_date=end_date,
            source_record_id=source_id,
        )
        return ProcessedOutcome(1 if row["inserted"] else 0, rejected)


def build_processors() -> dict[str, Any]:
    return {
        "stock_basic": StockBasicProcessor(),
        "stock_company": StockCompanyProcessor(),
        "namechange": NamechangeProcessor(),
        "index_classify": IndexClassifyProcessor(),
        "index_member_all": IndexMemberProcessor(),
        "ths_index": ConceptIndexProcessor("ths_index", "THS"),
        "ths_member": ConceptMemberProcessor("ths_member", "THS"),
        "dc_index": ConceptIndexProcessor("dc_index", "DC"),
        "dc_member": ConceptMemberProcessor("dc_member", "DC"),
        "fina_mainbz_vip": MainbzProcessor(),
        "income_vip": FinancialVipProcessor("income_vip", "INCOME", INCOME_METRICS),
        "cashflow_vip": FinancialVipProcessor(
            "cashflow_vip", "CASHFLOW", CASHFLOW_METRICS
        ),
        "fina_indicator_vip": FinancialVipProcessor(
            "fina_indicator_vip", "INDICATOR", INDICATOR_METRICS
        ),
        "top10_holders": Top10HoldersProcessor(),
    }
