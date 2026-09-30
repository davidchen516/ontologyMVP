/**
 * 能力感知导航（product-ui.md §3）：后端 /readyz 未声明的能力，
 * 导航显示"未启用"+原因，不放置可点击但无效的按钮（Epic #29 不变量）。
 */
import type { ReadinessReport } from "./api/types";

export interface NavItem {
  to: string;
  label: string;
  /** 交付里程碑（#30~#33）；未到里程碑的入口同样按"未启用"渲染 */
  availableIn: "#30" | "#31" | "#32" | "#33";
  /**
   * 能力门禁：返回 null 表示可用；返回字符串为"未启用"的原因。
   * report 为 null（readyz 尚未加载）时壳层不渲染导航细节。
   */
  gate: (report: ReadinessReport) => string | null;
}

const apiGate = (report: ReadinessReport): string | null =>
  report.components["postgres"]?.status === "OK"
    ? null
    : "PostgreSQL 组件不可用";

const graphGate = (report: ReadinessReport): string | null => {
  const postgres = apiGate(report);
  if (postgres) return postgres;
  return report.components["neo4j"]?.status === "OK"
    ? null
    : "Neo4j 图组件不可用";
};

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "首页", availableIn: "#30", gate: () => null },
  {
    to: "/workbench/query",
    label: "查询工作台",
    availableIn: "#31",
    gate: apiGate,
  },
  {
    to: "/workbench/companies",
    label: "公司列表",
    availableIn: "#31",
    gate: apiGate,
  },
  {
    to: "/graph",
    label: "图谱浏览器",
    availableIn: "#32",
    gate: graphGate,
  },
  {
    to: "/evidence",
    label: "证据浏览器",
    availableIn: "#32",
    gate: apiGate,
  },
  {
    to: "/review",
    label: "Claim 审核",
    availableIn: "#33",
    gate: () => null,
  },
  {
    to: "/ops",
    label: "运维视图",
    availableIn: "#33",
    gate: apiGate,
  },
];

/** 里程碑检查：#30 应用壳 + #31 查询/公司 + #32 图谱/证据已实装；其余按里程碑声明。 */
export const CURRENT_MILESTONE = "#32";

/** 里程碑序比较：availableIn 序 <= 当前序 ⇒ 已交付（启用）。 */
function milestoneNumber(milestone: string): number {
  const match = /#(\d+)/.exec(milestone);
  return match ? Number(match[1]) : 0;
}

export function navAvailability(
  item: NavItem,
  report: ReadinessReport | null,
): { enabled: boolean; reason: string | null } {
  if (milestoneNumber(item.availableIn) > milestoneNumber(CURRENT_MILESTONE)) {
    return { enabled: false, reason: `将于 ${item.availableIn} 交付` };
  }
  if (report === null) {
    return { enabled: false, reason: "系统状态加载中" };
  }
  const reason = item.gate(report);
  return reason === null ? { enabled: true, reason: null } : { enabled: false, reason };
}
