import { Link } from "react-router";

/**
 * #31~#33 里程碑占位页：深链接直达时给出交付计划说明，
 * 绝不放置可交互但无效的控件（Epic #29 非目标）。
 */
export function PlaceholderPage({ milestone, title }: { milestone: string; title: string }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h1 className="text-lg font-semibold">{title}</h1>
      <div className="rounded-card border border-dashed border-border bg-surface p-8">
        <p className="text-sm font-medium">此工作台将于 {milestone} 交付</p>
        <p className="mt-1 max-w-xl text-sm text-fg-muted">
          当前里程碑（#30）交付应用壳、设计系统与能力感知导航。
          该页面的功能随后续里程碑实装——请使用侧栏中已启用的入口。
        </p>
        <Link to="/" className="mt-3 inline-block text-sm text-primary hover:underline">
          返回首页
        </Link>
      </div>
    </section>
  );
}
