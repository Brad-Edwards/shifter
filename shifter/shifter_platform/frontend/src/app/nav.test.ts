import { describe, expect, it } from "vitest";

import type { Bootstrap } from "@/api/types";
import { STAFF_BOOTSTRAP } from "@/test/utils";

import { isNavEntryVisible, permissionAllows, visibleNavGroups, type NavEntry } from "./nav";

function bootstrap(overrides: Partial<Bootstrap> = {}): Bootstrap {
  return {
    ...STAFF_BOOTSTRAP,
    ...overrides,
    principal: { ...STAFF_BOOTSTRAP.principal, ...(overrides.principal ?? {}) },
    permissions: { ...STAFF_BOOTSTRAP.permissions, ...(overrides.permissions ?? {}) },
    modes: { ...STAFF_BOOTSTRAP.modes, ...(overrides.modes ?? {}) },
    feature_flags: { ...STAFF_BOOTSTRAP.feature_flags, ...(overrides.feature_flags ?? {}) },
  };
}

const STAFF_ENTRY: NavEntry = {
  surface: "Users",
  audience: "organizer",
  routeName: "administer:users",
  permissionPolicy: "staff",
  ownerApp: "management",
  purpose: "users",
  mode: "operator",
  group: "Administer",
  routePath: "/administer",
  iconKey: "user-cog",
};

describe("permissionAllows", () => {
  it("maps each policy to the right bootstrap flag", () => {
    const bs = bootstrap({
      permissions: {
        ...STAFF_BOOTSTRAP.permissions,
        can_access_threat_research: false,
        is_ctf_organizer: true,
        is_ctf_participant: false,
      },
    });
    expect(permissionAllows("authenticated", bs)).toBe(true);
    expect(permissionAllows("threat_research", bs)).toBe(false);
    expect(permissionAllows("ctf_organizer", bs)).toBe(true);
    expect(permissionAllows("ctf_participant", bs)).toBe(false);
    expect(permissionAllows("staff", bs)).toBe(true);
  });
});

describe("isNavEntryVisible", () => {
  it("hides an entry when its advisory permission is denied", () => {
    const bs = bootstrap({
      principal: { ...STAFF_BOOTSTRAP.principal, is_staff: false },
    });
    expect(isNavEntryVisible(STAFF_ENTRY, bs)).toBe(false);
  });

  it("hides an entry when its feature flag is off", () => {
    const gated: NavEntry = { ...STAFF_ENTRY, featureFlag: "administer_spa" };
    const bs = bootstrap({
      feature_flags: {
        ...STAFF_BOOTSTRAP.feature_flags,
        platform_spa: true,
        mission_control_spa: true,
        scenario_editor_spa: false,
        ctf_workspace_spa: false,
        raes_native_provisioning: false,
        administer_spa: false,
      },
    });
    expect(isNavEntryVisible(gated, bs)).toBe(false);
  });
});

describe("visibleNavGroups", () => {
  it("returns operator groups filtered by advisory permissions", () => {
    const bs = bootstrap({
      permissions: {
        ...STAFF_BOOTSTRAP.permissions,
        can_access_threat_research: false,
        is_ctf_organizer: false,
        is_ctf_participant: false,
      },
    });
    const groups = visibleNavGroups("operator", bs);
    const names = groups.map((g) => g.group);
    expect(names).toContain("Operate");
    expect(names).toContain("Administer");
    // No threat-research access -> Author (Scenarios) is hidden and its group drops.
    expect(names).not.toContain("Author");
    // No participant mode groups leak into operator mode.
    expect(names).not.toContain("Participate");
  });

  it("returns participant groups only for participant mode", () => {
    const bs = bootstrap({
      permissions: {
        ...STAFF_BOOTSTRAP.permissions,
        can_access_threat_research: false,
        is_ctf_organizer: false,
        is_ctf_participant: true,
      },
    });
    const groups = visibleNavGroups("participant", bs);
    expect(groups.map((g) => g.group)).toEqual(["Participate"]);
    expect(groups[0].entries.map((e) => e.surface)).toContain("Challenges");
  });

  it("filters an entry's children by advisory visibility", () => {
    const bs = bootstrap();
    const operate = visibleNavGroups("operator", bs).find((g) => g.group === "Operate");
    const assets = operate?.entries.find((e) => e.surface === "Assets");
    expect(assets?.children?.map((c) => c.surface)).toEqual(["Agents", "NGFW", "Credentials"]);
  });

  it("exposes CTF participant entries as internal, flag-gated SPA routes (#1372)", () => {
    const bs = bootstrap({ permissions: { ...STAFF_BOOTSTRAP.permissions, is_ctf_participant: true } });
    const [participate] = visibleNavGroups("participant", bs);
    const eventHome = participate.entries.find((e) => e.surface === "Event Home");
    expect(eventHome?.external).toBeFalsy();
    expect(eventHome?.featureFlag).toBe("ctf_workspace_spa");
    expect(eventHome?.routePath).toBe("/ctf/");
    expect(participate.entries.map((entry) => entry.surface)).not.toContain("Terminal");
    expect(participate.entries.every((e) => e.featureFlag === "ctf_workspace_spa" && !e.external)).toBe(true);
  });

  it("hides the Participate group until the CTF workspace flag flips on (#1372)", () => {
    const bs = bootstrap({
      permissions: { ...STAFF_BOOTSTRAP.permissions, is_ctf_participant: true },
      feature_flags: { ...STAFF_BOOTSTRAP.feature_flags, ctf_workspace_spa: false },
    });
    expect(visibleNavGroups("participant", bs)).toEqual([]);
  });

  it("shows the Organization console entry for staff with administer_spa on (#1938)", () => {
    const administer = visibleNavGroups("operator", bootstrap()).find((g) => g.group === "Administer");
    const org = administer?.entries.find((e) => e.surface === "Organization");
    expect(org?.routePath).toBe("/administer/organization");
    expect(org?.permissionPolicy).toBe("staff");
    expect(org?.featureFlag).toBe("administer_spa");
    expect(org?.external).toBeFalsy();
  });

  it("hides the Organization console entry when administer_spa is off (#1938)", () => {
    const bs = bootstrap({ feature_flags: { ...STAFF_BOOTSTRAP.feature_flags, administer_spa: false } });
    const administer = visibleNavGroups("operator", bs).find((g) => g.group === "Administer");
    expect(administer?.entries.some((e) => e.surface === "Organization")).toBeFalsy();
  });

  it("keeps the Django Admin escape hatch present and external in every rollout state (#1938)", () => {
    // administer_spa OFF: the SPA Administer entries drop, but the Django admin
    // escape hatch must remain reachable from the sidebar.
    const bs = bootstrap({ feature_flags: { ...STAFF_BOOTSTRAP.feature_flags, administer_spa: false } });
    const administer = visibleNavGroups("operator", bs).find((g) => g.group === "Administer");
    const djangoAdmin = administer?.entries.find((e) => e.surface === "Django Admin");
    expect(djangoAdmin?.external).toBe(true);
    expect(djangoAdmin?.routePath).toBe("/admin/");
    expect(djangoAdmin?.featureFlag).toBeUndefined();
  });
});
