import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// API 同源反代：/api、/admin、/healthz、/readyz → FastAPI（浏览器永不直连数据库/外部源）
const apiProxyTarget = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";
const apiProxy = Object.fromEntries(
  ["/api", "/admin", "/healthz", "/readyz"].map((path) => [
    path,
    { target: apiProxyTarget, changeOrigin: true },
  ]),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: apiProxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
