export function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Human label for the account-origin classification returned by the API. */
export function accountOriginLabel(origin: string): string {
  switch (origin) {
    case "provider":
      return "Provider";
    case "ctf":
      return "CTF";
    case "local":
      return "Local";
    default:
      return titleCase(origin);
  }
}
