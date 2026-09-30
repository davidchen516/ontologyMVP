import { Link } from "react-router";

/** 404：SPA fallback 后唯一会到达此页的情形是真实未知路由。 */
export function NotFoundPage() {
  return (
    <section aria-label="页面不存在" className="space-y-3">
      <h1 className="text-lg font-semibold">页面不存在</h1>
      <p className="text-sm text-fg-muted">
        访问的地址没有对应页面。请从导航进入，或
        <Link to="/" className="ml-1 text-primary hover:underline">
          返回首页
        </Link>
        。
      </p>
    </section>
  );
}
