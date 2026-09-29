"""黄金查询指标计算（issue #10 验收 8）。

指标定义（tests/golden/expectations.yaml 头部同步维护）：
- precision  = |results ∩ expected| / |results|
- recall     = |results ∩ expected| / |expected|
- evidence_grounding = 携带 evidence_ids 的结果比例

纯函数，可独立单测；黄金套件失败时输出各指标实际值便于定位。
"""

from __future__ import annotations

from typing import Any


def precision(results: list[str], expected: list[str]) -> float:
    if not results:
        return 1.0 if not expected else 0.0
    hit = len(set(results) & set(expected))
    return hit / len(results)


def recall(results: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    hit = len(set(results) & set(expected))
    return hit / len(expected)


def evidence_grounding(results: list[dict[str, Any]]) -> float:
    """携带证据的结果比例（无结果时定义为 1.0——空结果无 grounding 缺陷）。"""
    if not results:
        return 1.0
    grounded = sum(1 for r in results if r.get("evidence_ids"))
    return grounded / len(results)


def evaluate_case(case: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """对照单个黄金用例期望评估响应，返回指标与失败明细。"""
    results = body.get("results", [])
    names = [r.get("company_name") for r in results]
    expected = case.get("expected_company_names", [])
    forbidden = case.get("must_not_contain", [])

    failures: list[str] = []
    if "expected_status_code" in case:
        # 受控拒绝场景（如注入 422）：无 results 可评
        return {
            "case_id": case["id"], "passed": True, "failures": [],
            "precision": 1.0, "recall": 1.0, "evidence_grounding": 1.0,
        }
    prec = precision(names, expected)
    rec = recall(names, expected)
    grounding = evidence_grounding(results)

    if "expected_status" in case and body.get("status") != case["expected_status"]:
        failures.append(
            f"status {body.get('status')} != {case['expected_status']}"
        )
    if "expected_period_rule" in case and (
        body.get("period_rule") != case["expected_period_rule"]
    ):
        failures.append(
            f"period_rule {body.get('period_rule')} != "
            f"{case['expected_period_rule']}"
        )
    if prec < case["min_precision"]:
        failures.append(f"precision {prec:.3f} < {case['min_precision']}")
    if rec < case["min_recall"]:
        failures.append(f"recall {rec:.3f} < {case['min_recall']}")
    if grounding < case["min_evidence_grounding"]:
        failures.append(
            f"evidence_grounding {grounding:.3f} < "
            f"{case['min_evidence_grounding']}"
        )
    unexpected = sorted(set(names) - set(expected))
    if unexpected:
        failures.append(f"unexpected results: {unexpected}")
    leaked = sorted(set(names) & set(forbidden))
    if leaked:
        failures.append(f"forbidden companies in results: {leaked}")
    for name in case.get("expect_unknowns_contain", []):
        unknowns = body.get("unknowns", []) + body.get("excluded", [])
        if not any(name in item for item in unknowns):
            failures.append(f"{name} missing from unknowns/excluded")
    for name, value in case.get("expected_financial_value", {}).items():
        matched = next(
            (r for r in results if r.get("company_name") == name), None
        )
        if matched is None:
            failures.append(f"{name} missing for financial value check")
        elif matched.get("financial_value") != value:
            failures.append(
                f"{name} financial_value {matched.get('financial_value')} "
                f"!= {value}"
            )
    return {
        "case_id": case["id"], "passed": not failures,
        "failures": failures,
        "precision": prec, "recall": rec, "evidence_grounding": grounding,
    }
