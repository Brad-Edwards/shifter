import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { axe } from "vitest-axe";

vi.mock("@/app/bootstrap-context", () => ({
  useBootstrapContext: () => ({
    principal: { id: 1, username: "root", display_name: "Root", is_authenticated: true, is_staff: true, is_superuser: true },
    permissions: {},
  }),
}));

import { PlatformSettingsPage } from "./PlatformSettingsPage";

describe("PlatformSettingsPage", () => {
  it("presents the cut-over authorities as deployment-managed state", () => {
    render(<PlatformSettingsPage />);
    expect(screen.getByText("Managed by deployment")).toBeInTheDocument();
    expect(screen.getByText(/platform SPA and RAES provisioning path are the current product authorities/)).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<PlatformSettingsPage />);
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
