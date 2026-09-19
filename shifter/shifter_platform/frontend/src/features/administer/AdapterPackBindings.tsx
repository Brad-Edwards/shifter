import { useState } from "react";

import {
  useAdapterPackDetail,
  useAdapterPacks,
  useBindAdapterPack,
  type AdapterPack,
  type AdapterPackDetail,
  type AdapterTargetImageProfile,
} from "@/api/adapter-packs";
import type { Adapter } from "@/api/adapters";
import { describeMutationError } from "@/api/errors";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { manifestPreview } from "./adapter-manifest";
import { PackUploadForm } from "./PackUploadForm";

function bindingStatus(pack: AdapterPack): string {
  if (!pack.binding) return "No adapter assigned";
  if (pack.binding.pack_digest !== pack.pack_digest) return "Pack updated — assign this version before launching";
  if (!pack.binding.enabled) return "Disabled — new launches blocked";
  if (pack.binding.installation_state !== "ready") return "Adapter unavailable — new launches blocked";
  return "Adapter assigned";
}

export function AdapterPackBindings({ organization, adapters }: Readonly<{ organization: string; adapters: Adapter[] }>) {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState("");
  const [saved, setSaved] = useState(false);
  const packs = useAdapterPacks(organization, page);
  const detail = useAdapterPackDetail(organization, selected);
  const error = describeMutationError(packs.error ?? detail.error, "Pack details could not be loaded.");
  return <section className="my-6 space-y-4" aria-label="Pack adapter assignments">
    <h2 className="text-lg font-semibold">Pack assignments</h2>
    <p>Choose an installed adapter and match its required targets to guests in your pack. Existing ranges retain their original assignment.</p>
    {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
    {packs.isPending ? <output className="block">Loading packs…</output> : null}
    {saved ? <output className="block">Assignment saved. Existing ranges keep their original adapter.</output> : null}
    {packs.data ? <>
      <PackUploadForm organization={organization} packs={packs.data.results} />
      {packs.data.results.length === 0 ? <p>No accessible packs are registered.</p> : <Table>
        <TableHeader><TableRow><TableHead>Pack</TableHead><TableHead>Assignment</TableHead><TableHead>Action</TableHead></TableRow></TableHeader>
        <TableBody>{packs.data.results.map((pack) => <TableRow key={pack.id}>
          <TableCell>{pack.name}</TableCell><TableCell>{bindingStatus(pack)}</TableCell>
          <TableCell><Button variant="outline" onClick={() => { setSelected(pack.id); setSaved(false); }}>Configure {pack.name}</Button></TableCell>
        </TableRow>)}</TableBody>
      </Table>}
      <div className="flex gap-2">
        <Button variant="outline" disabled={!packs.data.previous} onClick={() => setPage(page - 1)}>Previous packs</Button>
        <Button variant="outline" disabled={!packs.data.next} onClick={() => setPage(page + 1)}>Next packs</Button>
      </div>
    </> : null}
    {selected && detail.isPending ? <output className="block">Checking pack contents and guest targets…</output> : null}
    {selected && detail.data ? <PackAssignmentForm key={`${selected}:${detail.data.pack_digest}`} organization={organization}
      pack={detail.data} adapters={adapters} onSaved={() => { setSelected(""); setSaved(true); }} /> : null}
  </section>;
}

function declaredNames(value: unknown, key: string): string[] {
  if (typeof value !== "object" || value === null) return [];
  const names = (value as Record<string, unknown>)[key];
  return Array.isArray(names) && names.every((name) => typeof name === "string") ? names : [];
}

const emptyImageProfile = (): AdapterTargetImageProfile => ({
  provider: "gcp",
  image_kind: "image",
  image_ref: "",
  machine_type: "",
  disk_size_gb: null,
  disk_type: "",
  bootstrap_capability: "standard",
  management_ssh_username: "",
  management_ssh_port: 22,
  participant_container_name: "",
  participant_username: "",
  participant_readiness_contract: "",
  participant_readiness_manifest_sha256: "",
});

function validImageProfile(profile: AdapterTargetImageProfile): boolean {
  if (!profile.image_ref || profile.management_ssh_port < 1 || profile.management_ssh_port > 65535) return false;
  if (profile.provider === "aws") return profile.image_kind === "image" && /^ami-(?:[0-9a-f]{8}|[0-9a-f]{17})$/.test(profile.image_ref);
  if (profile.image_kind === "image") return profile.bootstrap_capability === "standard";
  return profile.bootstrap_capability === "preconfigured-machine-host"
    && Boolean(profile.management_ssh_username && profile.participant_container_name && profile.participant_username)
    && profile.participant_readiness_contract === "participant-readiness/v1"
    && /^[0-9a-f]{64}$/.test(profile.participant_readiness_manifest_sha256);
}

function PackAssignmentForm({ organization, pack, adapters, onSaved }: Readonly<{
  organization: string; pack: AdapterPackDetail; adapters: Adapter[]; onSaved: () => void;
}>) {
  const [installation, setInstallation] = useState(pack.binding?.installation_id ?? "");
  const [targets, setTargets] = useState<Record<string, string>>(pack.binding?.bindings.targets ?? {});
  const [parameters, setParameters] = useState<Record<string, string>>(pack.binding?.bindings.parameters ?? {});
  const [imageProfiles, setImageProfiles] = useState<Record<string, AdapterTargetImageProfile>>(
    pack.binding?.bindings.image_profiles ?? {},
  );
  const [enabled, setEnabled] = useState(pack.binding?.enabled ?? true);
  const [confirm, setConfirm] = useState(false);
  const save = useBindAdapterPack(organization, pack.id);
  const adapter = adapters.find((row) => row.id === installation);
  const requiredTargets = declaredNames(adapter?.manifest, "required_bindings");
  const requiredParameters = declaredNames(adapter?.manifest, "required_parameters");
  const targetChoices = new Set(pack.targets.map((row) => row.address));
  const valid = adapter && (!enabled || adapter.state === "ready") && requiredTargets.length > 0
    && requiredTargets.every((name) => targetChoices.has(targets[name]))
    && requiredParameters.every((name) => parameters[name] !== undefined)
    && Object.entries(imageProfiles).every(([name, profile]) => requiredTargets.includes(name) && validImageProfile(profile));
  const manifest = manifestPreview(adapter?.manifest);
  return <div className="space-y-4 rounded border p-4">
    <h3 className="font-semibold">Assign an adapter to {pack.name}</h3>
    <Label htmlFor="pack-adapter">Installed adapter version</Label>
    <select id="pack-adapter" className="block w-full rounded border bg-background p-2" value={installation}
      disabled={save.isPending} onChange={(event) => { setInstallation(event.target.value); setTargets({}); setParameters({}); save.reset(); }}>
      <option value="">Choose an adapter</option>
      {adapters.map((row) => { const preview = manifestPreview(row.manifest); return <option key={row.id} value={row.id}
        disabled={row.state !== "ready" && row.id !== pack.binding?.installation_id}>
        {preview?.plugin_id ?? "Unknown plugin"} · {preview?.version} ({row.state})
      </option>; })}
    </select>
    {requiredTargets.map((name) => <div key={name} className="space-y-2">
      <Label htmlFor={`plugin-target-${name}`}>Guest for {name}</Label>
      <select id={`plugin-target-${name}`} className="block w-full rounded border bg-background p-2" value={targets[name] ?? ""}
        disabled={save.isPending} onChange={(event) => setTargets({ ...targets, [name]: event.target.value })}>
        <option value="">Choose a pack guest</option>
        {pack.targets.map((target) => <option key={target.address} value={target.address}>{target.address} ({target.os_family})</option>)}
      </select>
      <TargetImageProfile name={name} value={imageProfiles[name]}
        onChange={(profile) => setImageProfiles((current) => {
          if (profile === null) {
            const next = { ...current }; delete next[name]; return next;
          }
          return { ...current, [name]: profile };
        })} />
    </div>)}
    {Object.entries(manifest?.model_bindings ?? {}).map(([role, binding]) => <p key={role}>
      Model access for {role} will be delivered to {targets[binding] || `the guest selected for ${binding}`}.
      The deployment’s model policy and budget still apply.
    </p>)}
    {requiredParameters.length ? <p>Enter the adapter configuration values. Passwords, access tokens and private keys are not supported here.</p> : null}
    {requiredParameters.map((name) => <div key={name} className="space-y-2">
      <Label htmlFor={`plugin-parameter-${name}`}>{name}</Label>
      <Input id={`plugin-parameter-${name}`} maxLength={8192} value={parameters[name] ?? ""} disabled={save.isPending}
        onChange={(event) => setParameters({ ...parameters, [name]: event.target.value })} />
    </div>)}
    <div className="flex items-center gap-2">
      <input id="pack-adapter-enabled" type="checkbox" checked={enabled} disabled={save.isPending}
        onChange={(event) => setEnabled(event.target.checked)} />
      <Label htmlFor="pack-adapter-enabled">Allow new launches with this assignment</Label>
    </div>
    <Button disabled={!valid || save.isPending} onClick={() => { save.reset(); setConfirm(true); }}>Review assignment</Button>
    <ConfirmDialog open={confirm} onOpenChange={(open) => { if (!save.isPending) setConfirm(open); }} title="Save this pack assignment?"
      confirmLabel="Save assignment" pending={save.isPending} error={save.error} confirmDisabled={!valid}
      onConfirm={() => save.mutate({ installation_id: installation, pack_digest: pack.pack_digest, enabled,
        bindings: { targets: Object.fromEntries(requiredTargets.map((name) => [name, targets[name]])),
          parameters: Object.fromEntries(requiredParameters.map((name) => [name, parameters[name]])),
          image_profiles: Object.fromEntries(requiredTargets.filter((name) => imageProfiles[name])
            .map((name) => [name, imageProfiles[name]])) },
      }, { onSuccess: () => { setConfirm(false); onSaved(); } })}>
      {enabled ? `${pack.name} will use ${manifest?.plugin_id} version ${manifest?.version} for new launches.`
        : `New launches of ${pack.name} will be blocked until its assignment is enabled.`} Existing ranges keep their original adapter and configuration.
    </ConfirmDialog>
  </div>;
}

function TargetImageProfile({ name, value, onChange }: Readonly<{
  name: string;
  value?: AdapterTargetImageProfile;
  onChange: (value: AdapterTargetImageProfile | null) => void;
}>) {
  const update = (patch: Partial<AdapterTargetImageProfile>) => onChange({ ...(value ?? emptyImageProfile()), ...patch });
  return <fieldset className="space-y-3 rounded border p-3">
    <div className="flex items-center gap-2">
      <input id={`plugin-image-enabled-${name}`} type="checkbox" checked={Boolean(value)}
        onChange={(event) => onChange(event.target.checked ? emptyImageProfile() : null)} />
      <Label htmlFor={`plugin-image-enabled-${name}`}>Use an administrator-selected provider image for {name}</Label>
    </div>
    {value ? <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor={`plugin-image-provider-${name}`}>Provider for {name}</Label>
        <select id={`plugin-image-provider-${name}`} value={value.provider} className="block w-full rounded border bg-background p-2"
          onChange={(event) => update({ provider: event.target.value as "gcp" | "aws", image_kind: "image",
            bootstrap_capability: "standard", participant_container_name: "", participant_username: "",
            participant_readiness_contract: "", participant_readiness_manifest_sha256: "" })}>
          <option value="gcp">Google Cloud</option><option value="aws">AWS</option>
        </select>
      </div>
      {value.provider === "gcp" ? <div className="space-y-1">
        <Label htmlFor={`plugin-image-kind-${name}`}>Image type for {name}</Label>
        <select id={`plugin-image-kind-${name}`} value={value.image_kind} className="block w-full rounded border bg-background p-2"
          onChange={(event) => { const machine = event.target.value === "machine-image"; update({
            image_kind: machine ? "machine-image" : "image",
            bootstrap_capability: machine ? "preconfigured-machine-host" : "standard",
            participant_readiness_contract: machine ? "participant-readiness/v1" : "",
            participant_container_name: "", participant_username: "", participant_readiness_manifest_sha256: "",
          }); }}>
          <option value="image">Boot image</option><option value="machine-image">Preconfigured machine host</option>
        </select>
      </div> : null}
      <ProfileInput id={`plugin-image-ref-${name}`} label={`Image reference for ${name}`} value={value.image_ref}
        onChange={(image_ref) => update({ image_ref })} />
      <ProfileInput id={`plugin-machine-type-${name}`} label={`Machine type for ${name}`} value={value.machine_type}
        onChange={(machine_type) => update({ machine_type })} />
      <ProfileInput id={`plugin-management-user-${name}`} label={`Management SSH username for ${name}`}
        value={value.management_ssh_username} onChange={(management_ssh_username) => update({ management_ssh_username })} />
      <div className="space-y-1"><Label htmlFor={`plugin-management-port-${name}`}>Management SSH port for {name}</Label>
        <Input id={`plugin-management-port-${name}`} type="number" min={1} max={65535} value={value.management_ssh_port}
          onChange={(event) => update({ management_ssh_port: Number(event.target.value) })} /></div>
      <div className="space-y-1"><Label htmlFor={`plugin-disk-size-${name}`}>Disk size (GB) for {name}</Label>
        <Input id={`plugin-disk-size-${name}`} type="number" min={1} value={value.disk_size_gb ?? ""}
          onChange={(event) => update({ disk_size_gb: event.target.value ? Number(event.target.value) : null })} /></div>
      <ProfileInput id={`plugin-disk-type-${name}`} label={`Disk type for ${name}`} value={value.disk_type}
        onChange={(disk_type) => update({ disk_type })} />
      {value.provider === "gcp" && value.image_kind === "machine-image" ? <>
        <ProfileInput id={`plugin-participant-container-${name}`} label={`Participant container for ${name}`}
          value={value.participant_container_name} onChange={(participant_container_name) => update({ participant_container_name })} />
        <ProfileInput id={`plugin-participant-user-${name}`} label={`Participant username for ${name}`}
          value={value.participant_username} onChange={(participant_username) => update({ participant_username })} />
        <ProfileInput id={`plugin-readiness-digest-${name}`} label={`Readiness manifest SHA-256 for ${name}`}
          value={value.participant_readiness_manifest_sha256}
          onChange={(participant_readiness_manifest_sha256) => update({ participant_readiness_manifest_sha256 })} />
      </> : null}
    </div> : null}
  </fieldset>;
}

function ProfileInput({ id, label, value, onChange }: Readonly<{
  id: string; label: string; value: string; onChange: (value: string) => void;
}>) {
  return <div className="space-y-1"><Label htmlFor={id}>{label}</Label>
    <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} />
  </div>;
}
