import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useBootstrapContext } from "@/app/bootstrap-context";
import { useDeleteRisk, useRestoreRisk, useRisk, useUpdateRisk } from "@/api/risks";
import type { Risk } from "@/api/types";
import { Alert, Badge, Breadcrumb, Button, Dialog, PageHeader, Spinner, Tabs, TextAreaField, type TabItem } from "@/ds";

import { CommentsPanel } from "./CommentsPanel";
import { ConfirmDialog } from "./ConfirmDialog";
import { HistoryPanel } from "./HistoryPanel";
import { formatTimestamp, severityIntent, statusIntent, titleCase } from "./format";

type ActionDialog = "delete" | "restore" | "close" | "reopen" | null;

const TABS: TabItem[] = [
  { id: "overview", label: "Overview" },
  { id: "mitigation", label: "Mitigation" },
  { id: "comments", label: "Comments" },
  { id: "history", label: "History" },
];

function DetailRow({ label, value }: Readonly<{ label: string; value: React.ReactNode }>) {
  return (
    <>
      <dt className="ds-kv__key">{label}</dt>
      <dd className="ds-kv__value">{value}</dd>
    </>
  );
}

function MultilineText({ value }: Readonly<{ value: string }>) {
  return <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{value || "—"}</p>;
}

function DetailActions({
  risk,
  riskId,
  onAction,
}: Readonly<{ risk: Risk; riskId: number; onAction: (dialog: ActionDialog) => void }>) {
  if (risk.is_deleted) {
    return (
      <Button variant="secondary" small onClick={() => onAction("restore")}>
        Restore
      </Button>
    );
  }
  return (
    <>
      <Link className="ds-btn ds-btn--secondary ds-btn--sm" to={`/risks/${riskId}/edit`}>
        Edit
      </Link>
      {risk.status === "closed" ? (
        <Button variant="secondary" small onClick={() => onAction("reopen")}>
          Reopen
        </Button>
      ) : (
        <Button variant="secondary" small onClick={() => onAction("close")}>
          Close
        </Button>
      )}
      <Button variant="destructive" small onClick={() => onAction("delete")}>
        Delete
      </Button>
    </>
  );
}

function DetailPanel({
  tab,
  risk,
  riskId,
  canWrite,
  canViewAudit,
}: Readonly<{ tab: string; risk: Risk; riskId: number; canWrite: boolean; canViewAudit: boolean }>) {
  if (tab === "overview") {
    return (
      <dl className="ds-kv">
        <DetailRow label="Severity" value={titleCase(risk.severity ?? "low")} />
        <DetailRow label="Status" value={titleCase(risk.status ?? "open")} />
        <DetailRow label="Likelihood" value={risk.likelihood_score ?? "—"} />
        <DetailRow label="Impact" value={risk.impact_score ?? "—"} />
        <DetailRow label="Risk score" value={risk.risk_score ?? "—"} />
        <DetailRow label="STRIDE" value={(risk.stride_categories as string[] | undefined)?.join(", ") || "—"} />
        <DetailRow label="Description" value={<MultilineText value={risk.description ?? ""} />} />
        <DetailRow label="Created" value={formatTimestamp(risk.created_at)} />
        <DetailRow label="Updated" value={formatTimestamp(risk.updated_at)} />
      </dl>
    );
  }
  if (tab === "mitigation") {
    return (
      <dl className="ds-kv">
        <DetailRow label="Mitigation status" value={<MultilineText value={risk.mitigation_status ?? ""} />} />
        <DetailRow label="Attack vector" value={<MultilineText value={risk.attack_vector ?? ""} />} />
        <DetailRow label="Affected assets" value={<MultilineText value={risk.affected_assets ?? ""} />} />
        <DetailRow label="Resolution reason" value={<MultilineText value={risk.resolution_reason ?? ""} />} />
      </dl>
    );
  }
  if (tab === "comments") {
    return (
      <CommentsPanel
        riskId={riskId}
        canWrite={canWrite}
        readOnly={Boolean(risk.is_deleted)}
        includeDeleted={Boolean(risk.is_deleted)}
      />
    );
  }
  return <HistoryPanel riskId={riskId} canViewAudit={canViewAudit} />;
}

type RiskMutations = Readonly<{
  remove: ReturnType<typeof useDeleteRisk>;
  restore: ReturnType<typeof useRestoreRisk>;
  update: ReturnType<typeof useUpdateRisk>;
}>;

