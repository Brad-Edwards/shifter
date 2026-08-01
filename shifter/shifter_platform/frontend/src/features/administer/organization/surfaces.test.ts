import { describe, expect, it } from "vitest";

import type { PrincipalWorkspaceContext } from "@/api/types";

import { resolveSelectedWorkspace, surfaceEnabled, WORKSPACE_SURFACES } from "./surfaces";

function ctx(overrides: Partial<PrincipalWorkspaceContext> = {}): PrincipalWorkspaceContext {
  return {
    organization: { uuid: "org-1", name: "Acme" },
    workspace_uuid: "ws-1",
    workspace_name: "Blue",
    is_personal: false,
    role: "member",
    capabilities: ["read_self_membership"],
    ...overrides,
  };
}

describe("resolveSelectedWorkspace", () => {
  it("returns null when the caller has no workspaces", () => {
    expect(resolveSelectedWorkspace([], "ws-1")).toBeNull();
    expect(resolveSelectedWorkspace([], undefined)).toBeNull();
  });

  it("falls back to the first workspace when no UUID is supplied", () => {
    const first = ctx({ workspace_uuid: "ws-1" });
    const second = ctx({ workspace_uuid: "ws-2" });
    expect(resolveSelectedWorkspace([first, second], undefined)).toBe(first);
  });

  it("returns the exact UUID match", () => {
    const first = ctx({ workspace_uuid: "ws-1" });
    const second = ctx({ workspace_uuid: "ws-2" });
    expect(resolveSelectedWorkspace([first, second], "ws-2")).toBe(second);
  });

  it("returns null for an invalid/stale UUID and never silently picks another workspace", () => {
    const personal = ctx({ workspace_uuid: "ws-personal", is_personal: true });
    const shared = ctx({ workspace_uuid: "ws-shared" });
    // A stale/unknown UUID must not resolve to the personal workspace or any other.
    expect(resolveSelectedWorkspace([personal, shared], "ws-unknown")).toBeNull();
  });
});

describe("surfaceEnabled", () => {
  const membership = WORKSPACE_SURFACES.find((s) => s.key === "membership")!;

  it("enables an ungated surface regardless of capabilities", () => {
    const invitations = WORKSPACE_SURFACES.find((s) => s.key === "invitations")!;
    expect(surfaceEnabled(invitations, ctx({ capabilities: [] }))).toBe(true);
  });

  it("gates a capability-bound surface on the advertised operation", () => {
    expect(surfaceEnabled(membership, ctx({ capabilities: ["read_members"] }))).toBe(true);
    expect(surfaceEnabled(membership, ctx({ capabilities: ["read_self_membership"] }))).toBe(false);
    expect(surfaceEnabled(membership, null)).toBe(false);
  });
});
