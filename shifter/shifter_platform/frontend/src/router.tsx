import { createBrowserRouter } from "react-router-dom";

import { RootLayout, type RouteHandle } from "@/app/RootLayout";
import { NotFoundPage } from "@/components/not-found";
import { HomePage } from "@/features/home/HomePage";
import { AgentsPage } from "@/features/mission-control/AgentsPage";
import { CredentialsPage } from "@/features/mission-control/CredentialsPage";
import { NgfwDetailPage } from "@/features/mission-control/NgfwDetailPage";
import { NgfwListPage } from "@/features/mission-control/NgfwListPage";
import { NgfwWizardPage } from "@/features/mission-control/NgfwWizardPage";
import { RangeDashboardPage } from "@/features/mission-control/RangeDashboardPage";
import { RangeDetailPage } from "@/features/mission-control/RangeDetailPage";
import { RangeHistoryPage } from "@/features/mission-control/RangeHistoryPage";
import { RangeLaunchPage } from "@/features/mission-control/RangeLaunchPage";
import { TerminalPage } from "@/features/mission-control/TerminalPage";
import { RiskDetailPage } from "@/features/risk-register/RiskDetailPage";
import { RiskFormPage } from "@/features/risk-register/RiskFormPage";
import { RiskListPage } from "@/features/risk-register/RiskListPage";

// One platform router at the site root (#1369). The Django host serves the
// shell for the SPA-owned page paths (root and /risk-register/*), so deep links
// and refresh resolve to this client router. Risk Register is rehomed here as a
// child of the platform router; its advisory access gate rides the route handle.
const riskRegisterHandle: RouteHandle = { permissionPolicy: "risk_register_access" };
// Mission Control (#1370) is gated the same way the "Operate" nav group is:
// any authenticated principal, same as its legacy Django views.
const missionControlHandle: RouteHandle = { permissionPolicy: "authenticated" };

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <RootLayout />,
      children: [
        { index: true, element: <HomePage /> },
        {
          path: "risk-register",
          handle: riskRegisterHandle,
          children: [
            { index: true, element: <RiskListPage /> },
            { path: "risks/create", element: <RiskFormPage mode="create" /> },
            { path: "risks/:id", element: <RiskDetailPage /> },
            { path: "risks/:id/edit", element: <RiskFormPage mode="edit" /> },
          ],
        },
        {
          // The F1 foundation chunk registered only the dashboard; the
          // live-access chunk added the per-instance terminal page; the
          // range-pages chunk added the range-history list, the launch form,
          // and the per-range detail page; this chunk adds the asset pages
          // (agents, NGFW, credentials — see features/mission-control/routes.ts
          // for the matching path builders). "ngfw/setup" is listed before the
          // "ngfw/:appId" dynamic route for readability; React Router ranks
          // static segments over dynamic ones regardless of declaration order,
          // so this ordering is not load-bearing.
          path: "mission-control",
          handle: missionControlHandle,
          children: [
            { index: true, element: <RangeDashboardPage /> },
            { path: "ranges", element: <RangeHistoryPage /> },
            { path: "launch", element: <RangeLaunchPage /> },
            { path: "ranges/:requestId", element: <RangeDetailPage /> },
            { path: "terminal/:instanceUuid", element: <TerminalPage /> },
            { path: "agents", element: <AgentsPage /> },
            { path: "ngfw", element: <NgfwListPage /> },
            { path: "ngfw/setup", element: <NgfwWizardPage /> },
            { path: "ngfw/:appId", element: <NgfwDetailPage /> },
            { path: "credentials", element: <CredentialsPage /> },
          ],
        },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/" },
);
