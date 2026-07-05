import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Loader2 } from "lucide-react";

import { useCreateRisk, useRisk, useUpdateRisk } from "@/api/risks";
import { ApiError } from "@/api/errors";
import { SEVERITIES, STATUSES, STRIDE_OPTIONS, strideList, type Risk } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { titleCase } from "./format";

interface FormState {
  title: string;
  description: string;
  severity: string;
  status: string;
  likelihood_score: string;
  impact_score: string;
  stride_categories: string[];
  attack_vector: string;
  affected_assets: string;
  mitigation_status: string;
  resolution_reason: string;
}

const EMPTY: FormState = {
  title: "",
  description: "",
  severity: "medium",
  status: "open",
  likelihood_score: "",
  impact_score: "",
  stride_categories: [],
  attack_vector: "",
  affected_assets: "",
  mitigation_status: "",
  resolution_reason: "",
};

function fromRisk(risk: Risk): FormState {
  return {
    title: risk.title ?? "",
    description: risk.description ?? "",
    severity: risk.severity ?? "medium",
    status: risk.status ?? "open",
    likelihood_score: risk.likelihood_score == null ? "" : String(risk.likelihood_score),
    impact_score: risk.impact_score == null ? "" : String(risk.impact_score),
    stride_categories: strideList(risk.stride_categories),
    attack_vector: risk.attack_vector ?? "",
    affected_assets: risk.affected_assets ?? "",
    mitigation_status: risk.mitigation_status ?? "",
    resolution_reason: risk.resolution_reason ?? "",
  };
}

function scoreOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function toPayload(state: FormState, includeResolution: boolean) {
  const base = {
    title: state.title,
    description: state.description,
    severity: state.severity as Risk["severity"],
    status: state.status as Risk["status"],
    likelihood_score: scoreOrNull(state.likelihood_score),
    impact_score: scoreOrNull(state.impact_score),
    stride_categories: state.stride_categories,
    attack_vector: state.attack_vector,
    affected_assets: state.affected_assets,
    mitigation_status: state.mitigation_status,
  };
  return includeResolution ? { ...base, resolution_reason: state.resolution_reason } : base;
}

function FieldError({ id, error }: Readonly<{ id: string; error?: string }>) {
  if (!error) return null;
  return (
    <p id={id} className="text-sm text-destructive">
      {error}
    </p>
  );
}

