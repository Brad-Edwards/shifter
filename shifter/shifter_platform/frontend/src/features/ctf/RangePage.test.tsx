import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { axe } from "vitest-axe";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiDownload: vi.fn(), apiFetch: vi.fn() }));

import { apiDownload, apiFetch } from "@/api/client";

import { RangePage } from "./RangePage";

const mockApi = vi.mocked(apiFetch);
const mockDownload = vi.mocked(apiDownload);

beforeEach(() => {
  mockApi.mockReset();
  mockDownload.mockReset();
});

describe("RangePage", () => {
  it("renders a ready range with an access action", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "ready",
      range_instance_id: 5,
      vpn_profile_available: true,
    });
    renderRoute(<RangePage />);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Range instance #5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Access range" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download VPN profile" })).toBeInTheDocument();
  });

  it("hides the access action until the range is ready", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "provisioning",
      range_instance_id: null,
      vpn_profile_available: false,
    });
    renderRoute(<RangePage />);
    expect(await screen.findByText("Provisioning")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Access range" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download VPN profile" })).not.toBeInTheDocument();
    expect(screen.getByText(/becomes available once your range is ready/)).toBeInTheDocument();
  });

  it("hides the VPN download when a ready range has no profile", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "ready",
      range_instance_id: 5,
      vpn_profile_available: false,
    });
    renderRoute(<RangePage />);

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Access range" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download VPN profile" })).not.toBeInTheDocument();
  });

  it("has no axe violations when loaded", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "ready",
      range_instance_id: 5,
      vpn_profile_available: true,
    });
    const { container } = renderRoute(<RangePage />);
    await screen.findByText("Ready");
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });

  it("downloads the profile without retaining it in query state", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "ready",
      range_instance_id: 5,
      vpn_profile_available: true,
    });
    mockDownload.mockResolvedValue(new Blob(["client\n"], { type: "application/x-openvpn-profile" }));
    const createObjectURL = vi.fn().mockReturnValue("blob:ctf-profile");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    renderRoute(<RangePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Download VPN profile" }));

    await waitFor(() => expect(mockDownload).toHaveBeenCalledTimes(1));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:ctf-profile");
    click.mockRestore();
  });

  it("surfaces a VPN download failure", async () => {
    mockApi.mockResolvedValue({
      participant_id: "p1",
      status: "ready",
      range_instance_id: 5,
      vpn_profile_available: true,
    });
    mockDownload.mockRejectedValue(new Error("download failed"));

    renderRoute(<RangePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Download VPN profile" }));

    expect(await screen.findByText("Could not download the VPN profile.")).toBeInTheDocument();
  });
});
