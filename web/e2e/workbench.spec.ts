import { expect, test } from "@playwright/test";

/**
 * issue #31 真实浏览器流程：固定 MVP 快照上的研究主路径。
 * 快照：30 家辛示公司（12 家 MASS_PROD 量产+证据+3FY 全正）。
 * 前置：API（:8111，快照库 mvp_v02）+ vite dev 代理（:4173）。
 */
const SHOTS = "screenshots";

test("#31 主路径：查询 → 结果分区 → 证据展开 → 公司详情", async ({ page }) => {
  await page.goto("/workbench/query");
  await expect(page.getByRole("heading", { name: "查询工作台" })).toBeVisible();

  // 首个黄金筛选：MASS_PRODUCTION × 经营现金流 × 合计为正（默认 3FY）
  await page.getByLabel("财务指标").selectOption("NET_CF_OPERATING");
  await page.getByRole("button", { name: "执行筛选" }).click();

  // 结果区：辛示量产公司出现（快照 12 家 MASS_PROD + FIN_NEGATIVE 2 + RESTATE）
  await expect(
    page.getByRole("button", { name: /辛示机器人01号/ }),
  ).toBeVisible({ timeout: 10_000 });

  // 分区可见：状态（SUCCEEDED 有图 / DEGRADED 无图——两种合法终态）/
  // 口径 / trace_id
  await expect(
    page.getByText("SUCCEEDED", { exact: true })
      .or(page.getByText("DEGRADED", { exact: true })),
  ).toBeVisible();
  await expect(page.getByText("LAST_3_FY_TOTAL_POSITIVE")).toBeVisible();
  await expect(page.getByText(/trace_id：/)).toBeVisible();

  // 展开证据包：页码原文 + 推理路径 + 财务明细
  await page.getByRole("button", { name: /辛示机器人01号/ }).click();
  await expect(page.getByText(/第\d+页/).first()).toBeVisible();
  await expect(page.getByText(/已实现量产/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "推理路径" })).toBeVisible();
  await page.screenshot({
    path: `${SHOTS}/workbench-query-grounded.png`,
    fullPage: true,
  });

  // 公司列表 → 详情（深链接契约）
  await page.goto("/workbench/companies");
  await expect(page.getByText(/共 \d+ 家主体/)).toBeVisible();
  await page.getByRole("link", { name: /辛示机器人01号/ }).first().click();
  await expect(
    page.getByRole("heading", { name: "辛示机器人01号" }),
  ).toBeVisible();
  await expect(page.getByText("300101.SZ")).toBeVisible();
  await expect(page.getByText(/Claim（\d+/)).toBeVisible();
  await page.screenshot({
    path: `${SHOTS}/company-detail.png`,
    fullPage: true,
  });

  // 刷新（深链接可复现）
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "辛示机器人01号" }),
  ).toBeVisible();
});

test("#31 首页真实统计与快捷入口", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("系统概览")).toBeVisible();

  // 真实统计卡（快照：30 公司/400 accepted）
  await expect(page.getByText("公司主体")).toBeVisible();
  await expect(page.getByText("已接受 Claim")).toBeVisible();
  const companiesStat = page.getByText("公司主体").locator("..")
    .getByRole("paragraph").last();
  const value = await companiesStat.textContent();
  expect(Number(value)).toBeGreaterThanOrEqual(30);

  // 快照说明（不伪装真实数据）
  await expect(page.getByText(/合成快照/)).toBeVisible();

  // 快捷入口 → 查询工作台
  await page.getByRole("link", { name: "打开查询工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "查询工作台" }),
  ).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/home-with-stats.png`, fullPage: true });
});

test("#31 空结果：显示条件与排除原因，无虚构推荐", async ({ page }) => {
  await page.goto("/workbench/query");
  // 选择无任何公司命中的阶段组合（TECHNOLOGY_RESERVE 快照中无）
  await page.getByLabel("业务阶段").selectOption("TECHNOLOGY_RESERVE");
  await page.getByRole("button", { name: "执行筛选" }).click();

  await expect(page.getByText("无命中结果")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText(/筛选条件可能过严/)).toBeVisible();
  // 无虚构推荐列表
  await expect(page.getByRole("link", { name: /辛示机器人/ })).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/workbench-empty.png`, fullPage: true });
});

test("#31 422 就地报错：注入文本被受控拒绝", async ({ page }) => {
  await page.goto("/workbench/query");
  await page.getByLabel(/概念/).fill("'; DROP TABLE fact.claim; --");
  await page.getByRole("button", { name: "执行筛选" }).click();

  await expect(page.getByRole("alert")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("请求未通过校验")).toBeVisible();
  await expect(page.getByText(/unsafe|DROP/i).first()).toBeVisible();
  await expect(page.getByText(/trace_id：/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/workbench-422.png`, fullPage: true });
});

test("#31 公司搜索与空态", async ({ page }) => {
  await page.goto("/workbench/companies");
  await expect(page.getByText(/共 \d+ 家主体/)).toBeVisible();

  // 搜索命中
  await page.getByLabel("搜索公司名称").fill("01号");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByRole("link", { name: /辛示机器人01号/ })).toBeVisible();

  // 搜索无命中 → 空态
  await page.getByLabel("搜索公司名称").fill("不存在的公司");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByText(/未找到匹配/)).toBeVisible();
});
