import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type { components } from "./schema";

export type Adapter = components["schemas"]["RuntimePluginView"];
export type AdapterInstall = components["schemas"]["RuntimePluginInstall"];
export type AdapterAction = components["schemas"]["RuntimePluginAction"]["action"];
type Credentials = AdapterInstall["registry_credentials"];
const key = (organization: string) => ["installed-plugins", organization] as const;
const base = (organization: string) => `/cms/organizations/${organization}/plugins/`;

export function useAdapters(organization: string) {
  return useQuery({
    queryKey: key(organization), enabled: Boolean(organization),
    queryFn: ({ signal }) => apiFetch<Adapter[]>(base(organization), { signal }),
    refetchInterval: (query) => query.state.data?.some((row) => row.state === "checking") ? 3000 : false,
  });
}

export function useInstallAdapter(organization: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: AdapterInstall) => apiFetch<Adapter>(base(organization), { method: "POST", body }),
    onSuccess: () => client.invalidateQueries({ queryKey: key(organization) }),
  });
}

export function useSetAdapterState(organization: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, registry_credentials }: { id: string; action: AdapterAction; registry_credentials?: Credentials }) =>
      apiFetch<Adapter>(`${base(organization)}${id}/actions/`, { method: "POST", body: { action, registry_credentials } }),
    onSuccess: () => client.invalidateQueries({ queryKey: key(organization) }),
  });
}
