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

/**
 * Scenario Editor domain types (#1371), re-exported from the generated OpenAPI
 * schema. The backend serializers (mirroring the Pydantic `ScenarioTemplate`)
 * remain the authoritative validator; do not hand-copy these shapes — regenerate
 * `schema.d.ts` via `npm run gen:api` instead.
 */
export type ScenarioCatalogEntry = components["schemas"]["CatalogEntry"];
export type ScenarioDetail = components["schemas"]["ScenarioDetail"];
export type ScenarioInstance = components["schemas"]["ScenarioInstance"];
export type ScenarioSubnet = components["schemas"]["ScenarioSubnet"];
export type ScenarioDCConfig = components["schemas"]["DCConfig"];
export type ScenarioCreate = components["schemas"]["ScenarioCreate"];
export type ScenarioUpdate = components["schemas"]["PatchedScenarioUpdate"];
export type ScenarioClone = components["schemas"]["ScenarioClone"];
export type ScenarioMetadataUpdate = components["schemas"]["PatchedScenarioMetadataUpdate"];
export type ScenarioCreated = components["schemas"]["ScenarioCreated"];
export type ScenarioExport = components["schemas"]["ScenarioExport"];
export type ScenarioMetadataState = components["schemas"]["ScenarioMetadataState"];
export type ScenarioAcesFields = components["schemas"]["AcesCatalogFields"];
export type ScenarioYamlValidation = components["schemas"]["YAMLValidationResult"];
export type ScenarioInstanceRole = components["schemas"]["ScenarioInstanceRoleEnum"];
export type ScenarioInstanceOsType = components["schemas"]["ScenarioInstanceOsTypeEnum"];

/** Scenario source classification the detail endpoint returns in `source`. */
export type ScenarioSource = "builtin" | "custom" | "aces" | "ctf";

/**
 * ACES image registry types (#1566), re-exported from the generated OpenAPI
 * schema. The `engine.services` write path stays the authoritative validator;
 * regenerate `schema.d.ts` via `npm run gen:api` instead of hand-copying.
 */
export type AcesImageMapping = components["schemas"]["AcesImageMappingView"];
export type AcesImageMappingRegister = components["schemas"]["AcesImageMappingRegister"];
export type AcesImageMappingDisable = components["schemas"]["AcesImageMappingDisable"];

/** Provider choices mirroring engine.models.AcesImageMapping.Provider (UI affordance only). */
export type AcesImageProvider = "gce" | "aws";
export const ACES_IMAGE_PROVIDERS: ReadonlyArray<{ value: AcesImageProvider; label: string }> = [
  { value: "gce", label: "Google Compute Engine" },
  { value: "aws", label: "AWS EC2" },
];

/**
 * Runtime option lists for the structured editor. Typed against the generated
 * enums so an invalid value fails typecheck; the backend serializer + Pydantic
 * schema stay authoritative (these are UI affordances only).
 */
export const INSTANCE_ROLES: readonly ScenarioInstanceRole[] = ["attacker", "victim", "dc"];
export const INSTANCE_OS_TYPES: readonly ScenarioInstanceOsType[] = ["kali", "windows", "ubuntu", "from_agent"];

/**
 * CTF participant workspace domain types (#1372), re-exported from the generated
 * OpenAPI schema. The participant-safe DRF projections (`ctf.api.projections` +
 * `ctf.api.serializers`) remain the authoritative shape; do not hand-copy these
 * — regenerate `schema.d.ts` via `npm run gen:api` instead. The participant
 * serializers deliberately never declare flag/solution/validator-config fields,
 * so those values cannot be expressed here.
 */
export type CtfCurrentEvent = components["schemas"]["ParticipantCurrentEvent"];
export type CtfParticipantSelf = components["schemas"]["ParticipantSelf"];
export type CtfEvent = components["schemas"]["ParticipantEvent"];
export type CtfChallengeListItem = components["schemas"]["ParticipantChallengeListItem"];
export type CtfChallengeDetail = components["schemas"]["ParticipantChallengeDetail"];
export type CtfHint = components["schemas"]["ParticipantHint"];
export type CtfChallengeFile = components["schemas"]["ParticipantChallengeFile"];
export type CtfTeam = components["schemas"]["ParticipantTeam"];
export type CtfTeamMember = components["schemas"]["ParticipantTeamMember"];
export type CtfSubmitFlagResult = components["schemas"]["SubmitFlagResult"];
export type CtfUseHintResult = components["schemas"]["UseHintResult"];
export type CtfSubmissionList = components["schemas"]["SubmissionListResponse"];
export type CtfRangeStatus = components["schemas"]["RangeStatusResponse"];
export type CtfRangeAccess = components["schemas"]["RangeAccessResponse"];
export type CtfScoreboard = components["schemas"]["PublicScoreboardResponse"];