function DetailDialogs({
  dialog,
  riskId,
  mutations,
  resolution,
  onResolutionChange,
  onClose,
  onDeleted,
}: Readonly<{
  dialog: ActionDialog;
  riskId: number;
  mutations: RiskMutations;
  resolution: string;
  onResolutionChange: (value: string) => void;
  onClose: () => void;
  onDeleted: () => void;
}>) {
  const { remove, restore, update } = mutations;
  if (dialog === "delete") {
    return (
      <ConfirmDialog
        title="Delete risk?"
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        error={remove.error}
        onCancel={onClose}
        onConfirm={() => remove.mutate(riskId, { onSuccess: onDeleted })}
      >
        This risk will be moved to the deleted state. It can be restored later.
      </ConfirmDialog>
    );
  }
  if (dialog === "restore") {
    return (
      <ConfirmDialog
        title="Restore risk?"
        confirmLabel="Restore"
        pending={restore.isPending}
        error={restore.error}
        onCancel={onClose}
        onConfirm={() => restore.mutate(riskId, { onSuccess: onClose })}
      >
        This risk will be returned to the active register.
      </ConfirmDialog>
    );
  }
  if (dialog === "reopen") {
    return (
      <ConfirmDialog
        title="Reopen risk?"
        confirmLabel="Reopen"
        pending={update.isPending}
        error={update.error}
        onCancel={onClose}
        onConfirm={() => update.mutate({ status: "open" }, { onSuccess: onClose })}
      >
        This risk will be moved back to open.
      </ConfirmDialog>
    );
  }
  if (dialog === "close") {
    return (
      <Dialog
        title="Close risk?"
        onClose={onClose}
        footer={
          <>
            <Button variant="secondary" onClick={onClose} disabled={update.isPending}>
              Cancel
            </Button>
            <Button
              onClick={() => update.mutate({ status: "closed", resolution_reason: resolution }, { onSuccess: onClose })}
              loading={update.isPending}
            >
              Close risk
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Resolution reason"
          rows={3}
          value={resolution}
          onChange={(event) => onResolutionChange(event.target.value)}
        />
      </Dialog>
    );
  }
  return null;
}

export function RiskDetailPage() {
  const params = useParams();
  const navigate = useNavigate();
  const riskId = Number(params.id);
  const bootstrap = useBootstrapContext();
  const canWrite = bootstrap.principal.is_staff;
  const canViewAudit = bootstrap.principal.is_staff || bootstrap.principal.is_superuser;

  const query = useRisk(riskId, true);
  const remove = useDeleteRisk();
  const restore = useRestoreRisk();
  const update = useUpdateRisk(riskId);

  const [tab, setTab] = useState("overview");
  const [dialog, setDialog] = useState<ActionDialog>(null);
  const [resolution, setResolution] = useState("");

  function closeDialog() {
    remove.reset();
    restore.reset();
    update.reset();
    setResolution("");
    setDialog(null);
  }

  if (query.isLoading) {
    return (
      <div className="ds-empty">
        <Spinner label="Loading risk" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <Alert intent="warning" role="status" title="Risk unavailable">
        This risk was not found or has been removed.{" "}
        <Link className="ds-link" to="/">
          Back to risks
        </Link>
        .
      </Alert>
    );
  }

  const risk: Risk = query.data;

  return (
    <>
      <Breadcrumb items={[{ label: "Govern" }, { label: "Risks", to: "/" }, { label: risk.title ?? "Risk" }]} />
      <PageHeader
        title={risk.title ?? "Risk"}
        subtitle={
          <span style={{ display: "inline-flex", gap: "var(--ds-space-2)", alignItems: "center" }}>
            <Badge intent={severityIntent(risk.severity ?? "low")} solid>
              {titleCase(risk.severity ?? "low")}
            </Badge>
            <Badge intent={statusIntent(risk.status ?? "open")}>{titleCase(risk.status ?? "open")}</Badge>
            {risk.is_deleted ? <Badge intent="neutral">Deleted</Badge> : null}
          </span>
        }
        actions={canWrite ? <DetailActions risk={risk} riskId={riskId} onAction={setDialog} /> : null}
      />

      <Tabs items={TABS} value={tab} onChange={setTab} label="Risk detail sections" />

      <div
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="ds-card"
        style={{ marginBlockStart: "var(--ds-space-3)" }}
      >
        <div className="ds-card__body">
          <DetailPanel tab={tab} risk={risk} riskId={riskId} canWrite={canWrite} canViewAudit={canViewAudit} />
        </div>
      </div>

      <DetailDialogs
        dialog={dialog}
        riskId={riskId}
        mutations={{ remove, restore, update }}
        resolution={resolution}
        onResolutionChange={setResolution}
        onClose={closeDialog}
        onDeleted={() => navigate("/")}
      />
    </>
  );
}
