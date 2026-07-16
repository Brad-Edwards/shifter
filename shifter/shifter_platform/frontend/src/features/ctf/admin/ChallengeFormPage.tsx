import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Loader2, Trash2 } from "lucide-react";

import {
  useAddCtfFlag,
  useAddCtfHint,
  useAddCtfPrerequisite,
  useCreateCtfChallenge,
  useCtfChallengeFiles,
  useCtfChallengeHints,
  useCtfChallengePrerequisites,
  useCtfOrganizerChallenge,
  useDeleteCtfChallengeFile,
  useDeleteCtfHint,
  useDeleteCtfPrerequisite,
  useRemoveCtfFlag,
  useUpdateCtfChallenge,
  useUploadCtfChallengeFile,
} from "@/api/ctf";
import { ApiError, describeMutationError } from "@/api/errors";
import {
  CTF_CHALLENGE_CATEGORIES,
  CTF_CHALLENGE_DIFFICULTIES,
  type CtfChallengeWrite,
  type CtfOrganizerChallengeDetail,
} from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import { titleCase } from "../format";
import { ctfAdminChallengePath, ctfAdminEventChallengesPath, ctfAdminEventsPath } from "../routes";
import { CheckboxField, SelectField, TextAreaField, TextField } from "./form-fields";

const VISIBILITY_OPTIONS = ["visible", "hidden", "locked"] as const;

interface FormState {
  name: string;
  description: string;
  category: string;
  difficulty: string;
  points: string;
  order: string;
  max_attempts: string;
  flag_format: string;
  solution: string;
  visibility: string;
  target_instance_name: string;
  target_port: string;
  tags: string;
  topics: string;
  flag: string;
}

const EMPTY: FormState = {
  name: "",
  description: "",
  category: "web",
  difficulty: "easy",
  points: "100",
  order: "0",
  max_attempts: "0",
  flag_format: "",
  solution: "",
  visibility: "visible",
  target_instance_name: "",
  target_port: "",
  tags: "",
  topics: "",
  flag: "",
};

function fromChallenge(challenge: CtfOrganizerChallengeDetail): FormState {
  return {
    name: challenge.name ?? "",
    description: challenge.description ?? "",
    category: challenge.category || "web",
    difficulty: challenge.difficulty || "easy",
    points: String(challenge.points ?? 0),
    order: String(challenge.order ?? 0),
    max_attempts: String(challenge.max_attempts ?? 0),
    flag_format: challenge.flag_format ?? "",
    solution: challenge.solution ?? "",
    visibility: challenge.visibility || "visible",
    target_instance_name: challenge.target_instance_name ?? "",
    target_port: challenge.target_port == null ? "" : String(challenge.target_port),
    tags: (challenge.tags ?? []).join(", "),
    topics: (challenge.topics ?? []).join(", "),
    flag: "",
  };
}

function intOr(value: string, fallback: number): number {
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : fallback;
}

function csvToList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function toPayload(state: FormState, mode: "create" | "edit"): CtfChallengeWrite {
  const port = state.target_port.trim();
  const base: CtfChallengeWrite = {
    name: state.name,
    description: state.description,
    category: state.category,
    difficulty: state.difficulty,
    points: intOr(state.points, 0),
    order: intOr(state.order, 0),
    max_attempts: intOr(state.max_attempts, 0),
    flag_format: state.flag_format,
    solution: state.solution,
    visibility: state.visibility,
    target_instance_name: state.target_instance_name,
    target_port: port === "" ? null : intOr(port, 0),
    tags: csvToList(state.tags),
    topics: csvToList(state.topics),
  };
  // The flag is set on create; in edit mode flags are managed in their own
  // section (the SPA never resends the plaintext flag on an update).
  return mode === "create" && state.flag.trim() ? { ...base, flag: state.flag.trim() } : base;
}

