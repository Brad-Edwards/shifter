import { describe, expect, it } from "vitest";

import { router } from "./router";

interface RouteNode {
  readonly path?: string;
  readonly handle?: { readonly permissionPolicy?: string };
  readonly children?: readonly RouteNode[];
}

function collectPaths(routes: readonly RouteNode[]): string[] {
  const out: string[] = [];
  for (const route of routes) {
    if (route.path) out.push(route.path);
    if (route.children) out.push(...collectPaths(route.children));
  }
  return out;
}

describe("router", () => {
  it("mounts a single root route hosting the workspace layout", () => {
    expect(router.routes).toHaveLength(1);
    expect((router.routes[0] as RouteNode).path).toBe("/");
  });

  it("registers every top-level workspace group so deep links resolve", () => {
    const paths = collectPaths(router.routes as readonly RouteNode[]);
    for (const group of [
      "mission-control",
      "scenario-editor",
      "ctf",
      "ctf/admin",
      "raes-image-registry",
      "administer",
    ]) {
      expect(paths).toContain(group);
    }
  });

  it("registers a catch-all not-found route so an unknown path never dead-ends", () => {
    expect(collectPaths(router.routes as readonly RouteNode[])).toContain("*");
  });

  it("carries the advisory permission handle on each gated group", () => {
    const root = router.routes[0] as RouteNode;
    const group = (path: string) => root.children?.find((child) => child.path === path);

    expect(group("mission-control")?.handle?.permissionPolicy).toBe("authenticated");
    expect(group("scenario-editor")?.handle?.permissionPolicy).toBe("threat_research");
    expect(group("raes-image-registry")?.handle?.permissionPolicy).toBe("threat_research");
    expect(group("administer")?.handle?.permissionPolicy).toBe("staff");
    expect(group("ctf")?.handle?.permissionPolicy).toBe("ctf_participant");
    expect(group("ctf/admin")?.handle?.permissionPolicy).toBe("ctf_organizer");
  });

  it("nests the scoped workspace surfaces under the organization console", () => {
    const paths = collectPaths(router.routes as readonly RouteNode[]);
    expect(paths).toContain("organization");
    expect(paths).toContain("workspaces/:workspaceUuid");
    // The dynamic per-workspace surfaces are generated from WORKSPACE_SURFACES.
    expect(paths).toContain("membership");
  });
});
