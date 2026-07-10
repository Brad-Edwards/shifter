/**
 * Shared, role-aware platform navigation contract (#1369, ADR-013).
 *
 * This is the single source of navigation truth the platform shell renders from:
 * primary side navigation, mode switching, breadcrumbs, and contextual subnav
 * all derive from these entries. Adding a surface adds one entry here rather
 * than editing shell components (the navigation extensibility seam). The
 * per-surface issues (#1370–#1374) register their entries into this contract.
 *
 * Each entry carries the UX-003 minimum contract (`surface`, `audience`,
 * `routeName`, `permissionPolicy`, `ownerApp`, `purpose`) plus the #1368
 * presentation fields (`mode`, `group`, `routePath`, `iconKey`, `activeContext`,
 * `featureFlag`, `children`). See `docs/design/ux-003-information-architecture-sitemap.md`.
 *
 * Navigation visibility is advisory UX only, never authorization: entries may be
 * hidden for clarity, but every endpoint stays the authority (ADR-013-R3/R4). A
 * surface not yet migrated to the SPA is an `external` entry that links to its
 * existing Django route via full-page navigation; it moves to an in-SPA route
 * when its module issue lands.
 */
import type { Bootstrap } from "@/api/types";

export type UxMode = "participant" | "operator";

export type NavAudience = "participant" | "organizer" | "both" | "system";

export type NavGroupName = "Participate" | "Operate" | "Author" | "Govern" | "Administer";

/**
 * Advisory permission policy keys. The shell maps each to bootstrap flags in
 * `isNavEntryVisible`; the backend endpoints remain the authority.
 */
export type PermissionPolicy =
  | "authenticated"
  | "risk_register_access"
  | "threat_research"
  | "ctf_organizer"
  | "ctf_participant"
  | "staff";

export type NavIconKey =
  | "home"
  | "layout-dashboard"
  | "flag"
  | "server"
  | "trophy"
  | "users"
  | "help-circle"
  | "boxes"
  | "bot"
  | "shield"
  | "key-round"
  | "terminal"
  | "settings"
  | "file-code"
  | "shield-alert"
  | "user-cog";

export interface NavEntry {
  /** Canonical surface name (UX-003 taxonomy). */
  readonly surface: string;
  /** UX-003 audience classification. */
  readonly audience: NavAudience;
  /** Stable route name (Django route name / durable id). */
  readonly routeName: string;
  /** Advisory permission policy key (not authorization). */
  readonly permissionPolicy: PermissionPolicy;
  /** Owning Django app. */
  readonly ownerApp: string;
  /** Short purpose statement. */
  readonly purpose: string;
  /** UX mode this entry belongs to. */
  readonly mode: UxMode;
  /** IA group placement. */
  readonly group: NavGroupName;
  /** SPA path (in-app) or absolute legacy URL (when `external`). */
  readonly routePath: string;
  /** lucide icon key rendered by the shell. */
  readonly iconKey: NavIconKey;
  /** Optional active range/event context this surface reads. */
  readonly activeContext?: "range" | "event";
  /** Optional rollout flag gating visibility. */
  readonly featureFlag?: keyof Bootstrap["feature_flags"];
  /** True when the entry links to a legacy Django route via full-page nav. */
  readonly external?: boolean;
  /** Optional nested/contextual entries. */
  readonly children?: readonly NavEntry[];
}

export interface NavGroup {
  readonly group: NavGroupName;
  readonly mode: UxMode;
  readonly entries: readonly NavEntry[];
}

