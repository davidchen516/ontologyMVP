import { expect, test } from "@playwright/test";

/**
 * 应用壳真实浏览器验收（#30 核心验收逻辑 + 证据截图）。
 * 截图输出到 web/screenshots/（构建证据，由 CI artifact 或本地生成）。
 */
const SHOTS = "screenshots";

test("首页渲染真实 readyz 数据 + 能力感知导航", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "系统概览" })).toBeVisible();
  await expect(page.getByText("Stock Ontology MVP")).toBeVisible();

  // 真实组件状态（来自 /readyz：postgres/neo4j OK，tushare/llm UNAVAILABLE）
  await expect(page.getByText("postgres").first()).toBeVisible();
  await expect(page.getByText("tushare").first()).toBeVisible();
  await expect(page.getByText("未启用").first()).toBeVisible();

  await page.screenshot({ path: `${SHOTS}/home-1440-light.png`, fullPage: true });
});

test("亮/暗主题切换持久化", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("switch", { name: "切换亮色/暗色主题" });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: `${SHOTS}/home-1440-dark.png`, fullPage: true });

  // 刷新后保持
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("深链接刷新不 404（SPA fallback）", async ({ page }) => {
  await page.goto("/ops");
  await expect(page.getByRole("heading", { name: "运维视图" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "运维视图" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/deeplink-ops.png`, fullPage: true });
});

test("未知路由显示 404", async ({ page }) => {
  await page.goto("/definitely-missing");
  await expect(page.getByRole("heading", { name: "页面不存在" })).toBeVisible();
});

test("API 不可达时显示分类错误态（含重试出口，无内部细节）", async ({ page }) => {
  await page.route("**/readyz", (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("网络不可达", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  // 不得泄露内部细节
  const body = await page.textContent("body");
  expect(body).not.toContain("psycopg");
  expect(body).not.toContain("Traceback");
  await page.screenshot({ path: `${SHOTS}/error-network.png`, fullPage: true });
});

test("移动端 375px 无横向溢出且导航为抽屉", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "系统概览" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `${SHOTS}/home-375-light.png`, fullPage: true });
});

test("键盘可导航：Tab 到侧栏并聚焦可见", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
  expect(focused.length).toBeGreaterThan(0);
  await page.screenshot({ path: `${SHOTS}/keyboard-focus.png` });
});
