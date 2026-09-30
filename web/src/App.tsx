import { createBrowserRouter, RouterProvider } from "react-router";
import { AppShell } from "./components/shell/AppShell";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { HomePage } from "./routes/HomePage";
import { QueryWorkbench } from "./routes/QueryWorkbench";
import { CompaniesPage } from "./routes/CompaniesPage";
import { CompanyDetailPage } from "./routes/CompanyDetailPage";
import { PlaceholderPage } from "./routes/PlaceholderPage";
import { NotFoundPage } from "./routes/NotFoundPage";

/**
 * 路由表（#30 里程碑）：首页实装；#31~#33 页面为占位（能力感知导航
 * 会把它们显示为"未启用"，占位路由仅供深链接直达时的说明页）。
 */
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    errorElement: <ErrorBoundary />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/workbench/query", element: <QueryWorkbench /> },
      { path: "/workbench/companies", element: <CompaniesPage /> },
      {
        path: "/workbench/companies/:companyId",
        element: <CompanyDetailPage />,
      },
      {
        path: "/graph",
        element: <PlaceholderPage milestone="#32" title="图谱浏览器" />,
      },
      {
        path: "/evidence",
        element: <PlaceholderPage milestone="#32" title="证据浏览器" />,
      },
      {
        path: "/review",
        element: <PlaceholderPage milestone="#33" title="Claim 审核" />,
      },
      {
        path: "/ops",
        element: <PlaceholderPage milestone="#33" title="运维视图" />,
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
