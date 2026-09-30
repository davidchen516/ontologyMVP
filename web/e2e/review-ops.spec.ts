import { expect, test } from "@playwright/test";

/**
 * issue #33 真实浏览器流程：审核工作台 + 运维页。
 * API（:8111，review_write=OK，key=e2e-reviewer-key-1）+ vite（:4173）。
 */
const SHOTS = "screenshots";
const KEY = process.env["E2E_REVIEWER_KEY"] ?? "e2e-reviewer-key-1";

test("#33 审核工作台：登录 → 队列 → 决定须理由 → 提交成功", async ({ page }) => {
  await page.goto("/review");
  // 能力可用 → 登录入口渲染
  await expect(page.getByRole("heading", { name: "Claim 审核" })).toBeVisible();
  await page.getByLabel("Reviewer Key").waitFor({ timeout: 10_000 });

  await page.getByLabel("Reviewer Key").fill(KEY);
  await page.getByRole("button", { name: "进入审核工作台" }).click();

  // 队列加载（快照有 83 条 NEEDS_REVIEW → OPEN 任务）
  const firstAccept = page.getByRole("button", { name: "接受" }).first();
  await expect(firstAccept).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${SHOTS}/review-queue.png`, fullPage: true });

  // 打开决定对话框——理由必填
  await firstAccept.click();
  await expect(page.getByRole("dialog", { name: "审核决定" })).toBeVisible();
  const submit = page.getByRole("button", { name: "确认提交" });
  await expect(submit).toBeDisabled(); // 理由为空
  await page.getByLabel("决定理由").fill("E2E：证据充分，接受");
  await page.screenshot({ path: `${SHOTS}/review-decision-dialog.png`, fullPage: true });
  await submit.click();

  // 成功 → 对话框关闭 + 队列刷新（任务减少或重新加载）
  await expect(page.getByRole("dialog", { name: "审核决定" })).toBeHidden({
    timeout: 10_000,
  });
  await page.screenshot({ path: `${SHOTS}/review-after-decide.png`, fullPage: true });
});

test("#33 运维页：能力矩阵 + 运行 + 投影对账 + 新鲜度", async ({ page }) => {
  await page.goto("/ops");
  await expect(
    page.getByRole("heading", { name: "运维视图" }),
  ).toBeVisible();

  await expect(page.getByText("核心组件（readyz）")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("可选能力（readyz）")).toBeVisible();
  // review_write 能力 = OK（真实）
  await expect(page.getByText("review_write")).toBeVisible();
  await expect(page.getByRole("heading", { name: "投影水位与对账" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数据新鲜度" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/ops-page.png`, fullPage: true });
});

test("#33 审核写未授权：错误 key → 403 分类可见", async ({ page }) => {
  await page.goto("/review");
  await page.getByLabel("Reviewer Key").waitFor({ timeout: 10_000 });
  await page.getByLabel("Reviewer Key").fill("wrong-key");
  await page.getByRole("button", { name: "进入审核工作台" }).click();

  // 队列 401/403 → 分类错误态 + 可重试
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${SHOTS}/review-403.png`, fullPage: true });
});
