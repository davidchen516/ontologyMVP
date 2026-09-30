import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

/**
 * issue #34 WCAG 2.2 AA 自动无障碍检查：
 * - 全部关键路由跑 axe-core（严重/中等违规即失败）；
 * - 键盘可达性人工清单项以自动可测子集落进 e2e（焦点/对比度/标签）；
 * - 200% 缩放下无横向溢出（关闭条件 3）。
 */
const ROUTES = [
  "/",
  "/workbench/query",
  "/workbench/companies",
  "/graph",
  "/evidence",
  "/review",
  "/ops",
];

for (const route of ROUTES) {
  test(`axe WCAG 2.2 AA：${route}`, async ({ page }) => {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const violations = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate",
    );
    // 违规详情输出（失败时可定位）
    if (violations.length > 0) {
      console.error(
        `${route}: ${violations.length} violations:`,
        violations.map((v) => `${v.id}(${v.impact}): ${v.help}`).join("; "),
      );
    }
    expect(violations).toEqual([]);
  });
}

test("200% 缩放下关键路径无横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 512, height: 960 }); // 1024×200%≈512
  await page.goto("/workbench/query");
  await expect(
    page.getByRole("heading", { name: "查询工作台" }),
  ).toBeVisible({ timeout: 10_000 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  await page.goto("/workbench/companies");
  await expect(page.getByRole("heading", { name: "公司列表" })).toBeVisible();
  const overflow2 = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow2).toBeLessThanOrEqual(0);
});

test("reduced motion：动画禁用后关键交互可用", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "系统概览" })).toBeVisible({
    timeout: 10_000,
  });
  // reduced-motion 下统计面板仍渲染（数据可达，无动画依赖）
  await expect(page.getByText("公司主体")).toBeVisible();
});
