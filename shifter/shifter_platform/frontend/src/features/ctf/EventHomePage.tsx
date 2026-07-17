import { Link } from "react-router-dom";

import { Flag, Server, Trophy, Users } from "lucide-react";

import { useCtfCurrentEvent } from "@/api/ctf";
import { ApiError } from "@/api/errors";
import type { CtfCurrentEvent } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { titleCase } from "./format";
import { ctfChallengesPath, ctfRangePath, ctfScoreboardPath, ctfTeamPath } from "./routes";

const QUICK_LINKS = [
  { to: ctfChallengesPath(), label: "Challenges", icon: Flag },
  { to: ctfScoreboardPath(), label: "Scoreboard", icon: Trophy },
  { to: ctfTeamPath(), label: "Team", icon: Users },
  { to: ctfRangePath(), label: "Range", icon: Server },
] as const;

function Stat({ label, value }: Readonly<{ label: string; value: string | number }>) {
  return (
    <div className="rounded-lg border border-border/60 p-4">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tracking-tight">{value}</dd>
    </div>
  );
}

function EventOverview({ data }: Readonly<{ data: CtfCurrentEvent }>) {
  const { event, participant } = data;
  return (
    <>
      <PageHeader
        title={event.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{titleCase(event.status)}</Badge>
            {participant.team ? <span>Team: {participant.team.name}</span> : <span>Solo</span>}
            {participant.bracket ? <span>· Bracket: {participant.bracket.name}</span> : null}
          </span>
        }
      />

      {event.description ? (
        <Card className="mb-6">
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{event.description}</p>
          </CardContent>
        </Card>
      ) : null}

      <dl className="mb-8 grid gap-4 sm:grid-cols-3">
        <Stat label="Score" value={participant.cached_score} />
        <Stat label="Solves" value={participant.cached_solve_count} />
        <Stat label="Status" value={titleCase(participant.status)} />
      </dl>

      <h2 className="mb-3 text-sm font-semibold">Quick links</h2>
      <nav aria-label="Workspace quick links" className="flex flex-wrap gap-2">
        {QUICK_LINKS.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            <Icon className="size-4" />
            {label}
          </Link>
        ))}
      </nav>
    </>
  );
}

export function EventHomePage() {
  const query = useCtfCurrentEvent();

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <div className="grid gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-24 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <>
        <PageHeader title="Event Home" />
        <Alert>
          <AlertTitle>No active event</AlertTitle>
          <AlertDescription>
            You are not currently enrolled in an active CTF event. When an organizer invites you, your event will appear
            here.
          </AlertDescription>
        </Alert>
      </>
    );
  }

  if (query.isError || !query.data) {
    return (
      <>
        <PageHeader title="Event Home" />
        <Alert variant="destructive">
          <AlertTitle>Could not load your event</AlertTitle>
          <AlertDescription>Please retry.</AlertDescription>
        </Alert>
      </>
    );
  }

  return <EventOverview data={query.data} />;
}
