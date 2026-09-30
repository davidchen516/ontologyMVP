import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GraphBrowser } from "../routes/GraphBrowser";
import { EvidenceBrowser } from "../routes/EvidenceBrowser";
import type { SubgraphResponse, ClaimLineageResponse } from "../lib/api/types";

function renderPage(element: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: "/", element }])} />
    </QueryClientProvider>,
  );
}

const SUBGRAPH_OK: SubgraphResponse = {
  status: "SUCCEEDED",
  reason: null,
  center: { id: "c-1", name: "中心公司" },
  nodes: [
    { id: "c-1", labels: ["Company"], name: "中心公司" },
    { id: "p-1", labels: ["Product"], name: "谐波减速器" },
    { id: "k-1", labels: ["Concept"], name: "人形机器人" },
  ],
  edges: [{ type: "PRODUCES", claim_id: "cl-1" }],
  hops: 1,
  max_nodes: 100,
  truncated: false,
};

const SUBGRAPH_DEGRADED: SubgraphResponse = {
  status: "DEGRADED",
  reason: "graph backend unreachable: ServiceUnavailable",
  center: null, nodes: [], edges: [],
  hops: 1, max_nodes: 100, truncated: false,
};

const SUBGRAPH_TRUNCATED: SubgraphResponse = {
  status: "SUCCEEDED", reason: null,
  center: { id: "c-1", name: "中心" },
  nodes: Array.from({ length: 100 }, (_, i) => ({
    id: `n-${i}`, labels: ["Product"], name: `产品${i}`,
  })),
  edges: [], hops: 2, max_nodes: 100, truncated: true,
};

const LINEAGE: ClaimLineageResponse = {
  claim: {
    id: "cl-1", predicate_code: "PRODUCES", claim_status: "ACCEPTED",
    business_stage: "MASS_PRODUCTION", evidence_state: "PRODUCT_DISCLOSED",
    confidence: 0.85, valid_from: "2024-01-01", valid_to: null,
    recorded_at: "2026-09-30T00:00:00Z", object_entity_id: null,
  },
  evidence: [{
    id: "ev-1", document_id: "d-1", page_number: 3,
    quote_text: "谐波减速器已实现量产，报告期内批量生产并交付客户。",
    char_start: 0, char_end: 24, document_version_id: null,
  }],
  documents: [{
    id: "d-1", document_type: "ANNUAL_REPORT", source_system: "CNINFO",
    title: "年度报告", published_at: null, version: null, parse_status: null,
  }],
};

const COMPANIES_MOCK = {
  companies: [{
    id: "c-1", canonical_name: "中心公司",
    unified_social_credit_code: null, company_type: null,
    status: null, created_at: "2026-01-01", security_code: null,
    produces_claims: 1,
  }],
  total: 1, limit: 100, offset: 0,
};

function mockFetchByPath(routes: Record<string, unknown>) {
  return vi.fn(async (path: string) => {
    for (const [prefix, payload] of Object.entries(routes)) {
      if (path.startsWith(prefix)) {
        return {
          ok: true, status: 200, headers: new Headers(),
          json: async () => payload,
        } as Response;
      }
    }
    throw new TypeError(`unexpected path ${path}`);
  });
}

