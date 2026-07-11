import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/errors";
import { useAgents, useLaunchRange, useScenarios } from "@/api/mission-control";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import { missionControlDashboardPath, missionControlHistoryPath } from "./routes";

interface FormErrors {
  scenario?: string;
  agent?: string;
}

function FieldError({ id, message }: Readonly<{ id: string; message?: string }>) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="text-sm text-destructive">
      {message}
    </p>
  );
}

/**
 * Launch-a-range form (#1370). Mirrors the legacy `static/js/dashboard.js`
 * launch flow but only the simple single-agent path
 * (`LaunchRangeSerializer.agent_id`): every scenario returned by
 * `useScenarios()` is already launchable (the backend's
 * `list_launchable_scenarios` filters that), and the SPA does not yet
 * replicate the legacy from-agent/Windows/Linux OS-map UI, so a scenario plus
 * one agent is always sufficient here.
 */
export function RangeLaunchPage() {
  const navigate = useNavigate();
  const scenarios = useScenarios();
  const agents = useAgents();
  const launch = useLaunchRange();

  const [scenarioId, setScenarioId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});

  const scenarioList = scenarios.data?.scenarios ?? [];
  const agentList = agents.data?.agents ?? [];
  const selectedScenario = scenarioList.find((scenario) => scenario.id === scenarioId);

  const optionsLoading = scenarios.isLoading || agents.isLoading;
  const optionsError = scenarios.isError || agents.isError;

  const serverError =
    launch.error instanceof ApiError
      ? launch.error.message
      : launch.error
        ? "The range could not be launched."
        : null;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const nextErrors: FormErrors = {};
    if (!scenarioId) nextErrors.scenario = "Select a scenario to launch.";
    if (!agentId) nextErrors.agent = "Select an agent to launch.";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    launch.mutate(
      { scenario: scenarioId, agent_id: Number(agentId) },
      { onSuccess: () => navigate(missionControlDashboardPath()) },
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <nav className="mb-3 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link className="hover:text-foreground" to={missionControlHistoryPath()}>
          Ranges
        </Link>
        <span className="px-1.5">/</span>
        <span className="text-foreground">Launch</span>
      </nav>

      <PageHeader title="Launch a range" description="Choose a scenario and an agent to start a new range." />

      {serverError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not launch this range</AlertTitle>
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      ) : null}

      {optionsError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not load launch options</AlertTitle>
          <AlertDescription>Scenarios or agents failed to load. Please retry.</AlertDescription>
        </Alert>
      ) : optionsLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-40" />
        </div>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <Card>
            <CardContent className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <Label htmlFor="f-scenario">Scenario</Label>
                <Select value={scenarioId} onValueChange={setScenarioId}>
                  <SelectTrigger
                    id="f-scenario"
                    className="w-full"
                    aria-invalid={errors.scenario ? true : undefined}
                    aria-describedby={errors.scenario ? "f-scenario-e" : undefined}
                  >
                    <SelectValue placeholder="Select a scenario" />
                  </SelectTrigger>
                  <SelectContent>
                    {scenarioList.map((scenario) => (
                      <SelectItem key={scenario.id} value={scenario.id}>
                        {scenario.name || scenario.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedScenario?.description ? (
                  <p className="text-sm text-muted-foreground">{selectedScenario.description}</p>
                ) : null}
                <FieldError id="f-scenario-e" message={errors.scenario} />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="f-agent">Agent</Label>
                <Select value={agentId} onValueChange={setAgentId}>
                  <SelectTrigger
                    id="f-agent"
                    className="w-full"
                    aria-invalid={errors.agent ? true : undefined}
                    aria-describedby={errors.agent ? "f-agent-e" : undefined}
                  >
                    <SelectValue placeholder="Select an agent" />
                  </SelectTrigger>
                  <SelectContent>
                    {agentList.map((agent) => (
                      <SelectItem key={agent.id} value={String(agent.id)}>
                        {agent.name} ({agent.os_name})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FieldError id="f-agent-e" message={errors.agent} />
              </div>
            </CardContent>
            <CardFooter className="justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => navigate(missionControlDashboardPath())}>
                Cancel
              </Button>
              <Button type="submit" disabled={launch.isPending}>
                {launch.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
                {launch.isPending ? "Launching…" : "Launch range"}
              </Button>
            </CardFooter>
          </Card>
        </form>
      )}
    </div>
  );
}
