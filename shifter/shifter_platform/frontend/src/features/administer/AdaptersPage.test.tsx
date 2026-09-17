import { fireEvent, screen, within, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import userEvent from "@testing-library/user-event";

import { apiFetch } from "@/api/client";
import { ApiError } from "@/api/errors";
import { renderRoute } from "@/test/utils";

import { AdaptersPage } from "./AdaptersPage";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));
const mockApi = vi.mocked(apiFetch);
const organizations = { results: [{ uuid: "org-1", name: "Example tenant" }] };
const manifest = {
  plugin_id: "example.adapter", version: "1.0.0", protocol: "shifter.runtime-plugin/v1",
  worker_image: `registry.example.test/worker@sha256:${"a".repeat(64)}`,
  capabilities: ["guest.configure", "guest.verify"],
};
const adapter = {
  id: "adapter-1", organization_uuid: "org-1", manifest_digest: "digest",
  manifest, state: "ready", has_registry_credentials: false, failure_code: "",
};
const base = "/cms/organizations/org-1/plugins/";
const isOrganizations = (url: string) => url === "/workspaces/organizations/";

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => isOrganizations(url) ? organizations : [adapter]);
});

describe("tenant plugin administration", () => {
  it("does not render executable controls after an authorization failure", async () => {
    mockApi.mockRejectedValue(new ApiError(403, { code: "forbidden", message: "Access denied" }));
    renderRoute(<AdaptersPage />);
    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.queryByLabelText("Plugin installation file")).not.toBeInTheDocument();
    expect(mockApi).not.toHaveBeenCalledWith(expect.stringContaining("/plugins/"), expect.anything());
  });

  it("requires confirmation and preserves a rejected action", async () => {
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "POST") throw new ApiError(400, { code: "invalid", message: "Plugin unavailable" });
      return isOrganizations(url) ? organizations : [adapter];
    });
    renderRoute(<AdaptersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Disable plugin" }));
    expect(await screen.findByText("Plugin unavailable")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(mockApi).toHaveBeenCalledWith(`${base}adapter-1/actions/`, {
      method: "POST", body: { action: "disable", registry_credentials: undefined },
    });
  });

  it("retains retired versions and offers no reactivation", async () => {
    mockApi.mockImplementation(async (url) => isOrganizations(url) ? organizations : [{ ...adapter, state: "retired" }]);
    renderRoute(<AdaptersPage />);
    expect(await screen.findByText("Retained for existing ranges")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retire" })).not.toBeInTheDocument();
  });

  it("rejects an oversized manifest before reading or submitting it", async () => {
    renderRoute(<AdaptersPage />);
    const read = vi.fn();
    fireEvent.change(await screen.findByLabelText("Plugin installation file"), {
      target: { files: [{ size: 65_537, text: read }] },
    });
    expect(await screen.findByText("Choose a plugin manifest no larger than 64 KiB.")).toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Review installation" })).toBeDisabled();
  });

  it("installs the author's package without an operator grant and shows pending compatibility", async () => {
    const user = userEvent.setup();
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "POST") return { ...adapter, state: "checking" };
      return isOrganizations(url) ? organizations : [];
    });
    renderRoute(<AdaptersPage />);
    fireEvent.change(await screen.findByLabelText("Plugin installation file"), {
      target: { files: [{ size: 100, text: async () => JSON.stringify(manifest) }] },
    });
    await screen.findByText("example.adapter · 1.0.0");
    await user.click(screen.getByRole("button", { name: "Review installation" }));
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Install plugin" }));
    expect(await screen.findByText("Installation started. Compatibility checks are running.")).toBeInTheDocument();
    expect(mockApi).toHaveBeenCalledWith(base, { method: "POST", body: { manifest } });
    expect(mockApi.mock.calls.some(([url]) => url.includes("grant"))).toBe(false);
  });

  it("allows failed installation retry with replacement private registry credentials", async () => {
    mockApi.mockImplementation(async (url, options) => options?.method === "POST" ? adapter :
      isOrganizations(url) ? organizations : [{ ...adapter, state: "failed" }]);
    renderRoute(<AdaptersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry installation" }));
    const dialog = within(screen.getByRole("alertdialog"));
    fireEvent.change(dialog.getByLabelText("Registry username"), { target: { value: "reader" } });
    fireEvent.change(dialog.getByLabelText("Registry password or access token"), { target: { value: "test-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Retry plugin" }));
    await screen.findByText("example.adapter");
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${base}adapter-1/actions/`, {
      method: "POST", body: { action: "retry", registry_credentials: { username: "reader", password: "test-token" } },
    }));
  });

  it("has accessible installation and installed-version controls", async () => {
    const { container } = renderRoute(<AdaptersPage />);
    await screen.findByText("example.adapter");
    const result = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
