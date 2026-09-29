"""MVP 首个 QueryPlan 演示（issue #11）。

对已构建的快照库执行业务目标查询：
  Theme=人形机器人 + Product∈核心零部件 + BusinessStage=MASS_PRODUCTION
  + ClaimStatus=ACCEPTED + Evidence exists + 最近3个完整财年经营现金流合计>0

对每家命中公司输出：证券、标准产品、业务阶段、Claim ID、证据（可定位
片段 ID）、有效时间、三年现金流及口径、推理路径、数据新鲜度和未知项。

用法：
  uv run python scripts/demo_mvp_query.py --dsn <snapshot-dsn> [--with-graph]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from apps.api.app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.core.config import Settings  # noqa: E402


def build_client(dsn: str) -> TestClient:
    """从 DSN 构造查询客户端（图执行器按 --with-graph 决定）。"""
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(dsn)
    settings_kwargs: dict[str, object] = {
        "postgres_host": params["host"],
        "postgres_port": int(params.get("port") or 5432),
        "postgres_db": params["dbname"],
        "postgres_user": params["user"],
        "postgres_password": params["password"],
        "neo4j_uri": "bolt://127.0.0.1:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "placeholder-not-used",
        "tushare_token": None,
        "llm_api_key": None,
    }
    import os

    for env_name, key in (("NEO4J_URI", "neo4j_uri"),
                          ("NEO4J_USER", "neo4j_user"),
                          ("NEO4J_PASSWORD", "neo4j_password")):
        value = os.environ.get(env_name)
        if value:
            settings_kwargs[key] = value  # type: ignore[assignment]
    app = create_app(Settings(**settings_kwargs))  # type: ignore[arg-type]
    return TestClient(app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()

    client = build_client(args.dsn)
    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
        "metric_code": "NET_CF_OPERATING",
        "operator": "TOTAL_POSITIVE",
        "evidence_required": True,
        "max_results": 100,
    })
    body = response.json()
    if response.status_code != 200:
        print(f"query failed: HTTP {response.status_code}: {body}")
        return 1

    print("=" * 72)
    print("人形机器人核心零部件证据化筛选（首个 QueryPlan）")
    print("口径：最近 3 个完整财年经营活动现金流合计 > 0")
    print("约束：MASS_PRODUCTION × ACCEPTED Claim × Evidence × 财务达标")
    print("=" * 72)
    print(f"查询状态：{body['status']}  降级：{body['degraded']}"
          f"  口径：{body['period_rule']}")
    if body["degradation_notes"]:
        print("降级说明：", *body["degradation_notes"], sep="\n  - ")
    print(f"命中公司：{len(body['results'])} 家；"
          f"排除 {len(body['excluded'])}；证据不足 {len(body['unknowns'])}"
          f"；冲突警告 {len(body['conflicts'])}")
    for i, result in enumerate(body["results"], start=1):
        print(f"\n[{i}] {result['company_name']} ({result['company_id']})")
        print(f"    业务阶段：{result['business_stage']}"
              f"  证据状态：{result['evidence_state']}")
        print(f"    Claim ID：{', '.join(result['claim_ids'][:3])}"
              f"{' …' if len(result['claim_ids']) > 3 else ''}")
        print(f"    证据片段：{', '.join(result['evidence_ids'][:2])}"
              f"{' …' if len(result['evidence_ids']) > 2 else ''}")
        print(f"    三年现金流合计：{result['financial_value']} "
              f"{result['currency']}  报告期：{result['report_period']}")
        for detail in result["financial_detail"]:
            print(f"      - {detail['period_end']}: {detail['value']}")
        print("    推理路径：")
        for step in result["reasoning_path"]:
            print(f"      · {step}")
        print(f"    数据新鲜度：{result['data_freshness']}")
    if body["unknowns"]:
        print("\n证据不足（不进入正式结果，也不断言无业务）：")
        for item in body["unknowns"]:
            print(f"  - {item}")
    if body["conflicts"]:
        print("\n冲突警告：")
        for item in body["conflicts"]:
            print(f"  - {item}")
    print("\n免责声明：本演示基于确定性数据快照，不构成投资建议，"
          "不输出买卖指令；所有经营结论均可回溯至 Claim 与可定位 Evidence。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
