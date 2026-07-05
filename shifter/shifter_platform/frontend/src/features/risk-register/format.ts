import type { Severity, Status } from "@/api/types";
import type { Intent } from "@/ds";

/** Risk severity -> design-system intent (solid badge). */
export function severityIntent(severity: Severity): Intent {
  switch (severity) {
    case "critical":
      return "danger";
    case "high":
      return "warning";
    case "medium":
      return "info";
    case "low":
      return "neutral";
    default:
      return "neutral";
  }
}

/** Risk status -> intent by meaning (active vs terminal), not color alone. */
export function statusIntent(status: Status): Intent {
  switch (status) {
    case "open":
      return "warning";
    case "acknowledged":
    case "mitigating":
      return "info";
    case "resolved":
      return "success";
    case "closed":
      return "neutral";
    default:
      return "neutral";
  }
}

export function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
