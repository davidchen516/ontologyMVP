import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children?: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * 路由级错误边界：渲染异常不白屏，提供重载出口。
 * 数据请求错误由 TanStack Query / ErrorState 处理，不进入这里。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 不上报任何外部服务；控制台仅输出组件栈摘要（不含用户数据）
    console.error("ui_render_error", error.message, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8 text-center">
        <h1 className="text-xl font-semibold">页面渲染出错</h1>
        <p className="max-w-md text-sm text-fg-muted">
          界面发生意外错误。数据与系统不受影响，可尝试重新加载页面。
        </p>
        <button
          type="button"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg"
          onClick={() => this.setState({ error: null })}
        >
          重新加载
        </button>
      </main>
    );
  }
}
