import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryWorkbench } from "../routes/QueryWorkbench";
import { CompaniesPage } from "../routes/CompaniesPage";
import type { QueryResponse } from "../lib/api/types";

function renderPage(element: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider
        router={createMemoryRouter([{ path: "/", element }])}
      />
    </QueryClientProvider>,
  );
}

const GOLDEN_RESPONSE: QueryResponse = {
  query_id: "q-1",
  status: "DEGRADED",
  intent: "SEMANTIC_SCREEN",
  results: [
    {
      company_id: "c-1",
      company_name: "辛示机器人01号",
      security_code: "300101.SZ",
      standard_product: "谐波减速器",
      claim_ids: ["cl-1"],
      business_stage: "MASS_PRODUCTION",
      evidence_state: "PRODUCT_DISCLOSED",
      valid_from: "2024-01-01",
      valid_to: null,
      financial_value: 630,
      report_period: "2022-12-31..2024-12-31",
      currency: "CNY",
      financial_detail: [
        { period_end: "2024-12-31", value: 110 },
        { period_end: "2023-12-31", value: 210 },
        { period_end: "2022-12-31", value: 310 },
      ],
      evidence_ids: ["ev-1"],
      evidence_quotes: [
        {
          evidence_id: "ev-1",
          page_number: 3,
          quote_text: "谐波减速器已实现量产，报告期内批量生产并交付客户。",
        },
      ],
      reasoning_path: ["graph unavailable - PG fallback candidates"],
      data_freshness: "pg-only (graph unavailable)",
    },
  ],
  excluded: ["辛示机器人26号: insufficient financial data"],
  unknowns: ["无证据公司: claim without evidence (excluded from results)"],
  conflicts: [],
  period_rule: "LAST_3_FY_TOTAL_POSITIVE",
  degraded: true,
  degradation_notes: ["graph backend unreachable - degraded to PG-only candidates"],
  trace_id: "trace-golden-1",
  error: null,
};

function mockScreenResponse(payload: unknown, status = 200) {
  return vi.fn(async (_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return {
        ok: false,
        status,
        headers: new Headers({ "X-Trace-Id": "trace-422" }),
        json: async () => payload,
      } as Response;
    }
    void init;
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "X-Trace-Id": "trace-ok" }),
      json: async () => payload,
    } as Response;
  });
}

describe("查询工作台（issue #31 关闭条件）", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("黄金筛选：UI 结果/排除/证据/口径与 API 响应逐字段一致", async () => {
    vi.stubGlobal("fetch", mockScreenResponse(GOLDEN_RESPONSE));
    renderPage(<QueryWorkbench />);

    await userEvent.click(screen.getByRole("button", { name: "执行筛选" }));

    // 结果公司 + 证券 + 产品 + 财务值（与响应一致）
    expect(await screen.findByText("辛示机器人01号")).toBeInTheDocument();
    expect(screen.getByText("300101.SZ")).toBeInTheDocument();
    expect(screen.getByText("谐波减速器")).toBeInTheDocument();
    expect(screen.getByText(/630 CNY/)).toBeInTheDocument();

    // 状态/口径/trace_id（Epic 不变量：全部可见）
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
    expect(screen.getByText("LAST_3_FY_TOTAL_POSITIVE")).toBeInTheDocument();
    expect(screen.getByText(/trace-golden-1/)).toBeInTheDocument();

    // 降级说明 + 排除 + 未知（不能只展示命中）
    expect(screen.getByText(/graph backend unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/辛示机器人26号.*insufficient/)).toBeInTheDocument();
    expect(screen.getByText(/无证据公司.*without evidence/)).toBeInTheDocument();

    // 展开证据包：页码原文 + 推理路径 + 财务明细
    await userEvent.click(screen.getByRole("button", { name: /辛示机器人01号/ }));
    expect(screen.getByText(/第3页/)).toBeInTheDocument();
    expect(screen.getByText(/批量生产并交付客户/)).toBeInTheDocument();
    expect(screen.getByText(/graph unavailable - PG fallback/)).toBeInTheDocument();
    expect(screen.getByText("2024-12-31：110")).toBeInTheDocument();
  });

  it("空结果：显示筛选说明与排除原因，无虚构推荐", async () => {
    const empty: QueryResponse = { ...GOLDEN_RESPONSE, results: [] };
    vi.stubGlobal("fetch", mockScreenResponse(empty));
    renderPage(<QueryWorkbench />);

    await userEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    expect(await screen.findByText("无命中结果")).toBeInTheDocument();
    // 排除区（Section aria-label）存在且携带排除原因
    expect(screen.getByLabelText("被排除候选")).toBeInTheDocument();
  });

  it("非法输入（422）：展示后端受控错误与 trace_id，不发第二猜测请求", async () => {
    const fetchMock = mockScreenResponse(
      { detail: "metric 'TOTALLY_FAKE' not in allowed catalog" }, 422,
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage(<QueryWorkbench />);

    await userEvent.click(screen.getByRole("button", { name: "执行筛选" }));

    const alert = await screen.findByRole(
      "alert",
      {},
      { timeout: 2000 },
    );
    expect(alert).toBeInTheDocument();
    expect(screen.getByText("请求未通过校验")).toBeInTheDocument();
    expect(screen.getByText(/not in allowed catalog/)).toBeInTheDocument();
    expect(screen.getByText(/trace-422/)).toBeInTheDocument();
    // 只发了一次请求（无自动重试猜测）
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it("MIN_VALUE 无阈值：字段就地报错，不发请求", async () => {
    const fetchMock = mockScreenResponse(GOLDEN_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);
    renderPage(<QueryWorkbench />);

    await userEvent.selectOptions(
      screen.getByLabelText("口径（算子）"),
      "MIN_VALUE",
    );
    await userEvent.click(screen.getByRole("button", { name: "执行筛选" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("阈值");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("重复点击抑制：pending 期间按钮禁用", async () => {
    let resolveResponse: (value: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    renderPage(<QueryWorkbench />);

    const button = screen.getByRole("button", { name: "执行筛选" });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    resolveResponse({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => GOLDEN_RESPONSE,
    } as Response);
    await waitFor(() => expect(button).toBeEnabled());
  });
});

describe("公司列表页（issue #31）", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("列表渲染 + 搜索提交 + 空结果不虚构", async () => {
    const listPayload = {
      companies: [
        {
          id: "c-1",
          canonical_name: "辛示机器人01号",
          unified_social_credit_code: null,
          company_type: "LISTED_COMPANY",
          status: "ACTIVE",
          created_at: "2026-01-01T00:00:00Z",
          security_code: "300101.SZ",
          produces_claims: 18,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    };
    const fetchMock = vi.fn(async (path: string) => {
      // 搜索词进入 URL 参数（可分享链接——非敏感条件）
      expect(path).toMatch(/^\/api\/v1\/companies/);
      return {
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () =>
          path.includes("q=%E6%9C%BA%E5%99%A8")  // "机器人" URL 编码
            ? { companies: [], total: 0, limit: 20, offset: 0 }
            : listPayload,
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage(<CompaniesPage />);

    expect(await screen.findByText("辛示机器人01号")).toBeInTheDocument();
    expect(screen.getByText("300101.SZ")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /辛示机器人01号/ })).toHaveAttribute(
      "href",
      "/workbench/companies/c-1",
    );

    // 搜索空结果：空态 + 修改入口，不虚构推荐
    await userEvent.type(screen.getByLabelText("搜索公司名称"), "机器人");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText(/未找到匹配/)).toBeInTheDocument();
  });
});
