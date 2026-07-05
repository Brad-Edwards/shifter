import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useBootstrapContext } from "@/app/bootstrap-context";
import { useRestoreRisk, useRisks, type RiskFilters } from "@/api/risks";
import { SEVERITIES, STATUSES, type Severity, type Status } from "@/api/types";
import { Alert, Badge, Button, EmptyState, PageHeader, SelectField, Skeleton } from "@/ds";

import { ConfirmDialog } from "./ConfirmDialog";
import { formatTimestamp, severityIntent, statusIntent, titleCase } from "./format";

function parseFilters(params: URLSearchParams): RiskFilters {
  const severity = params.get("severity");
  const status = params.get("status");
  const page = Number(params.get("page") ?? "1");
  return {
    severity: SEVERITIES.includes(severity as Severity) ? (severity as Severity) : undefined,
    status: STATUSES.includes(status as Status) ? (status as Status) : undefined,
    includeDeleted: params.get("deleted") === "1",
    page: Number.isFinite(page) && page > 1 ? page : undefined,
  };
}

export function RiskListPage() {
  const [params, setParams] = useSearchParams();
  const bootstrap = useBootstrapContext();
  const canWrite = bootstrap.principal.is_staff;
  const filters = parseFilters(params);
  const query = useRisks(filters);
  const [restoreId, setRestoreId] = useState<number | null>(null);
  const restore = useRestoreRisk();

  const filtersActive = Boolean(filters.severity || filters.status || filters.includeDeleted);

  function updateParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.delete("page");
    setParams(next);
  }

  function goToPage(page: number) {
    const next = new URLSearchParams(params);
    if (page > 1) {
      next.set("page", String(page));
    } else {
      next.delete("page");
    }
    setParams(next);
  }

  const createAction = canWrite ? (
    <Link to="/risks/create" className="ds-btn ds-btn--primary ds-btn--sm">
      New risk
    </Link>
  ) : null;

  return (
    <>
      <PageHeader title="Risks" subtitle="Risk register" actions={createAction} />

      <div className="ds-card" style={{ marginBlockEnd: "var(--ds-space-4)" }}>
        <div
          className="ds-card__body"
          style={{ display: "flex", gap: "var(--ds-space-4)", flexWrap: "wrap", alignItems: "flex-end" }}
        >
          <SelectField
            label="Severity"
            value={filters.severity ?? ""}
            onChange={(event) => updateParam("severity", event.target.value || null)}
          >
            <option value="">All severities</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Status"
            value={filters.status ?? ""}
            onChange={(event) => updateParam("status", event.target.value || null)}
          >
            <option value="">All statuses</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </SelectField>
          <label className="ds-choice">
            <input
              className="ds-checkbox__control"
              type="checkbox"
              checked={filters.includeDeleted ?? false}
              onChange={(event) => updateParam("deleted", event.target.checked ? "1" : null)}
            />{" "}
            Show deleted
          </label>
        </div>
      </div>

      <div className="ds-card" aria-busy={query.isFetching} aria-live="polite">
        <RiskListBody
          query={query}
          filtersActive={filtersActive}
          canWrite={canWrite}
          onRestore={setRestoreId}
        />
      </div>

      {query.data ? (
        <div style={{ display: "flex", gap: "var(--ds-space-2)", marginBlockStart: "var(--ds-space-3)" }}>
          <Button
            variant="secondary"
            small
            disabled={!query.data.previous}
            onClick={() => goToPage((filters.page ?? 1) - 1)}
          >
            Previous
          </Button>
          <Button
            variant="secondary"
            small
            disabled={!query.data.next}
            onClick={() => goToPage((filters.page ?? 1) + 1)}
          >
            Next
          </Button>
        </div>
      ) : null}

      {restoreId !== null ? (
        <ConfirmDialog
          title="Restore risk?"
          confirmLabel="Restore"
          pending={restore.isPending}
          error={restore.error}
          onCancel={() => {
            restore.reset();
            setRestoreId(null);
          }}
          onConfirm={() =>
            restore.mutate(restoreId, {
              onSuccess: () => setRestoreId(null),
            })
          }
        >
          This risk will be restored to the active register.
        </ConfirmDialog>
      ) : null}
    </>
  );
}

function RiskListBody({
  query,
  filtersActive,
  canWrite,
  onRestore,
}: {
  query: ReturnType<typeof useRisks>;
  filtersActive: boolean;
  canWrite: boolean;
  onRestore: (id: number) => void;
}) {
  if (query.isLoading) {
    return (
      <div className="ds-card__body" style={{ display: "grid", gap: "var(--ds-space-2)" }}>
        <Skeleton width="90%" />
        <Skeleton width="80%" />
        <Skeleton width="85%" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="ds-card__body">
        <Alert intent="danger" role="alert" title="Could not load risks">
          Please retry.
        </Alert>
      </div>
    );
  }

  const results = query.data?.results ?? [];
  if (results.length === 0) {
    return (
      <div className="ds-card__body">
        <EmptyState title={filtersActive ? "No risks match these filters" : "No risks yet"}>
          {filtersActive ? "Adjust or clear the filters to see more." : "Create the first risk to get started."}
        </EmptyState>
      </div>
    );
  }

  return (
    <table className="ds-table ds-table--zebra">
      <thead>
        <tr>
          <th scope="col">Title</th>
          <th scope="col">Severity</th>
          <th scope="col">Status</th>
          <th scope="col">Score</th>
          <th scope="col">Comments</th>
          <th scope="col">Updated</th>
        </tr>
      </thead>
      <tbody>
        {results.map((risk) => (
          <tr key={risk.id}>
            <td>
              <Link className="ds-link" to={`/risks/${risk.id}`}>
                {risk.title}
              </Link>
              {risk.is_deleted ? (
                <>
                  {" "}
                  <Badge intent="neutral">Deleted</Badge>
                  {canWrite ? (
                    <>
                      {" "}
                      <Button variant="tertiary" small onClick={() => onRestore(risk.id)}>
                        Restore
                      </Button>
                    </>
                  ) : null}
                </>
              ) : null}
            </td>
            <td>
              <Badge intent={severityIntent(risk.severity ?? "low")} solid>
                {titleCase(risk.severity ?? "low")}
              </Badge>
            </td>
            <td>
              <Badge intent={statusIntent(risk.status ?? "open")}>{titleCase(risk.status ?? "open")}</Badge>
            </td>
            <td>{risk.risk_score ?? "—"}</td>
            <td>{risk.comment_count}</td>
            <td>{formatTimestamp(risk.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
