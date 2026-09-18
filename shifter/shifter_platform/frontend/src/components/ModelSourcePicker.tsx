import { useId } from "react";
import { useModelSourceOptions, type ModelSourceSelection } from "@/api/model-sources";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
  scenario: string;
  workspace?: string;
  purpose?: "range" | "ctf" | "admin";
  value: ModelSourceSelection;
  onChange: (value: ModelSourceSelection) => void;
  onWorkspaceChange?: (workspace: string) => void;
  disabled?: boolean;
}

/** Logical aliases stay stable; the selected sources fund new allocations. */
export function ModelSourcePicker({ scenario, workspace = "", purpose = "range", value, onChange, onWorkspaceChange, disabled }: Readonly<Props>) {
  const id = useId();
  const options = useModelSourceOptions(scenario, workspace, purpose);
  function setSources(alias: string, sources: ModelSourceSelection["aliases"][number]["sources"]) {
    onChange({ aliases: [...value.aliases.filter((item) => item.logical_alias !== alias),
      ...(sources.length ? [{ logical_alias: alias, sources }] : [])] });
  }
  if (options.isPending) return <p role="status">Loading model source choices…</p>;
  if (options.isError) return <div role="alert">Could not load model source choices. <Button type="button" variant="outline" onClick={() => options.refetch()}>Retry</Button></div>;
  return <fieldset disabled={disabled} className="space-y-4">
    <legend className="font-medium">Model sources</legend>
    {onWorkspaceChange && <div className="space-y-1">
      <Label htmlFor={`${id}-workspace`}>Workspace</Label>
      <select id={`${id}-workspace`} className="w-full rounded border bg-background p-2" value={workspace || options.data?.workspace || ""}
        onChange={(event) => { onWorkspaceChange(event.target.value); onChange({ aliases: [] }); }}>
        {!options.data?.workspace && <option value="">Personal workspace</option>}
        {options.data?.workspaces.map((item) => <option key={item.uuid} value={item.uuid}>{item.organization_name} / {item.name}</option>)}
      </select>
    </div>}
    {!options.data?.available && <p className="text-sm text-muted-foreground">Model access is not configured for this workspace.</p>}
    {options.data?.available && scenario && options.data.aliases.length === 0 && <p className="text-sm text-muted-foreground">This scenario has no model source choices.</p>}
    {options.data?.aliases.map((alias) => {
      const selected = value.aliases.find((item) => item.logical_alias === alias.logical_alias)?.sources ?? [];
      const stale = selected.some((choice) => !alias.sources.some((source) => source.id === choice.source_id && source.revision === choice.revision));
      return <fieldset key={alias.logical_alias} className="space-y-2 rounded border p-3">
        <legend className="px-1 font-medium">{alias.logical_alias}</legend>
        <p className="text-sm text-muted-foreground">{selected.length ? "Use the selected sources." : "Use the configured default."} {alias.multiple_allowed ? "Weights distribute new allocations; a running request stays on its assigned source." : "This scenario allows one source for this model."}</p>
        {stale && <p role="alert" className="text-sm text-destructive">A selected source changed or is unavailable. Clear it and select a current source before saving.</p>}
        {selected.length > 0 && <Button type="button" variant="outline" size="sm" onClick={() => setSources(alias.logical_alias, [])}>Use default</Button>}
        {alias.sources.length === 0 && <p className="text-sm">No compatible sources have been granted to you in this workspace.</p>}
        {alias.sources.map((source) => {
          const chosen = selected.find((item) => item.source_id === source.id);
          const config = source.configuration;
          const choiceId = `${id}-${alias.logical_alias}-${source.id}`;
          return <div key={source.id} className="rounded border p-2">
            <label htmlFor={choiceId} className="flex items-center gap-2 font-medium">
              <input id={choiceId} type="checkbox" checked={Boolean(chosen)} onChange={(event) => {
                const kept = alias.multiple_allowed ? selected.filter((item) => item.source_id !== source.id) : [];
                setSources(alias.logical_alias, event.target.checked ? [...kept, { source_id: source.id, revision: source.revision, weight: 1 }] : kept);
              }} />{config.name}
            </label>
            <p className="text-xs text-muted-foreground">{config.provider} · {config.model} · {config.region}</p>
            <p className="text-xs text-muted-foreground">Per million tokens: {config.currency} {config.input_price_per_million / 1_000_000} input / {config.output_price_per_million / 1_000_000} output</p>
            {chosen && alias.multiple_allowed && <div className="mt-2 flex items-center gap-2">
              <Label htmlFor={`${choiceId}-weight`}>Weight for {config.name}</Label>
              <Input id={`${choiceId}-weight`} type="number" min={1} max={64} className="w-20" value={chosen.weight ?? 1}
                onChange={(event) => { const weight = Number(event.target.value); if (Number.isInteger(weight) && weight >= 1 && weight <= 64) setSources(alias.logical_alias, selected.map((item) => item.source_id === source.id ? { ...item, weight } : item)); }} />
            </div>}
          </div>;
        })}
      </fieldset>;
    })}
  </fieldset>;
}