function BasicFields({
  state,
  set,
  firstError,
  mode,
}: Readonly<{
  state: FormState;
  set: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
  firstError: (field: string) => string | undefined;
  mode: "create" | "edit";
}>) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-5">
        <TextField id="c-name" label="Name" value={state.name} error={firstError("name")} onChange={(v) => set("name", v)} />
        <TextAreaField
          id="c-desc"
          label="Description"
          rows={4}
          value={state.description}
          error={firstError("description")}
          onChange={(v) => set("description", v)}
        />
        <div className="grid gap-5 sm:grid-cols-2">
          <SelectField
            id="c-cat"
            label="Category"
            value={state.category}
            error={firstError("category")}
            options={CTF_CHALLENGE_CATEGORIES}
            labelFor={titleCase}
            onChange={(v) => set("category", v)}
          />
          <SelectField
            id="c-diff"
            label="Difficulty"
            value={state.difficulty}
            error={firstError("difficulty")}
            options={CTF_CHALLENGE_DIFFICULTIES}
            labelFor={titleCase}
            onChange={(v) => set("difficulty", v)}
          />
        </div>
        <div className="grid gap-5 sm:grid-cols-3">
          <TextField id="c-points" label="Points" type="number" min={0} value={state.points} error={firstError("points")} onChange={(v) => set("points", v)} />
          <TextField id="c-order" label="Order" type="number" min={0} value={state.order} error={firstError("order")} onChange={(v) => set("order", v)} />
          <TextField
            id="c-attempts"
            label="Max attempts (0 = ∞)"
            type="number"
            min={0}
            value={state.max_attempts}
            error={firstError("max_attempts")}
            onChange={(v) => set("max_attempts", v)}
          />
        </div>
        <TextField
          id="c-format"
          label="Flag format (hint shown to participants)"
          value={state.flag_format}
          placeholder="FLAG{...}"
          error={firstError("flag_format")}
          onChange={(v) => set("flag_format", v)}
        />
        {mode === "create" ? (
          <TextField
            id="c-flag"
            label="Flag"
            value={state.flag}
            placeholder="FLAG{the_answer}"
            error={firstError("flag")}
            onChange={(v) => set("flag", v)}
          />
        ) : null}
        <div className="grid gap-5 sm:grid-cols-2">
          <TextField
            id="c-target"
            label="Target instance (optional)"
            value={state.target_instance_name}
            error={firstError("target_instance_name")}
            onChange={(v) => set("target_instance_name", v)}
          />
          <TextField
            id="c-port"
            label="Target port (optional)"
            type="number"
            min={0}
            value={state.target_port}
            error={firstError("target_port")}
            onChange={(v) => set("target_port", v)}
          />
        </div>
        <SelectField
          id="c-vis"
          label="Visibility"
          value={state.visibility}
          error={firstError("visibility")}
          options={VISIBILITY_OPTIONS}
          labelFor={titleCase}
          onChange={(v) => set("visibility", v)}
        />
        <div className="grid gap-5 sm:grid-cols-2">
          <TextField id="c-tags" label="Tags (comma separated)" value={state.tags} onChange={(v) => set("tags", v)} />
          <TextField id="c-topics" label="Topics (comma separated)" value={state.topics} onChange={(v) => set("topics", v)} />
        </div>
        <TextAreaField
          id="c-solution"
          label="Solution / writeup (organizer-only)"
          rows={3}
          value={state.solution}
          error={firstError("solution")}
          onChange={(v) => set("solution", v)}
        />
      </CardContent>
    </Card>
  );
}

function SectionCard({ title, children }: Readonly<{ title: string; children: React.ReactNode }>) {
  return (
    <Card>
      <CardContent>
        <h2 className="mb-4 text-sm font-semibold">{title}</h2>
        {children}
      </CardContent>
    </Card>
  );
}

function FlagsSection({ challengeId }: Readonly<{ challengeId: string }>) {
  const add = useAddCtfFlag(challengeId);
  const remove = useRemoveCtfFlag(challengeId);
  const [flag, setFlag] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(true);
  // No flags list endpoint exists (flags are secret); track this session's adds
  // so they can be removed without exposing stored flag values.
  const [added, setAdded] = useState<Array<{ id: string; flag_type: string; order: number }>>([]);
  const error = describeMutationError(add.error ?? remove.error, "Could not update flags.");

  return (
    <SectionCard title="Flags">
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!flag.trim()) return;
          add.mutate(
            { flag: flag.trim(), flag_type: "static", case_sensitive: caseSensitive, order: added.length },
            {
              onSuccess: (result) => {
                setAdded((prev) => [...prev, { id: result.id, flag_type: result.flag_type, order: result.order }]);
                setFlag("");
              },
            },
          );
        }}
      >
        <Label htmlFor="flag-add">Add a flag</Label>
        <div className="flex gap-2">
          <Input id="flag-add" value={flag} autoComplete="off" placeholder="FLAG{...}" onChange={(e) => setFlag(e.target.value)} />
          <Button type="submit" disabled={add.isPending || !flag.trim()}>
            Add
          </Button>
        </div>
        <CheckboxField id="flag-cs" label="Case sensitive" checked={caseSensitive} onChange={setCaseSensitive} />
      </form>
      {added.length > 0 ? (
        <ul className="mt-4 divide-y divide-border/60">
          {added.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="text-muted-foreground">
                Flag added ({titleCase(entry.flag_type)}, order {entry.order})
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(entry.id, {
                    onSuccess: () => setAdded((prev) => prev.filter((f) => f.id !== entry.id)),
                  })
                }
              >
                <Trash2 className="size-4" aria-hidden="true" />
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">Stored flags are hidden. Flags you add this session appear here.</p>
      )}
      {error ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </SectionCard>
  );
}

