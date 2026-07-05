import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useCreateRisk, useRisk, useUpdateRisk } from "@/api/risks";
import { ApiError } from "@/api/errors";
import { SEVERITIES, STATUSES, STRIDE_OPTIONS, strideList, type Risk } from "@/api/types";
import { Alert, Breadcrumb, Button, CheckboxGroup, PageHeader, SelectField, Spinner, TextAreaField, TextField } from "@/ds";

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

const FIELD_GAP = "var(--ds-space-4)";

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

  const fieldErrors = useMemo(() => {
    return mutation.error instanceof ApiError ? mutation.error.fieldErrors() : {};
  }, [mutation.error]);

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
      <div className="ds-empty">
        <Spinner label="Loading risk" />
      </div>
    );
  }
  if (mode === "edit" && existing.isError) {
    return (
      <Alert intent="danger" role="alert" title="Risk not found">
        This risk may have been deleted. Return to the risk list.
      </Alert>
    );
  }

  const heading = mode === "create" ? "New risk" : `Edit ${existing.data?.title ?? "risk"}`;

  return (
    <>
      <Breadcrumb
        items={[
          { label: "Govern" },
          { label: "Risks", to: "/" },
          ...(mode === "edit" && riskId ? [{ label: existing.data?.title ?? "Risk", to: `/risks/${riskId}` }] : []),
          { label: mode === "create" ? "New" : "Edit" },
        ]}
      />
      <PageHeader title={heading} />

      {nonFieldError ? (
        <Alert intent="danger" role="alert" title="Could not save">
          {nonFieldError}
        </Alert>
      ) : null}

      <form ref={formRef} onSubmit={onSubmit} noValidate className="ds-card">
        <div className="ds-card__body" style={{ display: "grid", gap: FIELD_GAP, maxInlineSize: "48rem" }}>
          <TextField
            label="Title"
            required
            value={state.title}
            error={firstError("title")}
            onChange={(event) => set("title", event.target.value)}
          />
          <TextAreaField
            label="Description"
            required
            rows={4}
            value={state.description}
            error={firstError("description")}
            onChange={(event) => set("description", event.target.value)}
          />
          <div style={{ display: "flex", gap: FIELD_GAP, flexWrap: "wrap" }}>
            <SelectField
              label="Severity"
              value={state.severity}
              error={firstError("severity")}
              onChange={(event) => set("severity", event.target.value)}
            >
              {SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </SelectField>
            <SelectField
              label="Status"
              value={state.status}
              error={firstError("status")}
              onChange={(event) => set("status", event.target.value)}
            >
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </SelectField>
          </div>
          <div style={{ display: "flex", gap: FIELD_GAP, flexWrap: "wrap" }}>
            <TextField
              label="Likelihood (1–5)"
              type="number"
              min={1}
              max={5}
              value={state.likelihood_score}
              error={firstError("likelihood_score")}
              onChange={(event) => set("likelihood_score", event.target.value)}
            />
            <TextField
              label="Impact (1–5)"
              type="number"
              min={1}
              max={5}
              value={state.impact_score}
              error={firstError("impact_score")}
              onChange={(event) => set("impact_score", event.target.value)}
            />
          </div>
          <CheckboxGroup
            legend="STRIDE categories"
            options={STRIDE_OPTIONS.map((option) => ({ value: option.code, label: option.label }))}
            selected={state.stride_categories}
            onChange={(next) => set("stride_categories", next)}
            error={firstError("stride_categories")}
          />
          <TextAreaField
            label="Attack vector"
            rows={2}
            value={state.attack_vector}
            error={firstError("attack_vector")}
            onChange={(event) => set("attack_vector", event.target.value)}
          />
          <TextAreaField
            label="Affected assets"
            rows={2}
            value={state.affected_assets}
            error={firstError("affected_assets")}
            onChange={(event) => set("affected_assets", event.target.value)}
          />
          <TextAreaField
            label="Mitigation status"
            rows={2}
            value={state.mitigation_status}
            error={firstError("mitigation_status")}
            onChange={(event) => set("mitigation_status", event.target.value)}
          />
          {mode === "edit" ? (
            <TextAreaField
              label="Resolution reason"
              rows={2}
              value={state.resolution_reason}
              error={firstError("resolution_reason")}
              onChange={(event) => set("resolution_reason", event.target.value)}
            />
          ) : null}
        </div>
        <div className="ds-card__footer">
          <Button variant="secondary" onClick={() => navigate(mode === "edit" && riskId ? `/risks/${riskId}` : "/")}>
            Cancel
          </Button>
          <Button type="submit" loading={mutation.isPending}>
            {mode === "create" ? "Create risk" : "Save changes"}
          </Button>
        </div>
      </form>
    </>
  );
}
