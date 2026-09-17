import { useState } from "react";

import { useAdapters, useSetAdapterState, type Adapter, type AdapterAction } from "@/api/adapters";
import { describeMutationError } from "@/api/errors";
import { useAdministrableOrganizations } from "@/api/organization";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { AdapterInstallForm } from "./AdapterInstallForm";
import { AdapterPackBindings } from "./AdapterPackBindings";
import { manifestPreview } from "./adapter-manifest";

export function AdaptersPage() {
  const organizations = useAdministrableOrganizations();
  const [selected, setSelected] = useState("");
  const rows = organizations.data?.results ?? [];
  const organization = rows.find((row) => row.uuid === selected)?.uuid ?? rows[0]?.uuid ?? "";
  const error = describeMutationError(organizations.error, "Organizations could not be loaded.");
  return <>
    <PageHeader title="Adapters" description="Install and manage your organization's runtime plugins." />
    {organizations.isPending ? <p role="status">Loading organizations…</p> : null}
    {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
    {organizations.isSuccess && rows.length === 0 ? <p>You need organization administrator access to install plugins.</p> : null}
    {rows.length > 0 ? <div className="mb-6 space-y-2">
      <Label htmlFor="plugin-organization">Organization</Label>
      <select id="plugin-organization" value={organization} onChange={(event) => setSelected(event.target.value)}
        className="block rounded border bg-background p-2">
        {rows.map((row) => <option key={row.uuid} value={row.uuid}>{row.name}</option>)}
      </select>
    </div> : null}
    {organization ? <InstalledPlugins key={organization} organization={organization} /> : null}
  </>;
}

function InstalledPlugins({ organization }: Readonly<{ organization: string }>) {
  const [showPacks, setShowPacks] = useState(false);
  const query = useAdapters(organization);
  const update = useSetAdapterState(organization);
  const [action, setAction] = useState<{ adapter: Adapter; kind: AdapterAction } | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const error = describeMutationError(query.error, "Plugins could not be loaded.");
  const label = action ? action.kind[0].toUpperCase() + action.kind.slice(1) : "Update";
  function choose(adapter: Adapter, kind: AdapterAction) {
    update.reset(); setUsername(""); setPassword(""); setAction({ adapter, kind });
  }
  return <>
    {query.isPending ? <p role="status">Loading plugins…</p> : null}
    {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
    {query.isSuccess ? <>
      <AdapterInstallForm organization={organization} />
      <Button variant="outline" className="mb-4" onClick={() => setShowPacks(!showPacks)} aria-expanded={showPacks}>
        {showPacks ? "Hide packs" : "Install packs and assign adapters"}
      </Button>
      {showPacks ? <AdapterPackBindings organization={organization} adapters={query.data} /> : null}
      <h2 className="mb-3 text-lg font-semibold">Installed versions</h2>
      {query.data.length === 0 ? <p>No plugins are installed.</p> : <Table>
        <TableHeader><TableRow><TableHead>Plugin</TableHead><TableHead>Version</TableHead>
          <TableHead>Status</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader>
        <TableBody>{query.data.map((adapter) => {
          const manifest = manifestPreview(adapter.manifest);
          return <TableRow key={adapter.id}>
            <TableCell>{manifest?.plugin_id ?? "Unrecognized manifest"}</TableCell>
            <TableCell>{manifest?.version ?? "Unavailable"}</TableCell>
            <TableCell><Badge variant="outline">{adapter.state === "checking" ? "Checking compatibility" : adapter.state}</Badge>
              {adapter.state === "failed" ? <p>Installation failed. Check the package and registry sign-in, then retry.</p> : null}
            </TableCell>
            <TableCell><div className="flex gap-2">
              {adapter.state === "retired" ? <span>Retained for existing ranges</span> : <>
                {adapter.state === "failed" ? <Button variant="outline" disabled={update.isPending}
                  onClick={() => choose(adapter, "retry")}>Retry installation</Button> : null}
                <Button variant="outline" disabled={update.isPending}
                  onClick={() => choose(adapter, adapter.state === "disabled" ? "enable" : "disable")}>
                  {adapter.state === "disabled" ? "Enable" : "Disable"}</Button>
                <Button variant="outline" disabled={update.isPending} onClick={() => choose(adapter, "retire")}>Retire</Button>
              </>}
            </div></TableCell>
          </TableRow>;
        })}</TableBody>
      </Table>}
    </> : null}
    <ConfirmDialog open={action !== null} onOpenChange={(open) => { if (!open && !update.isPending) setAction(null); }}
      title={`${label} this plugin version?`} confirmLabel={`${label} plugin`}
      destructive={action?.kind === "retire"} pending={update.isPending} error={update.error}
      confirmDisabled={Boolean(username) !== Boolean(password)}
      onConfirm={() => { if (action && Boolean(username) === Boolean(password)) update.mutate({
        id: action.adapter.id, action: action.kind,
        ...(username && password ? { registry_credentials: { username, password } } : {}),
      }, { onSuccess: () => { setAction(null); setUsername(""); setPassword(""); } }); }}
      content={action?.kind === "retry" ? <div className="mt-3 space-y-2">
        <p>Leave these fields empty to keep the existing registry sign-in.</p>
        <Label htmlFor="retry-username">Registry username</Label>
        <Input id="retry-username" value={username} disabled={update.isPending} onChange={(event) => setUsername(event.target.value)} autoComplete="off" />
        <Label htmlFor="retry-password">Registry password or access token</Label>
        <Input id="retry-password" type="password" value={password} disabled={update.isPending} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" />
        {Boolean(username) !== Boolean(password) ? <p>Enter both fields to change registry sign-in.</p> : null}
      </div> : null}>
      {action?.kind === "enable" || action?.kind === "retry"
        ? "Shifter will run compatibility checks before enabling this version."
        : "New range launches cannot use this version. Existing range and cleanup references are retained."}
      {action?.kind === "retire" ? " Retirement cannot be reversed." : ""}
    </ConfirmDialog>
  </>;
}