/**
 * The seeded platform IA (UX-003 sitemap + #1368). Durable, revisited surfaces
 * only; event/entity-scoped surfaces (participants, per-event challenges) render
 * as contextual subnav within their entity, not as top-level nav. Surfaces not
 * yet on the SPA link to their legacy Django route (`external`).
 */
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    group: "Participate",
    mode: "participant",
    entries: [
      {
        surface: "Event Home",
        audience: "participant",
        routeName: "ctf:dashboard",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Event entry point with current participant state.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/",
        iconKey: "home",
        activeContext: "event",
        external: true,
      },
      {
        surface: "Challenges",
        audience: "participant",
        routeName: "ctf:challenges",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Browse available challenges and progression.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/challenges/",
        iconKey: "flag",
        external: true,
      },
      {
        surface: "Range",
        audience: "participant",
        routeName: "ctf:range",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Access range status and participant resources.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/range/",
        iconKey: "server",
        activeContext: "range",
        external: true,
      },
      {
        surface: "Scoreboard",
        audience: "participant",
        routeName: "ctf:scoreboard",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Compare event scoring and rank.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/scoreboard/",
        iconKey: "trophy",
        external: true,
      },
      {
        surface: "Team",
        audience: "participant",
        routeName: "ctf:team",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Inspect team membership and status.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/team/",
        iconKey: "users",
        external: true,
      },
      {
        surface: "Help",
        audience: "participant",
        routeName: "ctf:help",
        permissionPolicy: "ctf_participant",
        ownerApp: "ctf",
        purpose: "Get CTF-specific help.",
        mode: "participant",
        group: "Participate",
        routePath: "/ctf/help/",
        iconKey: "help-circle",
        external: true,
      },
    ],
  },
  {
    group: "Operate",
    mode: "operator",
    entries: [
      {
        surface: "Overview",
        audience: "organizer",
        routeName: "home",
        permissionPolicy: "authenticated",
        ownerApp: "config",
        purpose: "Role-aware operational dashboard.",
        mode: "operator",
        group: "Operate",
        routePath: "/",
        iconKey: "layout-dashboard",
      },
      {
        surface: "Ranges",
        audience: "organizer",
        routeName: "mission_control:dashboard",
        permissionPolicy: "authenticated",
        ownerApp: "mission_control",
        purpose: "Launch and monitor ranges.",
        mode: "operator",
        group: "Operate",
        routePath: "/mission-control/",
        iconKey: "server",
        activeContext: "range",
        external: true,
      },
      {
        surface: "CTF Events",
        audience: "organizer",
        routeName: "ctf:admin_dashboard",
        permissionPolicy: "ctf_organizer",
        ownerApp: "ctf",
        purpose: "Monitor and manage CTF operations.",
        mode: "operator",
        group: "Operate",
        routePath: "/ctf/admin/",
        iconKey: "flag",
        activeContext: "event",
        external: true,
      },
      {
        surface: "Assets",
        audience: "organizer",
        routeName: "mission_control:agents",
        permissionPolicy: "authenticated",
        ownerApp: "mission_control",
        purpose: "Operational resources: agents, NGFW, credentials.",
        mode: "operator",
        group: "Operate",
        routePath: "/mission-control/agents/",
        iconKey: "boxes",
        external: true,
        children: [
          {
            surface: "Agents",
            audience: "organizer",
            routeName: "mission_control:agents",
            permissionPolicy: "authenticated",
            ownerApp: "mission_control",
            purpose: "Inspect or delete available agents.",
            mode: "operator",
            group: "Operate",
            routePath: "/mission-control/agents/",
            iconKey: "bot",
            external: true,
          },
          {
            surface: "NGFW",
            audience: "organizer",
            routeName: "mission_control:ngfw_list",
            permissionPolicy: "authenticated",
            ownerApp: "mission_control",
            purpose: "List NGFW instances.",
            mode: "operator",
            group: "Operate",
            routePath: "/mission-control/ngfw/",
            iconKey: "shield",
            external: true,
          },
          {
            surface: "Credentials",
            audience: "organizer",
            routeName: "mission_control:credentials",
            permissionPolicy: "authenticated",
            ownerApp: "mission_control",
            purpose: "List reusable credentials.",
            mode: "operator",
            group: "Operate",
            routePath: "/mission-control/credentials/",
            iconKey: "key-round",
            external: true,
          },
        ],
      },
      {
        surface: "Terminal",
        audience: "both",
        routeName: "mission_control:terminal",
        permissionPolicy: "authenticated",
        ownerApp: "mission_control",
        purpose: "Access terminal sessions when a range is available.",
        mode: "operator",
        group: "Operate",
        routePath: "/mission-control/terminal/",
        iconKey: "terminal",
        activeContext: "range",
        external: true,
      },
      {
        surface: "Settings",
        audience: "organizer",
        routeName: "mission_control:settings",
        permissionPolicy: "authenticated",
        ownerApp: "mission_control",
        purpose: "Change user or platform settings.",
        mode: "operator",
        group: "Operate",
        routePath: "/mission-control/settings/",
        iconKey: "settings",
        external: true,
      },
    ],
  },
  {
    group: "Author",
    mode: "operator",
    entries: [
      {
        surface: "Scenarios",
        audience: "organizer",
        routeName: "scenario_editor:list",
        permissionPolicy: "threat_research",
        ownerApp: "cms",
        purpose: "Browse scenarios and readiness metadata.",
        mode: "operator",
        group: "Author",
        routePath: "/scenario-editor/",
        iconKey: "file-code",
        external: true,
      },
    ],
  },
  {
    group: "Govern",
    mode: "operator",
    entries: [
      {
        surface: "Risk Register",
        audience: "organizer",
        routeName: "risk_register:risk_list",
        permissionPolicy: "risk_register_access",
        ownerApp: "risk_register",
        purpose: "List current and historical risks.",
        mode: "operator",
        group: "Govern",
        routePath: "/risk-register",
        iconKey: "shield-alert",
      },
    ],
  },
  {
    group: "Administer",
    mode: "operator",
    entries: [
      {
        surface: "Users",
        audience: "organizer",
        routeName: "admin:index",
        permissionPolicy: "staff",
        ownerApp: "management",
        purpose: "Manage users, groups, and access.",
        mode: "operator",
        group: "Administer",
        routePath: "/admin/",
        iconKey: "user-cog",
        external: true,
      },
    ],
  },
];

