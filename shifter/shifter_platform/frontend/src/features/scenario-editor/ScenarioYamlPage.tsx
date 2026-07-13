import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Loader2 } from "lucide-react";

import {
  fetchScenarioExport,
  useCreateScenarioFromYaml,
  useScenario,
  useUpdateScenario,
  useValidateYaml,
} from "@/api/scenarios";
import { ApiError } from "@/api/errors";
import type { ScenarioUpdate } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

import { scenarioListPath, scenarioPath } from "./routes";

const STARTER_YAML = `id: my-new-scenario
name: My New Scenario
description: Describe your scenario here.
ngfw: false

instances:
  - name: Attacker
    role: attacker
    os_type: kali
    xdr_agent: false

  - name: Workstation
    role: victim
    os_type: from_agent
    xdr_agent: true

subnets:
  - name: core
    instances: [Attacker, Workstation]
`;

/** Build a structured update body from a validated YAML definition dict. */
function updateFromDefinition(definition: Record<string, unknown>): ScenarioUpdate {
  return {
    name: typeof definition.name === "string" ? definition.name : "",
    description: typeof definition.description === "string" ? definition.description : "",
    ngfw: Boolean(definition.ngfw),
    instances: (Array.isArray(definition.instances) ? definition.instances : []) as ScenarioUpdate["instances"],
    subnets: (Array.isArray(definition.subnets) ? definition.subnets : []) as ScenarioUpdate["subnets"],
  };
}

export function ScenarioYamlPage({ mode }: Readonly<{ mode: "create" | "edit" }>) {
  const navigate = useNavigate();
  const params = useParams();
  const scenarioId = params.scenarioId ?? "";

  const detail = useScenario(scenarioId, mode === "edit");
  const validate = useValidateYaml();
  const create = useCreateScenarioFromYaml();
  const update = useUpdateScenario(scenarioId);

  const [yamlText, setYamlText] = useState(mode === "create" ? STARTER_YAML : "");
  const [seeded, setSeeded] = useState(mode === "create");
  const [seedError, setSeedError] = useState(false);

  useEffect(() => {
    if (mode !== "edit" || seeded || !detail.data?.editable) return;
    let cancelled = false;
    fetchScenarioExport(scenarioId)
      .then((result) => {
        if (!cancelled) {
          setYamlText(result.yaml);
          setSeeded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setSeedError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, seeded, detail.data?.editable, scenarioId]);

  function onValidate() {
    validate.mutate(yamlText);
  }

  function onSave() {
    if (mode === "create") {
      create.mutate(yamlText, { onSuccess: (created) => navigate(scenarioPath(created.scenario_id)) });
      return;
    }
    validate.mutate(yamlText, {
      onSuccess: (result) => {
        if (result.valid && result.definition) {
          update.mutate(updateFromDefinition(result.definition), {
            onSuccess: () => navigate(scenarioPath(scenarioId)),
          });
        }
      },
    });
  }

  if (mode === "edit" && detail.isLoading) {
    return (
      <div className="grid place-items-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" aria-label="Loading scenario" />
      </div>
    );
  }
  if (mode === "edit" && detail.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Scenario not found</AlertTitle>
        <AlertDescription>
          <Link className="underline" to={scenarioListPath()}>
            Back to scenarios
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }
  if (mode === "edit" && detail.data && !detail.data.editable) {
    return (
      <Alert variant="destructive">
        <AlertTitle>This scenario cannot be edited here</AlertTitle>
        <AlertDescription>
          Built-in, ACES, and CTF scenarios are read-only in the editor.{" "}
          <Link className="underline" to={scenarioPath(scenarioId)}>
            Back to scenario
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  const mutation = mode === "create" ? create : update;
  const saveError = mutation.error instanceof ApiError ? mutation.error.message : null;
  const cancelHref = mode === "edit" ? scenarioPath(scenarioId) : scenarioListPath();
  const result = validate.data;

  return (
    <div className="mx-auto max-w-3xl">
      <nav className="mb-3 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link className="hover:text-foreground" to={scenarioListPath()}>
          Scenarios
        </Link>
        <span className="px-1.5">/</span>
        <span className="text-foreground">{mode === "create" ? "New scenario (YAML)" : "Edit YAML"}</span>
      </nav>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">
        {mode === "create" ? "New scenario (YAML)" : `Edit ${detail.data?.name ?? "scenario"} (YAML)`}
      </h1>

      {seedError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not load YAML</AlertTitle>
          <AlertDescription>Please retry.</AlertDescription>
        </Alert>
      ) : null}
      {saveError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not save</AlertTitle>
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      ) : null}
      {result && !result.valid ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Validation failed</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-5">
              {result.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      {result?.valid ? (
        <Alert className="mb-4">
          <AlertTitle>Valid scenario</AlertTitle>
          <AlertDescription>The YAML passed validation.</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardContent>
          <label htmlFor="yaml" className="mb-2 block text-sm font-medium">
            Scenario YAML
          </label>
          <Textarea
            id="yaml"
            className="min-h-[420px] font-mono text-sm"
            spellCheck={false}
            value={yamlText}
            disabled={mode === "edit" && !seeded}
            onChange={(event) => setYamlText(event.target.value)}
          />
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => navigate(cancelHref)}>
            Cancel
          </Button>
          <Button type="button" variant="outline" onClick={onValidate} disabled={validate.isPending}>
            {validate.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Validate
          </Button>
          <Button type="button" onClick={onSave} disabled={mutation.isPending || (mode === "edit" && !seeded)}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            {mode === "create" ? "Create scenario" : "Save changes"}
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
