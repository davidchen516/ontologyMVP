import { Fragment } from "react";
import { Link, useLocation } from "react-router";

const LABELS: Record<string, string> = {
  "/": "首页",
  "/workbench": "研究工作台",
  "/workbench/query": "查询工作台",
  "/workbench/companies": "公司列表",
  "/graph": "图谱浏览器",
  "/evidence": "证据浏览器",
  "/review": "Claim 审核",
  "/ops": "运维视图",
};

/** 面包屑：按路径段逐级可回跳；首页始终是第一级。 */
export function Breadcrumbs() {
  const location = useLocation();
  if (location.pathname === "/") return null;

  const segments = location.pathname.split("/").filter(Boolean);
  const crumbs = segments.map((segment, index) => ({
    to: `/${segments.slice(0, index + 1).join("/")}`,
    label: LABELS[`/${segments.slice(0, index + 1).join("/")}`] ?? segment,
  }));

  return (
    <nav aria-label="面包屑" className="mb-4 text-xs text-fg-muted">
      <ol className="flex items-center gap-1">
        <li>
          <Link to="/" className="hover:text-fg">
            首页
          </Link>
        </li>
        {crumbs.map((crumb, index) => (
          <Fragment key={crumb.to}>
            <li aria-hidden>/</li>
            <li>
              {index === crumbs.length - 1 ? (
                <span aria-current="page">{crumb.label}</span>
              ) : (
                <Link to={crumb.to} className="hover:text-fg">
                  {crumb.label}
                </Link>
              )}
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  );
}
