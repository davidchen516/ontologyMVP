import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ErrorBoundary } from "../app/ErrorBoundary";
import { useState } from "react";

function BoomButton(): React.JSX.Element {
  const [boom, setBoom] = useState(false);
  if (boom) throw new Error("render explosion");
  return (
    <button type="button" onClick={() => setBoom(true)}>
      trigger
    </button>
  );
}

describe("ErrorBoundary（路由级）", () => {
  it("渲染异常不白屏：显示受控错误页 + 重新加载出口", async () => {
    // 压掉 React 的预期错误日志
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary>
        <BoomButton />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByText("trigger"));
    expect(screen.getByText("页面渲染出错")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
    vi.mocked(console.error).mockRestore();
  });
});
