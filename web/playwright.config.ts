import { defineConfig, devices } from "@playwright/test";

/**
 * 真实浏览器验收配置（#30 证据：真实渲染截图 / 键盘与断点检查）。
 * 复用 vite preview（生产构建产物），baseURL 由 E2E_BASE_URL 指定。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env["E2E_BASE_URL"] ?? "http://localhost:4173",
    trace: "off",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],
});
