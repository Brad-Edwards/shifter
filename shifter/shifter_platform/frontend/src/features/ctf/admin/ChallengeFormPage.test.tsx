import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderRoute } from "@/test/utils";

vi.mock("@/api/client", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/api/client";

import { ChallengeFormPage } from "./ChallengeFormPage";

const mockApi = vi.mocked(apiFetch);

const CHALLENGE = {
  id: "c1",
  name: "SQL Injection",
  description: "Find the flag",
  category: "web",
  points: 100,
  difficulty: "easy",
  flag_format: "FLAG{...}",
  hints: [],
  max_attempts: 0,
  order: 0,
  release_time: null,
  tags: ["sqli"],
  topics: [],
  solution: "",
};

function routeApi(handlers: (path: string) => unknown) {
  mockApi.mockImplementation((path: string) => Promise.resolve(handlers(path)));
}

beforeEach(() => mockApi.mockReset());

describe("ChallengeFormPage (create)", () => {
  it("renders the flag field and defers flag/hint/file management until saved", async () => {
    renderRoute(<ChallengeFormPage mode="create" />, {
      path: "/ctf/admin/events/:eventId/challenges/create",
      initialEntries: ["/ctf/admin/events/e1/challenges/create"],
    });
    expect(await screen.findByRole("heading", { name: "New challenge" })).toBeInTheDocument();
    expect(screen.getByLabelText("Flag")).toBeInTheDocument();
    expect(screen.getByText(/Save the challenge to manage/)).toBeInTheDocument();
  });
});

describe("ChallengeFormPage (edit)", () => {
  it("loads the challenge and renders the management sections", async () => {
    routeApi((path) => {
      if (path === "/ctf/challenges/c1/") return CHALLENGE;
      if (path === "/ctf/challenges/c1/hints/") return { hints: [] };
      if (path === "/ctf/challenges/c1/files/") return { files: [] };
      if (path === "/ctf/challenges/c1/prerequisites/") return { prerequisites: [] };
      return {};
    });
    renderRoute(<ChallengeFormPage mode="edit" />, {
      path: "/ctf/admin/challenges/:challengeId/edit",
      initialEntries: ["/ctf/admin/challenges/c1/edit"],
    });

    expect(await screen.findByDisplayValue("SQL Injection")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Flags" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Hints" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Files" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Prerequisites" })).toBeInTheDocument();
  });
});
