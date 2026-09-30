/**
 * API 契约一致性检查（#30 证据：客户端与 FastAPI OpenAPI 契约不漂移）。
 *
 * web/openapi.json 由 scripts/export_openapi.py 生成并随提交锁定；
 * 后端契约变化后 CI 重新导出即产生 diff（runtime-ci 的 contract job），
 * 本测试再断言 client 实际消费的路径与字段在契约中存在。
 */
import { describe, expect, it } from "vitest";
import openapi from "../../openapi.json";

interface OpenApiDoc {
  paths: Record<string, Record<string, unknown>>;
  components?: {
    schemas?: Record<string, { properties?: Record<string, unknown> }>;
  };
}

const doc = openapi as unknown as OpenApiDoc;

const CLIENT_PATHS: [string, string][] = [
  ["/readyz", "get"],
  ["/healthz", "get"],
  ["/api/v1/screen", "post"],
  ["/api/v1/query", "post"],
  ["/api/v1/companies/{company_id}", "get"],
  ["/api/v1/companies/{company_id}/claims", "get"],
  ["/api/v1/companies/{company_id}/timeline", "get"],
];

describe("API 契约一致性", () => {
  it.each(CLIENT_PATHS)("client 消费的 %s %s 在 OpenAPI 契约中", (path, method) => {
    expect(doc.paths[path], `missing path ${path}`).toBeDefined();
    expect(doc.paths[path]![method], `missing method ${method} on ${path}`).toBeDefined();
  });

  it("QueryResponse 契约包含 GroundedResult 关键字段", () => {
    const schemas = doc.components?.schemas ?? {};
    const grounded = Object.entries(schemas).find(
      ([name]) => name === "GroundedResult",
    );
    expect(grounded).toBeDefined();
    const properties = Object.keys(grounded![1].properties ?? {});
    for (const field of [
      "company_id",
      "company_name",
      "security_code",
      "standard_product",
      "claim_ids",
      "evidence_quotes",
      "valid_from",
      "financial_detail",
      "data_freshness",
    ]) {
      expect(properties, `GroundedResult lacks ${field}`).toContain(field);
    }
  });

  it("ScreenRequest 契约受控（无自由字段）", () => {
    const schemas = doc.components?.schemas ?? {};
    const screen = schemas["ScreenRequest"];
    expect(screen).toBeDefined();
    const properties = Object.keys(screen!.properties ?? {});
    // 受控表单字段集（product-ui.md §1 原则 3：只暴露 Schema 允许的字段）
    expect(properties).toEqual(
      expect.arrayContaining([
        "concept_name",
        "business_stage",
        "metric_code",
        "operator",
        "fiscal_years",
        "evidence_required",
        "as_of",
        "max_results",
      ]),
    );
  });
});
