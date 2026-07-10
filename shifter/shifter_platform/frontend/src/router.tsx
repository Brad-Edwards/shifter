import { createBrowserRouter } from "react-router-dom";

import { RootLayout, type RouteHandle } from "@/app/RootLayout";
import { NotFoundPage } from "@/components/not-found";
import { HomePage } from "@/features/home/HomePage";
import { RiskDetailPage } from "@/features/risk-register/RiskDetailPage";
import { RiskFormPage } from "@/features/risk-register/RiskFormPage";
import { RiskListPage } from "@/features/risk-register/RiskListPage";

// One platform router at the site root (#1369). The Django host serves the
// shell for the SPA-owned page paths (root and /risk-register/*), so deep links
// and refresh resolve to this client router. Risk Register is rehomed here as a
// child of the platform router; its advisory access gate rides the route handle.
const riskRegisterHandle: RouteHandle = { permissionPolicy: "risk_register_access" };

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
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/" },
);
