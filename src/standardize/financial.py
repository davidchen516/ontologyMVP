"""财务当前值选择规则与"最近三个完整财年经营现金流"服务（issue #4）。

选择规则（ontology/mappings/tushare.yaml financial_version_selection）：
- 保留全部版本（重述/口径/更新标志不覆盖）；
- 当前值 = 每组 (security, metric, period) 内：
  1) 报表口径偏好 CONSOLIDATED > PARENT > UNKNOWN（由 report_type 映射）；
  2) 最新公告日期优先；
  3) 公告日期相同取最新 update_flag；
- 查询响应必须携带选择策略（不静默切换）；
- 财务缺失值保持 NULL，绝不转 0；"三个完整财年"要求 3 个有效 FY 观察值。
"""

from __future__ import annotations

from typing import Any

from src.db.uow import UnitOfWork

SELECTION_POLICY: dict[str, Any] = {
    "statement_scope_preference": ["CONSOLIDATED", "PARENT", "UNKNOWN"],
    "version_preference": ["latest_announcement_date", "latest_update_flag"],
    "retain_all_versions": True,
    "exposed_in_response": True,
}

OPERATING_CASHFLOW_METRIC = "NET_CF_OPERATING"


def current_financial_observations(
    uow: UnitOfWork, *, security_id: Any, metric_code: str | None = None
) -> dict[str, Any]:
    """当前有效财务观察（确定性视图），响应携带选择策略。"""
    query = (
        "SELECT id, security_id, metric_code, period_end, report_type, value, "
        "currency, announced_at, update_flag FROM finance.v_financial_observation_current "
        "WHERE security_id = %s"
    )
    params: list[Any] = [security_id]
    if metric_code:
        query += " AND metric_code = %s"
        params.append(metric_code)
    query += " ORDER BY metric_code, period_end DESC"
    cur = uow._conn.execute(query, tuple(params))  # noqa: SLF001
    columns = [desc.name for desc in cur.description]
    rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    return {"policy": SELECTION_POLICY, "observations": rows}


def recent_three_fy_operating_cashflow(uow: UnitOfWork, *, security_id: Any) -> dict[str, Any]:
    """最近三个完整财年经营活动现金流；不足 3 个有效 FY 观察值时明确"数据不足"。

    - 只接受完整财年观察（period_end 为 12-31 的年报口径）且 value 非 NULL；
    - 重述由当前值视图按明示规则选择（同财年取最新公告/口径），不使用被替代旧值；
    - 返回携带 policy 与明细，调用方可解释每个财年取的是哪个版本。
    """
    cur = uow._conn.execute(  # noqa: SLF001
        """
        SELECT period_end, value, report_type, update_flag, announced_at
        FROM finance.v_financial_observation_current
        WHERE security_id = %s
          AND metric_code = %s
          AND EXTRACT(MONTH FROM period_end) = 12
          AND EXTRACT(DAY FROM period_end) = 31
          AND value IS NOT NULL
        ORDER BY period_end DESC
        """,
        (security_id, OPERATING_CASHFLOW_METRIC),
    )
    columns = [desc.name for desc in cur.description]
    valid_fys = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    three = valid_fys[:3]
    sufficient = len(three) == 3
    return {
        "security_id": str(security_id),
        "metric_code": OPERATING_CASHFLOW_METRIC,
        "policy": SELECTION_POLICY,
        "sufficient": sufficient,
        "reason": None if sufficient else "insufficient_complete_fiscal_years",
        "fiscal_years": [
            {
                "period_end": row["period_end"].isoformat(),
                "value": float(row["value"]),
                "selected_report_type": row["report_type"],
                "selected_update_flag": row["update_flag"],
                "announced_at": row["announced_at"],
            }
            for row in three
        ],
        "available_complete_fy_count": len(valid_fys),
    }
