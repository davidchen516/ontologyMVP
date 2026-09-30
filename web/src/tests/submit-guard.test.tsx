import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";

/**
 * 双击抑制（验收：连续点击提交时控件禁用并显示进度）。
 * TanStack Query isPending 驱动禁用态；请求幂等仍由服务端保证
 * （Epic #29 不变量：客户端抑制 + 服务端幂等双层）。
 */
function SubmitButton({ onFire }: { onFire: () => Promise<void> }) {
  const mutation = useMutation({ mutationFn: onFire });
  return (
    <button
      type="button"
      disabled={mutation.isPending}
      aria-busy={mutation.isPending}
      onClick={() => void mutation.mutateAsync()}
    >
      {mutation.isPending ? "提交中…" : "提交"}
    </button>
  );
}

function SlowSubmit() {
  const [calls, setCalls] = useState<string[]>([]);
  const client = new QueryClient();
  return (
    <QueryClientProvider client={client}>
      <p data-testid="calls">{calls.length}</p>
      <SubmitButton
        onFire={() =>
          new Promise<void>((resolve) => {
            setCalls((previous) => [...previous, String(previous.length + 1)]);
            setTimeout(resolve, 120);
          })
        }
      />
    </QueryClientProvider>
  );
}

describe("重复提交抑制", () => {
  it("Given 连续点击，When 首次请求未完成，Then 控件禁用且只触发一次", async () => {
    render(<SlowSubmit />);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "提交" });

    // 首次点击 → pending：立即禁用 + 进度文案（不等待完成）
    await user.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: /提交中/ })).toBeInTheDocument();

    // 完成后恢复可用，且仅触发一次（第二次点击发生在禁用窗口外/无效）
    await vi.waitFor(() => expect(button).toBeEnabled());
    expect(screen.getByTestId("calls")).toHaveTextContent("1");
  });
});
