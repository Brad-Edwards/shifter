import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Loader2 } from "lucide-react";

import { useCreateCtfEvent, useCtfEvent, useCtfScenarios, useUpdateCtfEvent } from "@/api/ctfAdmin";
import { ApiError } from "@/api/errors";
import type { CtfEventDetail, CtfEventWrite } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { fromDateTimeLocalValue, toDateTimeLocalValue } from "../format";
import { ctfAdminEventPath, ctfAdminEventsPath } from "../routes";
import { CheckboxField, FieldError, TextAreaField, TextField } from "./form-fields";

const NO_SCENARIO = "__none__";

interface FormState {
  name: string;
  description: string;
  event_start: string;
  event_end: string;
  registration_deadline: string;
  scenario_id: string;
  team_mode: boolean;
  team_size_limit: string;
  max_participants: string;
  range_spinup_minutes: string;
  submission_cooldown_seconds: string;
  attempt_limit_mode: string;
  attempt_limit_cooldown_seconds: string;
  auto_cleanup: boolean;
  cleanup_delay_hours: string;
  scoreboard_visible: boolean;
}

const EMPTY: FormState = {
  name: "",
  description: "",
  event_start: "",
  event_end: "",
  registration_deadline: "",
  scenario_id: "",
  team_mode: false,
  team_size_limit: "",
  max_participants: "",
  range_spinup_minutes: "30",
  submission_cooldown_seconds: "0",
  attempt_limit_mode: "lockout",
  attempt_limit_cooldown_seconds: "300",
  auto_cleanup: true,
  cleanup_delay_hours: "24",
  scoreboard_visible: true,
};

function fromEvent(event: CtfEventDetail): FormState {
  return {
    name: event.name ?? "",
    description: event.description ?? "",
    event_start: toDateTimeLocalValue(event.event_start),
    event_end: toDateTimeLocalValue(event.event_end),
    registration_deadline: toDateTimeLocalValue(event.registration_deadline),
    scenario_id: event.scenario_id ?? "",
    team_mode: Boolean(event.team_mode),
    team_size_limit: event.team_size_limit == null ? "" : String(event.team_size_limit),
    max_participants: event.max_participants == null ? "" : String(event.max_participants),
    range_spinup_minutes: String(event.range_spinup_minutes ?? 30),
    submission_cooldown_seconds: String(event.submission_cooldown_seconds ?? 0),
    attempt_limit_mode: event.attempt_limit_mode || "lockout",
    attempt_limit_cooldown_seconds: String(event.attempt_limit_cooldown_seconds ?? 300),
    auto_cleanup: Boolean(event.auto_cleanup),
    cleanup_delay_hours: String(event.cleanup_delay_hours ?? 24),
    scoreboard_visible: Boolean(event.scoreboard_visible),
  };
}

function intOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function intOr(value: string, fallback: number): number {
  return intOrNull(value) ?? fallback;
}

function toPayload(state: FormState): CtfEventWrite {
  return {
    name: state.name,
    description: state.description,
    event_start: fromDateTimeLocalValue(state.event_start) ?? "",
    event_end: fromDateTimeLocalValue(state.event_end) ?? "",
    registration_deadline: fromDateTimeLocalValue(state.registration_deadline),
    scenario_id: state.scenario_id,
    team_mode: state.team_mode,
    team_size_limit: intOrNull(state.team_size_limit),
    max_participants: intOrNull(state.max_participants),
    range_spinup_minutes: intOr(state.range_spinup_minutes, 30),
    submission_cooldown_seconds: intOr(state.submission_cooldown_seconds, 0),
    attempt_limit_mode: state.attempt_limit_mode,
    attempt_limit_cooldown_seconds: intOr(state.attempt_limit_cooldown_seconds, 300),
    auto_cleanup: state.auto_cleanup,
    cleanup_delay_hours: intOr(state.cleanup_delay_hours, 24),
    scoreboard_visible: state.scoreboard_visible,
  };
}

