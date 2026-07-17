import { createBrowserRouter } from "react-router-dom";

import { RootLayout, type RouteHandle } from "@/app/RootLayout";
import { NotFoundPage } from "@/components/not-found";
import { AcesImageRegistryPage } from "@/features/aces-image-registry/AcesImageRegistryPage";
import { CostPage } from "@/features/administer/CostPage";
import { PlatformSettingsPage } from "@/features/administer/PlatformSettingsPage";
import { UserDetailPage } from "@/features/administer/UserDetailPage";
import { UsersListPage } from "@/features/administer/UsersListPage";
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
import { ScenarioDetailPage } from "@/features/scenario-editor/ScenarioDetailPage";
import { ScenarioFormPage } from "@/features/scenario-editor/ScenarioFormPage";
import { ScenarioListPage } from "@/features/scenario-editor/ScenarioListPage";
import { ScenarioYamlPage } from "@/features/scenario-editor/ScenarioYamlPage";

// One platform router at the site root (#1369). The Django host serves the
// shell for the SPA-owned page paths (root and /risk-register/*), so deep links
// and refresh resolve to this client router. Risk Register is rehomed here as a
// child of the platform router; its advisory access gate rides the route handle.
const riskRegisterHandle: RouteHandle = { permissionPolicy: "risk_register_access" };
// Mission Control (#1370) is gated the same way the "Operate" nav group is:
// any authenticated principal, same as its legacy Django views.
const missionControlHandle: RouteHandle = { permissionPolicy: "authenticated" };
// Scenario Editor (#1371) is gated on CMS-authoring access, the same advisory
// policy the existing "Author" nav group / legacy threat-research views use.
const scenarioEditorHandle: RouteHandle = { permissionPolicy: "threat_research" };
// ACES image registry (#1566) shares the "Author" CMS-authoring gate; the API
// additionally 404s unless SHIFTER_ACES_NATIVE_PROVISIONING is on.
const acesImageRegistryHandle: RouteHandle = { permissionPolicy: "threat_research" };
// Administer workspace (#1373) is gated on staff, the same advisory policy the
// "Administer" nav group and the /api/v1/administer/ endpoints enforce. The Django
// host additionally serves these pages only when ADMINISTER_SPA_ENABLED is on.
const administerHandle: RouteHandle = { permissionPolicy: "staff" };

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
        {
          // Scenario Editor (#1371) rehomed under the unified client router.
          // Its legacy Django counterpart lives at the same /scenario-editor/
          // page paths (see features/scenario-editor/routes.ts); static
          // segments (create, create/yaml) outrank the ":scenarioId" dynamic
          // route regardless of declaration order.
          path: "scenario-editor",
          handle: scenarioEditorHandle,
          children: [
            { index: true, element: <ScenarioListPage /> },
            { path: "create", element: <ScenarioFormPage mode="create" /> },
            { path: "create/yaml", element: <ScenarioYamlPage mode="create" /> },
            { path: ":scenarioId", element: <ScenarioDetailPage /> },
            { path: ":scenarioId/edit", element: <ScenarioFormPage mode="edit" /> },
            { path: ":scenarioId/editor", element: <ScenarioYamlPage mode="edit" /> },
          ],
        },
        {
          // ACES image registry (#1566): greenfield SPA-only surface. The Django
          // host serves the shell for /aces-image-registry/* GET paths only when
          // PLATFORM_SPA_ENABLED and SHIFTER_ACES_NATIVE_PROVISIONING are on.
          path: "aces-image-registry",
          handle: acesImageRegistryHandle,
          children: [{ index: true, element: <AcesImageRegistryPage /> }],
        },
        {
          // Administer workspace (#1373): greenfield SPA surface. The Django host
          // serves the shell for /administer/* GET paths only when
          // PLATFORM_SPA_ENABLED and ADMINISTER_SPA_ENABLED are on; Django admin
          // stays at /admin/ and is never captured here. Users is the index;
          // static segments (cost, settings) outrank the users/:id dynamic route.
          path: "administer",
          handle: administerHandle,
          children: [
            { index: true, element: <UsersListPage /> },
            { path: "users/:id", element: <UserDetailPage /> },
            { path: "cost", element: <CostPage /> },
            { path: "settings", element: <PlatformSettingsPage /> },
          ],
        },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/" },
);
