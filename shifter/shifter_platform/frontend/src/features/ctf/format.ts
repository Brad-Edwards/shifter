/**
 * Small presentation helpers for the CTF participant workspace. UI affordances
 * only; the backend projections stay authoritative for the underlying values.
 */

/** Title-case a lowercase enum-ish token ("web" -> "Web", "not_assigned" -> "Not assigned"). */
export function titleCase(value: string): string {
  if (!value) return value;
  const spaced = value.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Format an ISO datetime for display, or a dash when absent/unparseable. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

/**
 * A scoreboard ranking row is an untyped `{[key: string]: unknown}` (the DRF
 * serializer documents it as a `DictField` list). These accessors coerce the
 * fields the participant board renders without trusting the runtime shape.
 */
export function rankingString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) return "";
  return String(value);
}

export function rankingNumber(row: Record<string, unknown>, key: string): number | null {
  const value = row[key];
  return typeof value === "number" ? value : null;
}

/** A stable React key for a ranking row, preferring its id fields. */
export function rankingKey(row: Record<string, unknown>, index: number): string {
  return rankingString(row, "participant_id") || rankingString(row, "team_id") || String(index);
}
