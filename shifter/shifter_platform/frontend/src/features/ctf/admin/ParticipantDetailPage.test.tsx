import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/api/client";

import { ParticipantDetailPage } from "./ParticipantDetailPage";

const mockApi = vi.mocked(apiFetch);

const PARTICIPANT = {
  id: "p1",
  name: "Ada Lovelace",
  email: "ada@example.com",
  status: "active",
  team_name: null,
  registered_at: "2026-08-01T10:00:00Z",
  invited_at: "2026-07-30T10:00:00Z",
  last_active_at: null,
  total_score: 300,
  solved_count: 3,
  attempt_count: 7,
  event_id: "e1",
};

function routeApi(handlers: (path: string) => unknown) {
  mockApi.mockImplementation((path: string) => Promise.resolve(handlers(path)));
}

function render() {
  return renderRoute(<ParticipantDetailPage />, {
    path: "/ctf/admin/participants/:participantId",
    initialEntries: ["/ctf/admin/participants/p1"],
  });
}

beforeEach(() => mockApi.mockReset());

describe("ParticipantDetailPage", () => {
  it("renders participant detail with range lifecycle controls", async () => {
    routeApi((path) => {
      if (path === "/ctf/participants/p1/") return PARTICIPANT;
      if (path === "/ctf/events/e1/ranges/")
        return { event_id: "e1", ranges: [{ participant_id: "p1", name: "Ada", email: "ada@example.com", range_instance_id: 5, range_status: "ready" }], progress: {} };
      if (path === "/ctf/events/e1/organizer-scoreboard/") return { brackets: [], rankings: [] };
      return {};
    });
    render();
    expect(await screen.findByRole("heading", { name: "Ada Lovelace" })).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Provision" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Destroy" })).toBeInTheDocument();
  });
});
