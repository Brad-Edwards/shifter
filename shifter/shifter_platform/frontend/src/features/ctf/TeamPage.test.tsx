import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { axe } from "vitest-axe";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/api/client";

import { TeamPage } from "./TeamPage";

const mockApi = vi.mocked(apiFetch);

beforeEach(() => mockApi.mockReset());

describe("TeamPage", () => {
  it("renders the team and its members", async () => {
    mockApi.mockResolvedValue({
      id: "t1",
      name: "Blue Team",
      members: [
        { id: "m1", name: "Alice" },
        { id: "m2", name: "Bob" },
      ],
    });
    renderRoute(<TeamPage />);
    expect(await screen.findByRole("heading", { name: "Blue Team" })).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("summarizes the member count", async () => {
    mockApi.mockResolvedValue({ id: "t1", name: "Blue Team", members: [{ id: "m1", name: "Alice" }] });
    renderRoute(<TeamPage />);
    expect(await screen.findByText("1 member")).toBeInTheDocument();
  });

  it("has no axe violations when loaded", async () => {
    mockApi.mockResolvedValue({ id: "t1", name: "Blue Team", members: [{ id: "m1", name: "Alice" }] });
    const { container } = renderRoute(<TeamPage />);
    await screen.findByRole("heading", { name: "Blue Team" });
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
