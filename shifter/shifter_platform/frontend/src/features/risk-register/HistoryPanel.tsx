import { useAudit } from "@/api/risks";
import { ApiError } from "@/api/errors";
import { Alert, EmptyState, Spinner } from "@/ds";

import { formatTimestamp, titleCase } from "./format";

export function HistoryPanel({ riskId, canViewAudit }: { riskId: number; canViewAudit: boolean }) {
  const audit = useAudit(riskId, canViewAudit);

  if (!canViewAudit) {
    return <EmptyState title="History not available">Audit history requires administrator access.</EmptyState>;
  }
  if (audit.isLoading) {
    return <Spinner label="Loading history" />;
  }
  if (audit.isError) {
    if (audit.error instanceof ApiError && audit.error.status === 403) {
      return <EmptyState title="History not available">Audit history requires administrator access.</EmptyState>;
    }
    return (
      <Alert intent="danger" role="alert">
        Could not load history.
      </Alert>
    );
  }

  const rows = audit.data?.results ?? [];
  if (rows.length === 0) {
    return <EmptyState title="No history yet" />;
  }

  return (
    <table className="ds-table ds-table--zebra">
      <thead>
        <tr>
          <th scope="col">When</th>
          <th scope="col">Action</th>
          <th scope="col">Actor</th>
          <th scope="col">Request</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>{formatTimestamp(row.timestamp)}</td>
            <td>{titleCase(String(row.action ?? ""))}</td>
            <td>
              {row.actor_type ? `${row.actor_type}` : "—"}
              {row.actor_id != null ? ` #${row.actor_id}` : ""}
            </td>
            <td>{row.request_id ? <span className="ds-code">{row.request_id}</span> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
