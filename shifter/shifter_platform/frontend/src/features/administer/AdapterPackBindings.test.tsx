import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import { apiFetch } from "@/api/client";
import type { Adapter } from "@/api/adapters";
import { ApiError } from "@/api/errors";
import { renderRoute } from "@/test/utils";

import { AdapterPackBindings } from "./AdapterPackBindings";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));
const mockApi = vi.mocked(apiFetch);
const adapter: Adapter = {
  id: "installed-1", organization_uuid: "org-1", state: "ready", manifest_digest: "digest", failure_code: "",
  has_registry_credentials: false,
  manifest: { plugin_id: "example.adapter", version: "2", protocol: "shifter.runtime-plugin/v1",
    worker_image: "registry.example.test/image@sha256:digest", capabilities: ["guest.configure", "guest.verify"],
    required_bindings: ["server"], required_parameters: ["mode"], model_bindings: { participant: "server" } },
};
const digest = `sha256:${"a".repeat(64)}`;
const pack = { id: "example", name: "Example pack", pack_digest: digest, binding: null };
const detail = { ...pack, targets: [{ address: "node.web", os_family: "linux" }] };
const base = "/cms/organizations/org-1/plugin-packs/";

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => url.includes("?page=")
    ? { count: 1, next: null, previous: null, results: [pack] } : detail);
});

async function fillAssignment() {
  fireEvent.click(await screen.findByRole("button", { name: "Configure Example pack" }));
  fireEvent.change(await screen.findByLabelText("Installed adapter version"), { target: { value: adapter.id } });
  fireEvent.change(screen.getByLabelText("Guest for server"), { target: { value: "node.web" } });
  fireEvent.change(screen.getByLabelText("mode"), { target: { value: "training" } });
}

describe("pack adapter assignments", () => {
  it("provides accessible guest selectors and confirms the exact pack version before saving", async () => {
    const { container } = renderRoute(<AdapterPackBindings organization="org-1" adapters={[adapter]} />);
    await fillAssignment();
    expect(screen.getByText(/Model access for participant will be delivered to node.web/)).toBeInTheDocument();
    const accessibility = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(accessibility.violations).toEqual([]);
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Review assignment" }));
    fireEvent.click(screen.getByRole("button", { name: "Save assignment" }));
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${base}example/`, { method: "POST", body: {
      installation_id: adapter.id, pack_digest: digest, enabled: true,
      bindings: { targets: { server: "node.web" }, parameters: { mode: "training" } },
    } }));
    expect(await screen.findByText("Assignment saved. Existing ranges keep their original adapter.")).toBeInTheDocument();
  });

  it("keeps a rejected stale assignment open and shows the server's recovery message", async () => {
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "POST") throw new ApiError(400, { code: "plugin_invalid", message: "The pack changed; reload its current version before saving" });
      return url.includes("?page=") ? { count: 1, next: null, previous: null, results: [pack] } : detail;
    });
    renderRoute(<AdapterPackBindings organization="org-1" adapters={[adapter]} />);
    await fillAssignment();
    fireEvent.click(screen.getByRole("button", { name: "Review assignment" }));
    fireEvent.click(screen.getByRole("button", { name: "Save assignment" }));
    expect(await screen.findByText("The pack changed; reload its current version before saving")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByText("Assignment saved. Existing ranges keep their original adapter.")).not.toBeInTheDocument();
  });

  it("blocks assignment when the chosen adapter has not passed compatibility checks", async () => {
    renderRoute(<AdapterPackBindings organization="org-1" adapters={[{ ...adapter, state: "checking" }]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Configure Example pack" }));
    await screen.findByLabelText("Installed adapter version");
    expect(screen.getByRole("option", { name: "example.adapter · 2 (checking)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review assignment" })).toBeDisabled();
  });
});
