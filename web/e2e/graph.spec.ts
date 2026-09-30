import { expect, test } from "@playwright/test";

/**
 * issue #32 真实浏览器流程：图谱 + 证据追溯。
 * 前置：API（:8111 快照库）+ vite 代理（:4173）。
 * 快照：辛示公司 30 家，含图投影（公司/产品/概念节点与 claim 边）。
 */
const SHOTS = "screenshots";

test("#32 图谱：选择公司 → 子图加载 → 表格视图 → 证据追溯链", async ({ page }) => {
  await page.goto("/graph");
  await expect(page.getByRole("heading", { name: "图谱浏览器" })).toBeVisible();

  // 选择辛示机器人01号（快照真实公司）
  const select = page.getByRole("combobox", { name: "选择公司" });
  await select.waitFor({ timeout: 10_000 });
  await select.selectOption({ label: "辛示机器人01号" });
  await page.getByRole("button", { name: "加载子图" }).click();

  // SUCCEEDED（有图投影）或 DEGRADED（投影滞后）——两种合法态
  await expect(
    page.getByText("SUCCEEDED").or(page.getByText("DEGRADED")),
  ).toBeVisible({ timeout: 10_000 });

  // 切表格视图（键盘/读屏替代视图承载全部关系）
  await page.getByRole("radio", { name: "表格" }).click();
  await expect(
    page.getByRole("table", { name: /图谱关系列表/ }),
  ).toBeVisible();

  // 节点摘要可见（真实图数据）
  await expect(page.getByText(/节点清单/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/graph-table-view.png`, fullPage: true });

  // 边带 claim_id → 证据追溯抽屉（GWT：每条经营边可回 Claim 与证据）
  // 表格视图的"查看证据链"必达（快照有 PRODUCES 边——断言不弱化为可选）
  const evidenceButton = page.getByRole("button", { name: "查看证据链" }).first();
  await expect(evidenceButton).toBeVisible({ timeout: 10_000 });
  await evidenceButton.click();
  await expect(
    page.getByRole("dialog", { name: "证据追溯链" }),
  ).toBeVisible({ timeout: 10_000 });
  // 页码原文 + 来源文档（真实数据）
  await expect(page.getByText(/第 \d+ 页/).first()).toBeVisible();
  await expect(page.getByText(/CNINFO|SIN/i).first()).toBeVisible();
  await page.screenshot({
    path: `${SHOTS}/evidence-drawer.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭" }).click();
});

test("#32 图谱降级：停图后端 → DEGRADED + 原因 + PG 提示", async ({ page }) => {
  // 拦截子图请求模拟图后端断连（后端 executor 抛错会返回 DEGRADED——
  // 此处直接 mock 响应形态验证 UI 渲染路径）
  await page.route("**/api/v1/graph/subgraph**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "DEGRADED",
        reason: "graph backend unreachable: ServiceUnavailable",
        center: null, nodes: [], edges: [],
        hops: 1, max_nodes: 100, truncated: false,
      }),
    }),
  );
  await page.goto("/graph");
  const select = page.getByRole("combobox", { name: "选择公司" });
  await select.waitFor({ timeout: 10_000 });
  await select.selectOption({ label: "辛示机器人01号" });
  await page.getByRole("button", { name: "加载子图" }).click();

  await expect(page.getByText(/图后端不可用（DEGRADED）/)).toBeVisible();
  await expect(page.getByText(/ServiceUnavailable/)).toBeVisible();
  await expect(page.getByText(/PostgreSQL 事实/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/graph-degraded.png`, fullPage: true });
});

test("#32 查询结果证据链展开（图谱抽屉之外的原文定位路径）", async ({ page }) => {
  // 查询工作台黄金结果 → 展开证据包（页码原文/推理路径）——与图谱抽屉
  // 互补的证据定位入口
  await page.goto("/workbench/query");
  await page.getByLabel("财务指标").selectOption("NET_CF_OPERATING");
  await page.getByRole("button", { name: "执行筛选" }).click();
  await expect(
    page.getByRole("button", { name: /辛示机器人01号/ }),
  ).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /辛示机器人01号/ }).click();
  await expect(page.getByRole("heading", { name: "推理路径" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/evidence-from-query.png`, fullPage: true });
});

test("#32 深链接刷新：图谱与证据路由不 404", async ({ page }) => {
  await page.goto("/graph");
  await page.reload();
  await expect(page.getByRole("heading", { name: "图谱浏览器" })).toBeVisible();
  await page.goto("/evidence");
  await page.reload();
  await expect(page.getByRole("heading", { name: "证据浏览器" })).toBeVisible();
});
