import { createBrowserRouter, RouterProvider } from "react-router";
import { AppShell } from "./components/shell/AppShell";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { HomePage } from "./routes/HomePage";
import { QueryWorkbench } from "./routes/QueryWorkbench";
import { CompaniesPage } from "./routes/CompaniesPage";
import { CompanyDetailPage } from "./routes/CompanyDetailPage";
import { GraphBrowser } from "./routes/GraphBrowser";
import { EvidenceBrowser } from "./routes/EvidenceBrowser";
import { ReviewWorkbench } from "./routes/ReviewWorkbench";
import { OpsPage } from "./routes/OpsPage";
import { NotFoundPage } from "./routes/NotFoundPage";

/**
 * 路由表（#30~#33 里程碑）：首页/查询/公司/图谱/证据/审核/运维全部实装。
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
      { path: "/graph", element: <GraphBrowser /> },
      { path: "/evidence", element: <EvidenceBrowser /> },
      { path: "/review", element: <ReviewWorkbench /> },
      { path: "/ops", element: <OpsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