export function RiskFormPage({ mode }: Readonly<{ mode: "create" | "edit" }>) {
  const navigate = useNavigate();
  const params = useParams();
  const riskId = mode === "edit" ? Number(params.id) : undefined;

  const existing = useRisk(riskId ?? 0, false, mode === "edit");
  const create = useCreateRisk();
  const update = useUpdateRisk(riskId ?? 0);
  const mutation = mode === "create" ? create : update;

  const [state, setState] = useState<FormState>(EMPTY);
  const [initialized, setInitialized] = useState(mode === "create");
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (mode === "edit" && existing.data && !initialized) {
      setState(fromRisk(existing.data));
      setInitialized(true);
    }
  }, [mode, existing.data, initialized]);

  const fieldErrors = useMemo(
    () => (mutation.error instanceof ApiError ? mutation.error.fieldErrors() : {}),
    [mutation.error],
  );

  useEffect(() => {
    if (Object.keys(fieldErrors).length > 0) {
      formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
    }
  }, [fieldErrors]);

  const nonFieldError =
    mutation.error instanceof ApiError && Object.keys(fieldErrors).length === 0 ? mutation.error.message : null;

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setState((prev) => ({ ...prev, [key]: value }));
  }

  function firstError(field: string): string | undefined {
    return fieldErrors[field]?.[0];
  }

  function toggleStride(code: string, checked: boolean) {
    set("stride_categories", checked ? [...state.stride_categories, code] : state.stride_categories.filter((c) => c !== code));
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const payload = toPayload(state, mode === "edit");
    if (mode === "create") {
      create.mutate(payload, { onSuccess: (risk) => navigate(`/risks/${risk.id}`) });
    } else if (riskId !== undefined) {
      update.mutate(payload, { onSuccess: () => navigate(`/risks/${riskId}`) });
    }
  }

  if (mode === "edit" && existing.isLoading) {
    return (
      <div className="grid place-items-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" aria-label="Loading risk" />
      </div>
    );
  }
  if (mode === "edit" && existing.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Risk not found</AlertTitle>
        <AlertDescription>
          This risk may have been deleted.{" "}
          <Link className="underline" to="/">
            Back to risks
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  const cancelHref = mode === "edit" && riskId ? `/risks/${riskId}` : "/";

  return (
    <div className="mx-auto max-w-3xl">
      <nav className="mb-3 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link className="hover:text-foreground" to="/">
          Risks
        </Link>
        <span className="px-1.5">/</span>
        <span className="text-foreground">{mode === "create" ? "New risk" : "Edit"}</span>
      </nav>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">
        {mode === "create" ? "New risk" : `Edit ${existing.data?.title ?? "risk"}`}
      </h1>

      {nonFieldError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not save</AlertTitle>
          <AlertDescription>{nonFieldError}</AlertDescription>
        </Alert>
      ) : null}

      <form ref={formRef} onSubmit={onSubmit} noValidate>
        <Card>
          <CardContent className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="f-title">Title</Label>
              <Input
                id="f-title"
                value={state.title}
                aria-invalid={firstError("title") ? true : undefined}
                aria-describedby={firstError("title") ? "e-title" : undefined}
                onChange={(event) => set("title", event.target.value)}
              />
              <FieldError id="e-title" error={firstError("title")} />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="f-desc">Description</Label>
              <Textarea
                id="f-desc"
                rows={4}
                value={state.description}
                aria-invalid={firstError("description") ? true : undefined}
                aria-describedby={firstError("description") ? "e-desc" : undefined}
                onChange={(event) => set("description", event.target.value)}
              />
              <FieldError id="e-desc" error={firstError("description")} />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-sev">Severity</Label>
                <Select value={state.severity} onValueChange={(value) => set("severity", value)}>
                  <SelectTrigger id="f-sev" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SEVERITIES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {titleCase(value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-status">Status</Label>
                <Select value={state.status} onValueChange={(value) => set("status", value)}>
                  <SelectTrigger id="f-status" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {STATUSES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {titleCase(value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-like">Likelihood (1–5)</Label>
                <Input
                  id="f-like"
                  type="number"
                  min={1}
                  max={5}
                  value={state.likelihood_score}
                  aria-invalid={firstError("likelihood_score") ? true : undefined}
                  onChange={(event) => set("likelihood_score", event.target.value)}
                />
                <FieldError id="e-like" error={firstError("likelihood_score")} />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-impact">Impact (1–5)</Label>
                <Input
                  id="f-impact"
                  type="number"
                  min={1}
                  max={5}
                  value={state.impact_score}
                  aria-invalid={firstError("impact_score") ? true : undefined}
                  onChange={(event) => set("impact_score", event.target.value)}
                />
                <FieldError id="e-impact" error={firstError("impact_score")} />
              </div>
            </div>

            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-sm font-medium">STRIDE categories</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {STRIDE_OPTIONS.map((option) => (
                  <label key={option.code} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-input bg-transparent accent-primary"
                      checked={state.stride_categories.includes(option.code)}
                      onChange={(event) => toggleStride(option.code, event.target.checked)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
              <FieldError id="e-stride" error={firstError("stride_categories")} />
            </fieldset>

            <div className="flex flex-col gap-2">
              <Label htmlFor="f-attack">Attack vector</Label>
              <Textarea id="f-attack" rows={2} value={state.attack_vector} onChange={(event) => set("attack_vector", event.target.value)} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="f-assets">Affected assets</Label>
              <Textarea id="f-assets" rows={2} value={state.affected_assets} onChange={(event) => set("affected_assets", event.target.value)} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="f-mit">Mitigation status</Label>
              <Textarea id="f-mit" rows={2} value={state.mitigation_status} onChange={(event) => set("mitigation_status", event.target.value)} />
            </div>
            {mode === "edit" ? (
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-res">Resolution reason</Label>
                <Textarea id="f-res" rows={2} value={state.resolution_reason} onChange={(event) => set("resolution_reason", event.target.value)} />
              </div>
            ) : null}
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => navigate(cancelHref)}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              {mode === "create" ? "Create risk" : "Save changes"}
            </Button>
          </CardFooter>
        </Card>
      </form>
    </div>
  );
}
