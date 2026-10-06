"""熔断解除服务（issue #44）：SCHEMA_CHANGED → 验证探针 → AVAILABLE/回滚。

状态机（issue #44 补充节）：
```
SCHEMA_CHANGED --(reset: 认证通过 + reason + confirm)--> 待验证态
待验证态 --(单发验证探针成功)--> AVAILABLE
待验证态 --(探针返回 SCHEMA_CHANGED)--> SCHEMA_CHANGED（审计记"解除验证失败"）
待验证态 --(探针超时/进程崩溃)--> 待验证态（保持 AVAILABLE+detail.reset_pending）
```

审计不变量（I1）：每次状态迁移（含失败回滚与 no-op）各产生一条
ops.audit_event（actor/reason/前态/后态/trace_id）。
单发验证（D-4）：仅触发一次探针；超时/崩溃不自动重试（防额度循环）。
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from src.db.uow import UnitOfWork

log = structlog.get_logger(__name__)

RESET_EVENT_TYPE = "CAPABILITY_RESET"


class CapabilityResetError(Exception):
    """解除请求校验失败（4xx 语义，不产生状态审计）。"""


def reset_capability(
    uow: UnitOfWork,
    *,
    source_system: str,
    api_name: str,
    actor: str,
    reason: str,
    probe_fn: Any | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """执行一次带审计的熔断解除 + 单发验证探针。

    probe_fn: 可注入的探针函数（生产为 connector.probe；测试可 stub）。
    返回 {status, reset_applied, probe_result: {...}, detail}。
    """
    current = uow.source_capabilities.get_status(source_system, api_name)
    if current is None:
        raise CapabilityResetError(f"capability {source_system}/{api_name} not found")
    current_status = current["status"]

    if current_status != "SCHEMA_CHANGED":
        # no-op：幂等返回当前状态 + 1 条 no-op 审计（不触发探针）
        _audit_reset(
            uow, source_system, api_name, actor, reason, trace_id,
            before=current_status, after=current_status, no_op=True,
        )
        return {
            "status": current_status,
            "reset_applied": False,
            "no_op_reason": f"current status is {current_status}, not SCHEMA_CHANGED",
            "detail": current.get("detail", {}),
        }

    # 1. 解除：SCHEMA_CHANGED → AVAILABLE + reset_pending 标记（事务内）
    uow.source_capabilities.upsert(
        source_system=source_system, api_name=api_name,
        status="AVAILABLE",
        detail={"reset_pending": True, "reset_reason": reason[:300],
                "reset_by": actor},
    )
    _audit_reset(
        uow, source_system, api_name, actor, reason, trace_id,
        before="SCHEMA_CHANGED", after="RESET_PENDING",
    )

    # 2. 单发验证探针（D-4：仅一次，异常不重试）
    if probe_fn is None:
        probe_result: dict[str, Any] = {"status": "PROBE_SKIPPED"}
        probe_status = "AVAILABLE"
        # 无探针函数（测试或无连接器场景）：保持 reset_pending 供下次显式触发
        uow.source_capabilities.upsert(
            source_system=source_system, api_name=api_name,
            status="AVAILABLE",
            detail={"reset_pending": True, "reset_reason": reason[:300],
                    "reset_by": actor},
        )
        _audit_reset(
            uow, source_system, api_name, actor, reason, trace_id,
            before="RESET_PENDING", after="RESET_PENDING",
            probe_outcome="probe_skipped",
        )
        return {"status": "AVAILABLE", "reset_applied": True,
                "probe_result": probe_result,
                "detail": {"reset_pending": True}}

    try:
        probe = probe_fn()
        probe_status = str(getattr(probe, "status", probe.get("status", "UNKNOWN"))
                           if isinstance(probe, dict) else probe.status)
        probe_metadata = (
            probe.get("metadata", {}) if isinstance(probe, dict)
            else getattr(probe, "metadata", {})
        )
    except Exception as exc:  # noqa: BLE001 — 探针崩溃：停 RESET_PENDING 不伪装
        log.warning("capability_reset_probe_crashed",
                    api_name=api_name, error=type(exc).__name__)
        _audit_reset(
            uow, source_system, api_name, actor, reason, trace_id,
            before="RESET_PENDING", after="RESET_PENDING",
            probe_outcome=f"probe_crashed:{type(exc).__name__}",
        )
        return {
            "status": "AVAILABLE",
            "reset_applied": True,
            "probe_result": {"status": "PROBE_CRASHED",
                             "error": type(exc).__name__},
            "detail": {"reset_pending": True},
        }

    # 3. 根据探针结果更新
    if probe_status == "AVAILABLE":
        detail = {"reset_verified": True, **{k: v for k, v in
                   (probe_metadata or {}).items() if isinstance(v, (str, int, float, list))}}
        uow.source_capabilities.upsert(
            source_system=source_system, api_name=api_name,
            status="AVAILABLE", detail=detail,
        )
        _audit_reset(
            uow, source_system, api_name, actor, reason, trace_id,
            before="RESET_PENDING", after="AVAILABLE",
            probe_outcome="probe_available",
        )
        return {"status": "AVAILABLE", "reset_applied": True,
                "probe_result": {"status": probe_status,
                                  "metadata": probe_metadata},
                "detail": detail}
    # 探针失败（含返回 SCHEMA_CHANGED）→ 回滚熔断（I2）
    uow.source_capabilities.upsert(
        source_system=source_system, api_name=api_name,
        status=probe_status if probe_status in (
            "SCHEMA_CHANGED", "NO_PERMISSION", "SEPARATE_PERMISSION_REQUIRED",
            "RATE_LIMITED", "NETWORK_ERROR", "UNKNOWN") else "UNKNOWN",
        detail={"reset_verified": False,
                "probe_status": probe_status},
    )
    _audit_reset(
        uow, source_system, api_name, actor, reason, trace_id,
        before="RESET_PENDING", after=probe_status,
        probe_outcome="reset_verification_failed",
    )
    return {"status": probe_status, "reset_applied": True,
            "probe_result": {"status": probe_status},
            "detail": {"reset_verified": False}}


def _audit_reset(
    uow: UnitOfWork, source_system: str, api_name: str,
    actor: str, reason: str, trace_id: str | None, *,
    before: str, after: str, no_op: bool = False,
    probe_outcome: str | None = None,
) -> None:
    """审计一次解除状态迁移（I1：无审计的迁移 = 验收失败）。"""
    payload: dict[str, Any] = {
        "source_system": source_system, "api_name": api_name,
        "actor": actor, "reason": reason[:300],
        "before_status": before, "after_status": after,
        "no_op": no_op,
    }
    if probe_outcome:
        payload["probe_outcome"] = probe_outcome
    uow.audit_events.insert(
        entity_type="SourceCapability",
        entity_id=uuid.uuid5(uuid.NAMESPACE_URL,
                             f"capability:{source_system}:{api_name}"),
        event_type=RESET_EVENT_TYPE,
        payload=payload,
        trace_id=trace_id,
    )
