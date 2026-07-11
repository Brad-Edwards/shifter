/**
 * Domain types for the Risk Register, re-exported from the generated OpenAPI
 * schema (`schema.d.ts`, produced by `npm run gen:api`). Do not hand-copy Risk,
 * Comment, AuditLog, severity, status, or STRIDE shapes — regenerate instead.
 */
import type { components } from "./schema";

export type Risk = components["schemas"]["Risk"];
export type RiskCreate = components["schemas"]["RiskCreate"];
export type RiskUpdate = components["schemas"]["RiskUpdate"];
export type PatchedRiskUpdate = components["schemas"]["PatchedRiskUpdate"];
export type Comment = components["schemas"]["Comment"];
export type AuditLog = components["schemas"]["AuditLog"];
export type PaginatedRiskList = components["schemas"]["PaginatedRiskList"];
export type PaginatedAuditLogList = components["schemas"]["PaginatedAuditLogList"];
export type Bootstrap = components["schemas"]["Bootstrap"];
export type BootstrapModes = components["schemas"]["BootstrapModes"];
export type BootstrapPermissions = components["schemas"]["BootstrapPermissions"];
export type UxMode = BootstrapModes["default"];
export type DashboardSummary = components["schemas"]["DashboardSummary"];

export type Severity = components["schemas"]["SeverityEnum"];
export type Status = components["schemas"]["StatusEnum"];

export type StrideCode = "S" | "T" | "R" | "I" | "D" | "E";

/**
 * Runtime option lists for filters/forms. Typed against the generated enums so
 * an invalid value fails typecheck; the backend serializers remain the
 * authoritative validator (these are UI affordances only).
 */
export const SEVERITIES: readonly Severity[] = ["critical", "high", "medium", "low"];
export const STATUSES: readonly Status[] = ["open", "acknowledged", "mitigating", "resolved", "closed"];
export const STRIDE_OPTIONS: ReadonlyArray<{ code: StrideCode; label: string }> = [
  { code: "S", label: "Spoofing" },
  { code: "T", label: "Tampering" },
  { code: "R", label: "Repudiation" },
  { code: "I", label: "Information Disclosure" },
  { code: "D", label: "Denial of Service" },
  { code: "E", label: "Elevation of Privilege" },
];

/** Normalize the JSONField `stride_categories` (typed `unknown`) into a string list. */
export function strideList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/**
 * Mission Control domain types (#1370), re-exported from the generated OpenAPI
 * schema. Only the shapes this foundation chunk (and the pages/live-access
 * chunks that follow it) need are re-exported here; do not hand-copy field
 * shapes — regenerate `schema.d.ts` instead.
 */
export type RangeStatus = components["schemas"]["ResourceStatusEnum"];
export type RangePresentation = components["schemas"]["RangePresentation"];
export type InstancePresentation = components["schemas"]["InstancePresentation"];
export type CurrentRangeResponse = components["schemas"]["CurrentRangeResponse"];
export type LaunchRangeResponse = components["schemas"]["LaunchRangeResponse"];
export type SuccessResponse = components["schemas"]["SuccessResponse"];
export type AgentListResponse = components["schemas"]["AgentListResponse"];
export type AgentListItem = components["schemas"]["AgentListItem"];
export type ScenarioListResponse = components["schemas"]["ScenarioListResponse"];
export type ScenarioListItem = components["schemas"]["ScenarioListItem"];
export type RangeHistory = components["schemas"]["RangeHistory"];
export type RangeHistoryResponse = components["schemas"]["RangeHistoryResponse"];
export type GuacamoleBootstrapQueued = components["schemas"]["GuacamoleBootstrapQueued"];
export type GuacamoleBootstrapStatus = components["schemas"]["GuacamoleBootstrapStatus"];
export type NGFWListResponse = components["schemas"]["NGFWListResponse"];
export type NGFWListItem = components["schemas"]["NGFWListItem"];
export type NGFWCreateResponse = components["schemas"]["NGFWCreateResponse"];
export type NGFWDestroyResponse = components["schemas"]["NGFWDestroyResponse"];
export type CredentialCreateResponse = components["schemas"]["CredentialCreateResponse"];
export type UploadInitiateResponse = components["schemas"]["UploadInitiateResponse"];
export type UploadCompleteResponse = components["schemas"]["UploadCompleteResponse"];
