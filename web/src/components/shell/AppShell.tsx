import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Menu, X } from "lucide-react";
import { api } from "../../lib/api/endpoints";
import { CURRENT_MILESTONE, NAV_ITEMS, navAvailability } from "../../lib/capabilities";
import { ThemeToggle } from "./ThemeToggle";
import { Breadcrumbs } from "./Breadcrumbs";

const SIDEBAR_OPEN_KEY = "sidebar.open";

interface SidebarState {
  open: boolean;
  toggle: () => void;
  mobileOpen: boolean;
  setMobileOpen: (value: boolean) => void;
}

function useSidebar(): SidebarState {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_OPEN_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const [mobileOpen, setMobileOpenState] = useState(false);
  const toggle = () =>
    setOpen((value) => {
      try {
        localStorage.setItem(SIDEBAR_OPEN_KEY, String(!value));
      } catch {
        /* 隐私模式：不持久化 */
      }
      return !value;
    });
  return {
    open,
    toggle,
    mobileOpen,
    setMobileOpen: setMobileOpenState,
  };
}

/** 系统状态点：/readyz 总体状态的颜色映射（不只靠颜色——附带 aria-label） */
function SystemStatusDot() {
  const { data } = useQuery({
    queryKey: ["readyz"],
    queryFn: api.readyz,
    refetchInterval: 30_000,
  });
  const status = data?.status ?? null;
  const color =
    status === "OK"
      ? "bg-success"
      : status === "DEGRADED"
        ? "bg-warning"
        : status === "FAIL"
          ? "bg-danger"
          : "bg-fg-muted";
  const label = status === null ? "系统状态：加载中" : `系统状态：${status}`;
  return (
    <span className="inline-flex items-center gap-2 text-xs text-fg-muted">
      <span aria-hidden className={`inline-block size-2 rounded-full ${color}`} />
      <span>{label}</span>
    </span>
  );
}

export function AppShell() {
  const { open, toggle, mobileOpen, setMobileOpen } = useSidebar();
  const location = useLocation();
  const { data: readiness } = useQuery({
    queryKey: ["readyz"],
    queryFn: api.readyz,
    refetchInterval: 30_000,
  });

  const setMobileOpenRef = useRef(setMobileOpen);
  setMobileOpenRef.current = setMobileOpen;
  // 路由切换关闭移动端抽屉
  useEffect(() => {
    setMobileOpenRef.current(false);
  }, [location.pathname]);

  const sidebar = (
    <nav aria-label="主导航" className="flex flex-col gap-1 p-3">
      {NAV_ITEMS.map((item) => {
        const { enabled, reason } = navAvailability(item, readiness ?? null);
        if (enabled) {
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-sm font-medium ${
                  isActive
                    ? "bg-primary-muted text-primary"
                    : "text-fg hover:bg-surface-muted"
                }`
              }
            >
              {item.label}
            </NavLink>
          );
        }
        return (
          <span
            key={item.to}
            aria-disabled="true"
            title={reason ?? undefined}
            className="flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium text-fg-muted"
          >
            <span>{item.label}</span>
            <span className="text-xs">未启用</span>
          </span>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <button
          type="button"
          aria-label={mobileOpen ? "关闭导航" : "打开导航"}
          aria-expanded={mobileOpen}
          className="rounded-md p-2 hover:bg-surface-muted md:hidden"
          onClick={() => setMobileOpen(!mobileOpen)}
        >
          {mobileOpen ? <Menu aria-hidden className="size-5" /> : <Menu aria-hidden className="size-5" />}
        </button>
        <NavLink to="/" className="text-sm font-semibold tracking-tight">
          Stock Ontology MVP
        </NavLink>
        <span className="hidden text-xs text-fg-muted md:inline">
          研究工作台 · {CURRENT_MILESTONE}
        </span>
        <div className="ml-auto flex items-center gap-3">
          <SystemStatusDot />
          <ThemeToggle />
        </div>
      </header>

      <div className="flex flex-1">
        {/* 桌面侧栏（可折叠） */}
        <aside
          className={`hidden border-r border-border bg-surface transition-all md:block ${
            open ? "w-56" : "w-14"
          }`}
        >
          <div className="flex justify-end px-2 pt-2">
            <button
              type="button"
              aria-label={open ? "折叠侧栏" : "展开侧栏"}
              className="hidden rounded-md p-2 hover:bg-surface-muted md:block"
              onClick={toggle}
            >
              {open ? <X aria-hidden className="size-4" /> : <Menu aria-hidden className="size-4" />}
            </button>
          </div>
          {open ? sidebar : null}
        </aside>

        {/* 移动端抽屉 */}
        {mobileOpen ? (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true">
            <button
              type="button"
              aria-label="关闭导航"
              className="absolute inset-0 bg-black/40"
              onClick={() => setMobileOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 w-64 bg-surface shadow-lg">
              {sidebar}
            </div>
          </div>
        ) : null}

        <main className="flex-1 overflow-x-hidden p-4 md:p-6">
          <Breadcrumbs />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
