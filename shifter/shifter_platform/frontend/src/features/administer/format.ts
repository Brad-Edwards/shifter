import { formatTimestamp, titleCase } from "@/lib/format";

export { formatTimestamp, titleCase };

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