function HintsSection({ challengeId }: Readonly<{ challengeId: string }>) {
  const query = useCtfChallengeHints(challengeId);
  const add = useAddCtfHint(challengeId);
  const remove = useDeleteCtfHint(challengeId);
  const [text, setText] = useState("");
  const [penalty, setPenalty] = useState("0");
  const error = describeMutationError(add.error ?? remove.error, "Could not update hints.");
  const hints = query.data?.hints ?? [];

  return (
    <SectionCard title="Hints">
      {query.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : hints.length === 0 ? (
        <p className="text-sm text-muted-foreground">No hints yet.</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {hints.map((hint) => (
            <li key={hint.id} className="flex items-start justify-between gap-3 py-2">
              <span className="min-w-0 text-sm">
                <span className="block whitespace-pre-wrap">{hint.text}</span>
                <span className="text-xs text-muted-foreground">−{hint.penalty} pts</span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(hint.id)}
                aria-label={`Delete hint ${hint.order + 1}`}
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-4 flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!text.trim()) return;
          add.mutate(
            { text: text.trim(), penalty: intOr(penalty, 0), order: hints.length },
            { onSuccess: () => setText("") },
          );
        }}
      >
        <div className="flex flex-col gap-2">
          <Label htmlFor="hint-text">Add a hint</Label>
          <Input id="hint-text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Hint text" />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="hint-penalty">Penalty</Label>
            <Input id="hint-penalty" type="number" min={0} value={penalty} onChange={(e) => setPenalty(e.target.value)} className="w-28" />
          </div>
          <Button type="submit" disabled={add.isPending || !text.trim()}>
            Add hint
          </Button>
        </div>
      </form>
      {error ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </SectionCard>
  );
}

function FilesSection({ challengeId }: Readonly<{ challengeId: string }>) {
  const query = useCtfChallengeFiles(challengeId);
  const upload = useUploadCtfChallengeFile(challengeId);
  const remove = useDeleteCtfChallengeFile(challengeId);
  const [displayName, setDisplayName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const error = describeMutationError(upload.error ?? remove.error, "Could not update files.");
  const files = query.data?.files ?? [];

  return (
    <SectionCard title="Files">
      {query.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : files.length === 0 ? (
        <p className="text-sm text-muted-foreground">No attachments yet.</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {files.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0 text-sm">
                <span className="block truncate font-medium">{file.display_name || file.filename}</span>
                <span className="text-xs text-muted-foreground">{file.file_size_display}</span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(file.id)}
                aria-label={`Delete ${file.display_name || file.filename}`}
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-4 flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const file = fileRef.current?.files?.[0];
          if (!file) return;
          upload.mutate(
            { file, displayName: displayName.trim() || undefined },
            {
              onSuccess: () => {
                setDisplayName("");
                if (fileRef.current) fileRef.current.value = "";
              },
            },
          );
        }}
      >
        <div className="flex flex-col gap-2">
          <Label htmlFor="file-input">Upload an attachment</Label>
          <Input id="file-input" type="file" ref={fileRef} />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex flex-1 flex-col gap-2">
            <Label htmlFor="file-name">Display name (optional)</Label>
            <Input id="file-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <Button type="submit" disabled={upload.isPending}>
            {upload.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Upload
          </Button>
        </div>
      </form>
      {error ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </SectionCard>
  );
}

function PrerequisitesSection({ challengeId }: Readonly<{ challengeId: string }>) {
  const query = useCtfChallengePrerequisites(challengeId);
  const add = useAddCtfPrerequisite(challengeId);
  const remove = useDeleteCtfPrerequisite(challengeId);
  const [requiredId, setRequiredId] = useState("");
  const error = describeMutationError(add.error ?? remove.error, "Could not update prerequisites.");
  const prerequisites = query.data?.prerequisites ?? [];

  return (
    <SectionCard title="Prerequisites">
      {query.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : prerequisites.length === 0 ? (
        <p className="text-sm text-muted-foreground">No prerequisites. This challenge unlocks immediately.</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {prerequisites.map((prereq) => (
            <li key={prereq.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0">
                <span className="block truncate font-medium">{prereq.required_challenge_name}</span>
                <span className="text-xs text-muted-foreground">{titleCase(prereq.required_challenge_category)}</span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(prereq.id)}
                aria-label={`Remove prerequisite ${prereq.required_challenge_name}`}
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-4 flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!requiredId.trim()) return;
          add.mutate({ required_challenge_id: requiredId.trim() }, { onSuccess: () => setRequiredId("") });
        }}
      >
        <div className="flex flex-1 flex-col gap-2">
          <Label htmlFor="prereq-id">Required challenge ID</Label>
          <Input id="prereq-id" value={requiredId} onChange={(e) => setRequiredId(e.target.value)} placeholder="UUID" />
        </div>
        <Button type="submit" disabled={add.isPending || !requiredId.trim()}>
          Add
        </Button>
      </form>
      {error ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </SectionCard>
  );
}

export function ChallengeFormPage({ mode }: Readonly<{ mode: "create" | "edit" }>) {
  const navigate = useNavigate();
  const params = useParams();
  const eventId = params.eventId ?? "";
  const challengeId = mode === "edit" ? (params.challengeId ?? "") : "";

  const existing = useCtfOrganizerChallenge(challengeId, mode === "edit");
  const create = useCreateCtfChallenge(eventId);
  const update = useUpdateCtfChallenge(challengeId);
  const mutation = mode === "create" ? create : update;

  const [state, setState] = useState<FormState>(EMPTY);
  const [initialized, setInitialized] = useState(mode === "create");
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (mode === "edit" && existing.data && !initialized) {
      setState(fromChallenge(existing.data));
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
    event.preventDefault();
    const payload = toPayload(state, mode);
    if (mode === "create") {
      create.mutate(payload, { onSuccess: (result) => navigate(ctfAdminChallengePath(result.id)) });
    } else if (challengeId) {
      update.mutate(payload, { onSuccess: () => navigate(ctfAdminChallengePath(challengeId)) });
    }
  }

  if (mode === "edit" && existing.isLoading) {
    return (
      <div className="grid place-items-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" aria-label="Loading challenge" />
      </div>
    );
  }
  if (mode === "edit" && existing.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Challenge not found</AlertTitle>
        <AlertDescription>
          This challenge may have been deleted.{" "}
          <Link className="underline" to={ctfAdminEventsPath()}>
            Back to events
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  const cancelHref =
    mode === "edit" && challengeId ? ctfAdminChallengePath(challengeId) : ctfAdminEventChallengesPath(eventId);

  return (
    <div className="mx-auto max-w-3xl">
      <nav className="mb-3 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link className="hover:text-foreground" to={ctfAdminEventsPath()}>
          Events
        </Link>
        <span className="px-1.5">/</span>
        <span className="text-foreground">{mode === "create" ? "New challenge" : "Edit challenge"}</span>
      </nav>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">
        {mode === "create" ? "New challenge" : `Edit ${existing.data?.name ?? "challenge"}`}
      </h1>

      {nonFieldError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not save</AlertTitle>
          <AlertDescription>{nonFieldError}</AlertDescription>
        </Alert>
      ) : null}

      <form ref={formRef} onSubmit={onSubmit} noValidate>
        <BasicFields state={state} set={set} firstError={firstError} mode={mode} />
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => navigate(cancelHref)}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            {mode === "create" ? "Create challenge" : "Save changes"}
          </Button>
        </div>
      </form>

      {mode === "edit" && challengeId ? (
        <div className="mt-8 space-y-6">
          <FlagsSection challengeId={challengeId} />
          <HintsSection challengeId={challengeId} />
          <FilesSection challengeId={challengeId} />
          <PrerequisitesSection challengeId={challengeId} />
        </div>
      ) : (
        <p className="mt-6 text-sm text-muted-foreground">
          Save the challenge to manage its flags, hints, files, and prerequisites.
        </p>
      )}
    </div>
  );
}
