import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { axe } from "vitest-axe";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/api/client";

import { EventHomePage } from "./EventHomePage";

const mockApi = vi.mocked(apiFetch);

function currentEvent(participant: Record<string, unknown> = {}) {
  return {
    event: {
      id: "e1",
      name: "Spring CTF",
      description: "A friendly competition.",
      status: "active",
      team_mode: true,
      scoring_mode: "static",
      rating_visibility: "disabled",
      attempt_limit_mode: "unlimited",
      scoreboard_visible: true,
      event_start: null,
      event_end: null,
    },
    participant: {
      id: "p1",
      name: "Alice",
      status: "active",
      range_status: "",
      cached_score: 350,
      cached_solve_count: 4,
      team: { id: "t1", name: "Blue Team" },
      bracket: null,
      ...participant,
    },
  };
}

beforeEach(() => mockApi.mockReset());

describe("EventHomePage", () => {
  it("renders the event and participant state", async () => {
    mockApi.mockResolvedValue(currentEvent());
    renderRoute(<EventHomePage />);
    expect(await screen.findByRole("heading", { name: "Spring CTF" })).toBeInTheDocument();
    expect(screen.getByText("350")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("Team: Blue Team")).toBeInTheDocument();
  });

  it("links to the other participant surfaces", async () => {
    mockApi.mockResolvedValue(currentEvent());
    renderRoute(<EventHomePage />);
    expect(await screen.findByRole("link", { name: "Challenges" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Scoreboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Team" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Range" })).toBeInTheDocument();
  });

  it("renders the solo state when the participant has no team", async () => {
    mockApi.mockResolvedValue(currentEvent({ team: null }));
    renderRoute(<EventHomePage />);
    expect(await screen.findByText("Solo")).toBeInTheDocument();
  });

  it("has no axe violations when loaded", async () => {
    mockApi.mockResolvedValue(currentEvent());
    const { container } = renderRoute(<EventHomePage />);
    await screen.findByRole("heading", { name: "Spring CTF" });
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
