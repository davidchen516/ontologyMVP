import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { router } from "../App";
import { ThemeToggle } from "../components/shell/ThemeToggle";

function renderWithQuery() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const READY_OK = {
  status: "OK",
  components: { postgres: { status: "OK" }, neo4j: { status: "OK" } },
  capabilities: { tushare: { status: "OK" }, llm: { status: "UNAVAILABLE" } },
};

export function mockReadyz(payload: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/readyz") {
        return {
          ok: true,
          status: 200,
          headers: new Headers({ "X-Trace-Id": "t" }),
          json: async () => payload,
        } as Response;
      }
      throw new TypeError("unexpected");
    }),
  );
}

describe("应用壳（#30）", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    document.documentElement.dataset.theme = "light";
  });

  it("Given API 可用，When 打开首页，Then 渲染系统概览与导航", async () => {
    mockReadyz(READY_OK);
    renderWithQuery();
    expect(await screen.findByText("Stock Ontology MVP")).toBeInTheDocument();
    expect(await screen.findByText("系统概览")).toBeInTheDocument();
    // 能力感知导航：#30 里程碑仅首页启用（外层 span 含 "未启用" 标记）
    const queryItem = screen.getByText("查询工作台").parentElement;
    expect(queryItem).toHaveTextContent("未启用");
    expect(screen.getAllByText("首页")[0]).toBeInTheDocument();
  });

  it("Given 未到交付里程碑，When 渲染导航，Then 图谱入口显示未启用+里程碑原因", async () => {
    mockReadyz({
      ...READY_OK,
      components: { postgres: { status: "OK" }, neo4j: { status: "FAIL" } },
    });
    renderWithQuery();
    const graphItem = (await screen.findByText("图谱浏览器")).parentElement;
    expect(graphItem).toHaveTextContent("未启用");
    // 里程碑门禁优先于能力门禁（#32 交付后能力门禁才开始生效）
    expect(graphItem).toHaveAttribute("title", "将于 #32 交付");
  });

  it("Given 未知路由，Then 404 页面", async () => {
    mockReadyz(READY_OK);
    const memoryRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/nonexistent-path"],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("页面不存在")).toBeInTheDocument();
  });

  it("主题切换：aria 状态翻转并持久化", async () => {
    render(<ThemeToggle />);
    const toggle = screen.getByRole("switch", { name: "切换亮色/暗色主题" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

describe("深链接刷新（SPA fallback 由 nginx try_files 提供，路由层验证）", () => {
  it("Given 深链接 /ops，Then 壳层直接加载对应路由", async () => {
    mockReadyz(READY_OK);
    const memoryRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/ops"],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      // PlaceholderPage 正文（区别于侧栏导航项的"运维视图"）
      expect(screen.getByText("此工作台将于 #33 交付")).toBeInTheDocument();
    });
  });
});
