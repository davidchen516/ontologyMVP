import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkbench } from "../routes/ReviewWorkbench";
import { OpsPage } from "../routes/OpsPage";

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

const READY_WRITE_ON = {
  status: "DEGRADED",
  components: { postgres: { status: "OK" }, neo4j: { status: "OK" } },
  capabilities: { tushare: { status: "UNAVAILABLE" }, llm: { status: "UNAVAILABLE" }, review_write: { status: "OK" } },
};

const QUEUE = {
  tasks: [{
    task_id: "t-1", task_status: "OPEN", priority: "HIGH",
    reason_codes: ["hedged"], claim_id: "c-1",
    predicate_code: "PRODUCES", claim_status: "NEEDS_REVIEW",
    business_stage: "SAMPLE_VALIDATION", evidence_state: "PRODUCT_DISCLOSED",
    confidence: 0.5, subject_entity_id: "co-1", object_value: null,
    evidence_count: 1,
  }],
  total: 1,
  write_available: true,
};

describe("审核工作台（issue #33 关闭条件）", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("GWT：非 Reviewer/开关关闭 → 登录入口不渲染、只读引导", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path === "/readyz") {
        return { ok: true, status: 200, headers: new Headers(),
                 json: async () => ({
                   ...READY_WRITE_ON,
                   capabilities: { ...READY_WRITE_ON.capabilities,
                     review_write: { status: "UNAVAILABLE" } },
                 }) } as Response;
      }
      throw new TypeError("unexpected");
    }));
    renderPage(<ReviewWorkbench />);
    expect(await screen.findByText("审核功能未启用")).toBeInTheDocument();
    // 登录输入框不渲染（能力感知——无死按钮/无效入口）
    expect(screen.queryByLabelText("Reviewer Key")).not.toBeInTheDocument();
  });

  it(
    "GWT：Reviewer 进入队列 → 任务摘要 + 决定须理由 + 409 冲突提示",
    { timeout: 10_000 },
    async () => {
    const user = userEvent.setup({ delay: null });
    let decided = false;
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path === "/readyz") {
        return { ok: true, status: 200, headers: new Headers(),
                 json: async () => READY_WRITE_ON } as Response;
      }
      if (path === "/api/v1/review/queue") {
        return { ok: true, status: 200, headers: new Headers(),
                 json: async () => QUEUE } as Response;
      }
      if (path.startsWith("/api/v1/review/tasks/t-1/decision")) {
        if (decided) {
          return { ok: false, status: 409, headers: new Headers(),
                   json: async () => ({ detail: {
                     message: "review task was modified by another reviewer",
                     reason: "concurrent update on claim c-1",
                     current_task_status: "COMPLETED",
                     current_decision: "ACCEPT",
                     current_claim_status: "ACCEPTED" } }) } as Response;
        }
        decided = true;
        return { ok: true, status: 200, headers: new Headers(),
                 json: async () => ({ idempotent_replay: false, task_id: "t-1",
                   before_status: "NEEDS_REVIEW", after_status: "ACCEPTED" }) } as Response;
      }
      throw new TypeError(`unexpected ${path}`);
    }));
    renderPage(<ReviewWorkbench />);

    // 登录（key 内存态）
    await user.type(await screen.findByLabelText("Reviewer Key"), "valid-key");
    await user.click(screen.getByRole("button", { name: "进入审核工作台" }));

    // 队列渲染：任务摘要字段
    expect(await screen.findByText(/PRODUCES/)).toBeInTheDocument();
    expect(screen.getByText(/证据 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/优先级 HIGH/)).toBeInTheDocument();

    // 接受 → 理由必填 → 提交
    await user.click(screen.getByRole("button", { name: "接受" }));
    expect(screen.getByRole("dialog", { name: "审核决定" })).toBeInTheDocument();
    // 状态机后果可见
    expect(screen.getByText(/NEEDS_REVIEW → ACCEPTED/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "确认提交" });
    expect(submit).toBeDisabled(); // 理由为空时禁用
    await user.type(screen.getByLabelText("决定理由"), "证据充分");
    await user.click(submit);

    // 第一次成功 → 再决定 → 409 冲突（decided=true 后 mock 全返回 409）
    await waitFor(() => expect(decided).toBe(true));
    await user.click(screen.getByRole("button", { name: "接受" }));
    // 第二个对话框打开后填写提交
    const reason2 = await screen.findByLabelText("决定理由");
    await user.type(reason2, "再次提交");
    await user.click(screen.getByRole("button", { name: "确认提交" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("并发冲突"),
      { timeout: 4000 },
    );
    // B1 修复契约：后端回传的当前状态也如实渲染
    expect(screen.getByRole("alert")).toHaveTextContent("ACCEPTED");
    },
  );

  it("Reviewer key 不入 localStorage/URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path === "/readyz") {
        return { ok: true, status: 200, headers: new Headers(),
                 json: async () => READY_WRITE_ON } as Response;
      }
      return { ok: true, status: 200, headers: new Headers(),
               json: async () => QUEUE } as Response;
    }));
    renderPage(<ReviewWorkbench />);
    await userEvent.type(await screen.findByLabelText("Reviewer Key"), "secret-key-123");
    await userEvent.click(screen.getByRole("button", { name: "进入审核工作台" }));
    await screen.findByText(/PRODUCES/);
    expect(localStorage.getItem("reviewerKey")).toBeNull();
    expect(window.location.search).not.toContain("secret");
  });
});

