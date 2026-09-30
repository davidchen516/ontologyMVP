/** 统一加载骨架：布局稳定不跳动（product-ui.md §4）。 */
export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="animate-pulse space-y-3" role="status" aria-label="加载中">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="h-10 rounded-card bg-surface-muted"
        />
      ))}
    </div>
  );
}

/** 空态：说明原因 + 下一步动作，不用占位数字。 */
export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface p-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="mt-1 text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
}
