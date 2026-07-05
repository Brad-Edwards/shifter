import { Outlet, useLocation } from "react-router-dom";

import { AppShell, EmptyState, type NavGroup } from "@/ds";

import { BootstrapProvider, useBootstrapContext } from "./bootstrap-context";

const NAV_GROUPS: NavGroup[] = [{ label: "Govern", items: [{ label: "Risks", to: "/", end: false }] }];

function AccessDenied() {
  // Does not reveal whether any specific risk exists; advisory only. The API
  // remains the authoritative boundary and returns 403 regardless.
  return (
    <EmptyState title="Access denied">
      You do not have access to the Risk Register. Contact an administrator if you believe this is an error.
    </EmptyState>
  );
}

function WorkspaceFrame() {
  const bootstrap = useBootstrapContext();
  const location = useLocation();
  return (
    <AppShell principalName={bootstrap.principal.display_name} groups={NAV_GROUPS} currentPath={location.pathname}>
      {bootstrap.permissions.can_access_risk_register ? <Outlet /> : <AccessDenied />}
    </AppShell>
  );
}

export function RootLayout() {
  return (
    <BootstrapProvider>
      <WorkspaceFrame />
    </BootstrapProvider>
  );
}
