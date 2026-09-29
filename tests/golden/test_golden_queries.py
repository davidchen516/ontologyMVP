"""黄金查询回归（issue #10 验收 8）。

期望基线：tests/golden/expectations.yaml（更新需在 PR 说明理由 + 独立
审查确认）。指标：Precision / Recall / Evidence Grounding，计算逻辑在
metrics.py（纯函数）；时态正确性由 as_of/known_at 场景断言锁定，
路径正确性由真实 Neo4j 读路径 e2e 锁定（tests/integration）。

场景全部走无图执行器的 API 全链路（TestClient），锁定的不只是查询
正确性，还包括降级注记、口径返回、受控拒绝等响应语义。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.golden.metrics import evaluate_case


def _run_case(client: TestClient, case: dict[str, Any]) -> dict[str, Any] | int:
    response = client.post(
        case["request"]["path"], json=case["request"]["body"]
    )
    if "expected_status_code" in case:
        return response.status_code
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("case_index", range(7), ids=lambda i: f"case{i}")
def test_golden_case(
    golden_client: TestClient, expectations: list[dict[str, Any]],
    case_index: int,
) -> None:
    case = expectations[case_index]
    outcome = _run_case(golden_client, case)

    if isinstance(outcome, int):
        # 受控拒绝场景：断言精确状态码
        assert outcome == case["expected_status_code"], (
            f"{case['id']}: got HTTP {outcome}"
        )
        return

    verdict = evaluate_case(case, outcome)
    assert verdict["passed"], (
        f"{case['id']} failed: {verdict['failures']}\n"
        f"metrics: precision={verdict['precision']:.3f} "
        f"recall={verdict['recall']:.3f} "
        f"grounding={verdict['evidence_grounding']:.3f}"
    )


def test_injection_payload_never_touches_database(
    golden_client: TestClient, expectations: list[dict[str, Any]]
) -> None:
    """验收 3：注入用例除受控 422 外零副作用（事实层行数不变）。"""
    import psycopg

    case = next(
        c for c in expectations if c["id"] == "injection_never_executes"
    )
    dsn = golden_client.app.state.settings.postgres_dsn
    with psycopg.connect(dsn) as conn:
        before_claims = conn.execute(
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0]

    response = golden_client.post(
        case["request"]["path"], json=case["request"]["body"]
    )
    assert response.status_code == case["expected_status_code"]

    with psycopg.connect(dsn) as conn:
        after_claims = conn.execute(
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0]
    assert after_claims == before_claims


def test_golden_metrics_module() -> None:
    """指标函数本身的已知值守护（防止指标静默退化）。"""
    from tests.golden.metrics import (
        evidence_grounding,
        precision,
        recall,
    )

    assert precision(["A", "B"], ["A"]) == 0.5
    assert precision([], ["A"]) == 0.0
    assert precision([], []) == 1.0
    assert recall(["A"], ["A", "B"]) == 0.5
    assert recall(["A"], []) == 1.0
    assert evidence_grounding([]) == 1.0
    assert evidence_grounding([{"evidence_ids": ["e"]}]) == 1.0
    assert evidence_grounding([{"evidence_ids": []}, {"evidence_ids": ["e"]}]) == 0.5


def test_expectations_file_complete() -> None:
    """期望文件结构守护：每用例必备字段齐全（防止基线文件静默损坏）。"""
    import yaml

    from tests.golden.conftest import GOLDEN_DIR

    data = yaml.safe_load((GOLDEN_DIR / "expectations.yaml").read_text("utf-8"))
    assert data["suite"] == "golden-query-v1"
    required = {"request", "min_precision", "min_recall",
                "min_evidence_grounding"}
    assert len(data["cases"]) == 7
    for case in data["cases"]:
        missing = required - set(case)
        assert not missing, f"{case.get('id')}: missing {missing}"
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids)), "duplicate case ids"