/** Render the edit-mode loading / not-found states, or null when the form itself should render. */
function renderEditLoadState(
  mode: "create" | "edit",
  existing: ReturnType<typeof useCtfEvent>,
): React.ReactNode {
  if (mode !== "edit") return null;
  if (existing.isLoading) {
    return (
      <div className="grid place-items-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" aria-label="Loading event" />
      </div>
    );
  }
  if (existing.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Event not found</AlertTitle>
        <AlertDescription>
          This event may have been deleted.{" "}
          <Link className="underline" to={ctfAdminEventsPath()}>
            Back to events
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }
  return null;
}

function submitEventForm(
  event: React.FormEvent,
  opts: Readonly<{
    state: FormState;
    mode: "create" | "edit";
    eventId: string;
    create: ReturnType<typeof useCreateCtfEvent>;
    update: ReturnType<typeof useUpdateCtfEvent>;
    navigate: ReturnType<typeof useNavigate>;
  }>,
) {
  event.preventDefault();
  const payload = toPayload(opts.state);
  if (opts.mode === "create") {
    opts.create.mutate(payload, { onSuccess: (result) => opts.navigate(ctfAdminEventPath(result.id)) });
  } else if (opts.eventId) {
    opts.update.mutate(payload, { onSuccess: () => opts.navigate(ctfAdminEventPath(opts.eventId)) });
  }
}