/** Evaluate an advisory permission policy against the bootstrap payload. */
export function permissionAllows(policy: PermissionPolicy, bootstrap: Bootstrap): boolean {
  switch (policy) {
    case "authenticated":
      return bootstrap.principal.is_authenticated;
    case "risk_register_access":
      return bootstrap.permissions.can_access_risk_register;
    case "threat_research":
      return bootstrap.permissions.can_access_threat_research;
    case "ctf_organizer":
      return bootstrap.permissions.is_ctf_organizer;
    case "ctf_participant":
      return bootstrap.permissions.is_ctf_participant;
    case "staff":
      return bootstrap.principal.is_staff;
    default:
      return false;
  }
}

/** True when an entry should be shown (advisory permission + feature flag). */
export function isNavEntryVisible(entry: NavEntry, bootstrap: Bootstrap): boolean {
  if (entry.featureFlag && !bootstrap.feature_flags[entry.featureFlag]) {
    return false;
  }
  return permissionAllows(entry.permissionPolicy, bootstrap);
}

/**
 * Return the visible nav groups for a mode: filtered to the current mode, with
 * each group's entries (and any children) filtered by advisory visibility, and
 * empty groups dropped.
 */
export function visibleNavGroups(mode: UxMode, bootstrap: Bootstrap): NavGroup[] {
  return NAV_GROUPS.filter((group) => group.mode === mode)
    .map((group) => ({
      ...group,
      entries: group.entries
        .filter((entry) => isNavEntryVisible(entry, bootstrap))
        .map((entry) => ({
          ...entry,
          children: entry.children?.filter((child) => isNavEntryVisible(child, bootstrap)),
        })),
    }))
    .filter((group) => group.entries.length > 0);
}
