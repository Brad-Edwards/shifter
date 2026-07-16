import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { axe } from "vitest-axe";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/api/client";

import { RangePage } from "./RangePage";

const mockApi = vi.mocked(apiFetch);

beforeEach(() => mockApi.mockReset());

describe("RangePage", () => {
  it("renders a ready range with an access action", async () => {
    mockApi.mockResolvedValue({ participant_id: "p1", status: "ready", range_instance_id: 5 });
    renderRoute(<RangePage />);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Range instance #5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Access range" })).toBeInTheDocument();
  });

  it("hides the access action until the range is ready", async () => {
    mockApi.mockResolvedValue({ participant_id: "p1", status: "provisioning", range_instance_id: null });
    renderRoute(<RangePage />);
    expect(await screen.findByText("Provisioning")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Access range" })).not.toBeInTheDocument();
    expect(screen.getByText(/becomes available once your range is ready/)).toBeInTheDocument();
  });

  it("has no axe violations when loaded", async () => {
    mockApi.mockResolvedValue({ participant_id: "p1", status: "ready", range_instance_id: 5 });
    const { container } = renderRoute(<RangePage />);
    await screen.findByText("Ready");
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
