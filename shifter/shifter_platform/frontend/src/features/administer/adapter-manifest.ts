/** Display projection only. The server validates the complete immutable manifest. */
export interface AdapterManifestPreview {
  readonly plugin_id: string;
  readonly version: string;
  readonly protocol: string;
  readonly worker_image: string;
  readonly capabilities: string[];
}

export function manifestPreview(value: unknown): AdapterManifestPreview | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const textKeys = ["plugin_id", "version", "protocol", "worker_image"] as const;
  if (textKeys.some((key) => typeof record[key] !== "string" || !record[key])) return null;
  if (!Array.isArray(record.capabilities) || record.capabilities.some((item) => typeof item !== "string")) {
    return null;
  }
  return record as unknown as AdapterManifestPreview;
}