describe("运维页（issue #33）", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("能力矩阵 + 运行状态 + 投影对账（真实 /admin/* 数据形状）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      const payloads: Record<string, unknown> = {
        "/readyz": { status: "DEGRADED",
          components: { postgres: { status: "OK" }, neo4j: { status: "OK" } },
          capabilities: { tushare: { status: "UNAVAILABLE" },
            review_write: { status: "OK" } } },
        "/admin/capabilities": [
          { source_system: "TUSHARE", api_name: "stock_basic", status: "OK" },
        ],
        "/admin/ingest-runs": { runs: [
          { dataset_name: "tushare:stock_basic", status: "SUCCEEDED" },
        ] },
        "/admin/normalization-runs": { runs: [
          { dataset_name: "stock_basic", status: "PARTIAL_SUCCESS" },
        ] },
        "/admin/projection/status": { outbox: { pending: 0 } },
        "/admin/projection/reconciliation": { business_edges: { total: 340 } },
        "/admin/data-freshness": { latest_claim_at: "2026-09-30T00:00:00Z" },
        "/admin/review-tasks": { review_tasks: [
          { id: "r1", status: "OPEN" },
        ], count: 1 },
      };
      for (const [prefix, payload] of Object.entries(payloads)) {
        if (path.startsWith(prefix)) {
          return { ok: true, status: 200, headers: new Headers(),
                   json: async () => payload } as Response;
        }
      }
      throw new TypeError(`unexpected ${path}`);
    }));
    renderPage(<OpsPage />);

    expect(await screen.findByText("核心组件（readyz）")).toBeInTheDocument();
    expect(await screen.findByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("tushare")).toBeInTheDocument();
    expect(screen.getByText("未启用")).toBeInTheDocument();  // tushare 能力
    expect(await screen.findByText("TUSHARE · stock_basic")).toBeInTheDocument();
    expect(await screen.findByText("tushare:stock_basic")).toBeInTheDocument();
    expect(await screen.findByText("PARTIAL_SUCCESS")).toBeInTheDocument();
    expect(screen.getByText("投影水位与对账")).toBeInTheDocument();
    expect(await screen.findByText(/340/)).toBeInTheDocument(); // 对账边数
    expect(await screen.findByText(/待审任务 1 条/)).toBeInTheDocument();
  });

  it("数据不可达 → 错误态可见（不隐藏）+ 可重试", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    renderPage(<OpsPage />);
    // 至少一个面板的错误态出现
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });
  });
});