describe("图谱浏览器（issue #32 关闭条件）", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("GWT：图后端不可用 → DEGRADED 显示 + 原因 + PG 事实提示", async () => {
    vi.stubGlobal("fetch", mockFetchByPath({
      "/api/v1/graph/subgraph": SUBGRAPH_DEGRADED,
      "/api/v1/companies": COMPANIES_MOCK,
    }));
    renderPage(<GraphBrowser />);

    // 等公司选项加载（companies 查询异步）
    const option = await screen.findByRole("option", { name: "中心公司" });
    await userEvent.selectOptions(
      screen.getByLabelText("选择公司"),
      (option as HTMLOptionElement).value,
    );
    await userEvent.click(screen.getByRole("button", { name: "加载子图" }));

    expect(await screen.findByText(/图后端不可用（DEGRADED）/)).toBeInTheDocument();
    expect(screen.getByText(/ServiceUnavailable/)).toBeInTheDocument();
    expect(
      screen.getByText(/PostgreSQL 事实/),
    ).toBeInTheDocument();
  });

  it("GWT：节点超预算 → 裁剪说明可见", async () => {
    vi.stubGlobal("fetch", mockFetchByPath({
      "/api/v1/graph/subgraph": SUBGRAPH_TRUNCATED,
      "/api/v1/companies": COMPANIES_MOCK,
    }));
    renderPage(<GraphBrowser />);
    const option = await screen.findByRole("option", { name: "中心公司" });
    await userEvent.selectOptions(
      screen.getByLabelText("选择公司"),
      (option as HTMLOptionElement).value,
    );
    await userEvent.click(screen.getByRole("button", { name: "加载子图" }));

    expect(await screen.findByText(/已按节点预算（100）裁剪/)).toBeInTheDocument();
  });

  it("GWT：键盘用户 → 表格替代视图承载全部关系 + 证据链入口", async () => {
    vi.stubGlobal("fetch", mockFetchByPath({
      "/api/v1/graph/subgraph": SUBGRAPH_OK,
      "/api/v1/companies": COMPANIES_MOCK,
      "/api/v1/claims/cl-1/lineage": LINEAGE,
    }));
    renderPage(<GraphBrowser />);
    const option = await screen.findByRole("option", { name: "中心公司" });
    await userEvent.selectOptions(
      screen.getByLabelText("选择公司"),
      (option as HTMLOptionElement).value,
    );
    await userEvent.click(screen.getByRole("button", { name: "加载子图" }));
    await waitFor(() =>
      expect(screen.getByText(/3 节点/)).toBeInTheDocument(),
    );

    // 切换表格视图（radiogroup，键盘可达）
    await userEvent.click(screen.getByRole("radio", { name: "表格" }));
    expect(
      screen.getByRole("table", { name: /图谱关系列表/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("PRODUCES")).toBeInTheDocument();
    expect(screen.getByText("cl-1")).toBeInTheDocument();

    // 证据链 → 抽屉（Claim → 页码原文 → 来源文档）
    await userEvent.click(screen.getByRole("button", { name: "查看证据链" }));
    expect(await screen.findByRole("dialog", { name: "证据追溯链" })).toBeInTheDocument();
    expect(screen.getByText(/第 3 页/)).toBeInTheDocument();
    expect(screen.getByText(/批量生产并交付客户/)).toBeInTheDocument();
    expect(screen.getByText("年度报告")).toBeInTheDocument();
    expect(screen.getByText(/CNINFO/)).toBeInTheDocument();
  });

  it("初始空态：引导选择公司", () => {
    vi.stubGlobal("fetch", mockFetchByPath({
      "/api/v1/companies": {
        companies: [], total: 0, limit: 100, offset: 0,
      },
    }));
    renderPage(<GraphBrowser />);
    expect(screen.getByText("选择公司以加载子图")).toBeInTheDocument();
    expect(
      screen.getByText(/白名单 Cypher 模板生成/),
    ).toBeInTheDocument();
  });
});

describe("证据浏览器（issue #32）", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("文档证据：片段页码/原文/字符区间 + 版本状态如实", async () => {
    vi.stubGlobal("fetch", mockFetchByPath({
      "/api/v1/documents/d-1/evidence": {
        document: {
          id: "d-1", document_type: "ANNUAL_REPORT",
          source_system: "CNINFO", title: "年度报告",
          published_at: null, parse_status: "PARSED",
          version: null, version_id: null,
        },
        fragments: [{
          id: "ev-1", document_id: "d-1", page_number: 5,
          quote_text: "伺服系统已实现量产。",
          char_start: 10, char_end: 30, document_version_id: null,
        }],
        count: 1,
      },
    }));
    renderPage(<EvidenceBrowser />);

    await userEvent.type(screen.getByLabelText("文档 ID"), "d-1");
    await userEvent.click(screen.getByRole("button", { name: "查看证据" }));

    expect(await screen.findByText("年度报告")).toBeInTheDocument();
    expect(screen.getByText(/第 5 页/)).toBeInTheDocument();
    expect(screen.getByText(/字符 10–30/)).toBeInTheDocument();
    expect(screen.getByText(/伺服系统已实现量产/)).toBeInTheDocument();
    // 版本缺失如实呈现（不伪造）
    expect(screen.getByText(/版本 无/)).toBeInTheDocument();
  });
});