export function EventFormPage({ mode }: Readonly<{ mode: "create" | "edit" }>) {
  const navigate = useNavigate();
  const params = useParams();
  const eventId = mode === "edit" ? (params.eventId ?? "") : "";

  const existing = useCtfEvent(eventId, mode === "edit");
  const scenarios = useCtfScenarios();
  const create = useCreateCtfEvent();
  const update = useUpdateCtfEvent(eventId);
  const mutation = mode === "create" ? create : update;

  const [state, setState] = useState<FormState>(EMPTY);
  const [initialized, setInitialized] = useState(mode === "create");
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (mode === "edit" && existing.data && !initialized) {
      setState(fromEvent(existing.data));
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

  function onSubmit(event: React.FormEvent) {
    submitEventForm(event, { state, mode, eventId, create, update, navigate });
  }

  const editLoadState = renderEditLoadState(mode, existing);
  if (editLoadState) return <>{editLoadState}</>;

  const cancelHref = mode === "edit" && eventId ? ctfAdminEventPath(eventId) : ctfAdminEventsPath();
  const scenarioOptions = scenarios.data?.scenarios ?? [];

  return (
    <div className="mx-auto max-w-3xl">
      <nav className="mb-3 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link className="hover:text-foreground" to={ctfAdminEventsPath()}>
          Events
        </Link>
        <span className="px-1.5">/</span>
        <span className="text-foreground">{mode === "create" ? "New event" : "Edit"}</span>
      </nav>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">
        {mode === "create" ? "New event" : `Edit ${existing.data?.name ?? "event"}`}
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
            <TextField id="e-name" label="Name" value={state.name} error={firstError("name")} onChange={(v) => set("name", v)} />
            <TextAreaField
              id="e-desc"
              label="Description"
              rows={3}
              value={state.description}
              error={firstError("description")}
              onChange={(v) => set("description", v)}
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <TextField
                id="e-start"
                label="Event start"
                type="datetime-local"
                value={state.event_start}
                error={firstError("event_start")}
                onChange={(v) => set("event_start", v)}
              />
              <TextField
                id="e-end"
                label="Event end"
                type="datetime-local"
                value={state.event_end}
                error={firstError("event_end")}
                onChange={(v) => set("event_end", v)}
              />
            </div>

            <TextField
              id="e-regdeadline"
              label="Registration deadline (optional)"
              type="datetime-local"
              value={state.registration_deadline}
              error={firstError("registration_deadline")}
              onChange={(v) => set("registration_deadline", v)}
            />

            <div className="flex flex-col gap-2">
              <Label htmlFor="e-scenario">Scenario</Label>
              <Select
                value={state.scenario_id === "" ? NO_SCENARIO : state.scenario_id}
                onValueChange={(v) => set("scenario_id", v === NO_SCENARIO ? "" : v)}
              >
                <SelectTrigger
                  id="e-scenario"
                  className="w-full"
                  aria-invalid={firstError("scenario_id") ? true : undefined}
                >
                  <SelectValue placeholder="Select a scenario" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_SCENARIO}>No scenario</SelectItem>
                  {scenarioOptions.map((scenario) => (
                    <SelectItem key={scenario.id} value={scenario.id}>
                      {scenario.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldError id="e-scenario-e" error={firstError("scenario_id")} />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <TextField
                id="e-maxpart"
                label="Max participants (optional)"
                type="number"
                min={1}
                value={state.max_participants}
                error={firstError("max_participants")}
                onChange={(v) => set("max_participants", v)}
              />
              <TextField
                id="e-spinup"
                label="Range spin-up (minutes)"
                type="number"
                min={0}
                value={state.range_spinup_minutes}
                error={firstError("range_spinup_minutes")}
                onChange={(v) => set("range_spinup_minutes", v)}
              />
            </div>

            <fieldset className="flex flex-col gap-3">
              <legend className="mb-1 text-sm font-medium">Submission limits</legend>
              <div className="grid gap-5 sm:grid-cols-2">
                <TextField
                  id="e-subcooldown"
                  label="Cooldown between submissions (seconds)"
                  type="number"
                  min={0}
                  value={state.submission_cooldown_seconds}
                  error={firstError("submission_cooldown_seconds")}
                  onChange={(v) => set("submission_cooldown_seconds", v)}
                />
                <div className="flex flex-col gap-2">
                  <Label htmlFor="e-attemptmode">When max attempts is reached</Label>
                  <Select value={state.attempt_limit_mode} onValueChange={(v) => set("attempt_limit_mode", v)}>
                    <SelectTrigger id="e-attemptmode" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="lockout">Lock out permanently</SelectItem>
                      <SelectItem value="timeout">Time out, then allow retries</SelectItem>
                    </SelectContent>
                  </Select>
                  <FieldError id="e-attemptmode-e" error={firstError("attempt_limit_mode")} />
                </div>
              </div>
              {state.attempt_limit_mode === "timeout" ? (
                <TextField
                  id="e-attemptcooldown"
                  label="Attempt-limit timeout (seconds)"
                  type="number"
                  min={1}
                  value={state.attempt_limit_cooldown_seconds}
                  error={firstError("attempt_limit_cooldown_seconds")}
                  onChange={(v) => set("attempt_limit_cooldown_seconds", v)}
                />
              ) : null}
            </fieldset>

            <fieldset className="flex flex-col gap-3">
              <legend className="mb-1 text-sm font-medium">Options</legend>
              <CheckboxField id="e-team" label="Team mode" checked={state.team_mode} onChange={(c) => set("team_mode", c)} />
              {state.team_mode ? (
                <TextField
                  id="e-teamsize"
                  label="Team size limit (optional)"
                  type="number"
                  min={1}
                  value={state.team_size_limit}
                  error={firstError("team_size_limit")}
                  onChange={(v) => set("team_size_limit", v)}
                />
              ) : null}
              <CheckboxField
                id="e-scoreboard"
                label="Scoreboard visible to participants"
                checked={state.scoreboard_visible}
                onChange={(c) => set("scoreboard_visible", c)}
              />
              <CheckboxField
                id="e-cleanup"
                label="Auto-clean up ranges after the event"
                checked={state.auto_cleanup}
                onChange={(c) => set("auto_cleanup", c)}
              />
            </fieldset>
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => navigate(cancelHref)}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              {mode === "create" ? "Create event" : "Save changes"}
            </Button>
          </CardFooter>
        </Card>
      </form>
    </div>
  );
}
