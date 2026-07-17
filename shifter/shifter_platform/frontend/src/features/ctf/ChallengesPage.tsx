import { useMemo } from "react";
import { Link } from "react-router-dom";

import { CheckCircle2 } from "lucide-react";

import { useCtfChallenges } from "@/api/ctf";
import type { CtfChallengeListItem } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { titleCase } from "./format";
import { ctfChallengeDetailPath } from "./routes";

const UNCATEGORIZED = "Uncategorized";

/** Group challenges by category, preserving each challenge's server `order`. */
function groupByCategory(challenges: CtfChallengeListItem[]): Array<[string, CtfChallengeListItem[]]> {
  const groups = new Map<string, CtfChallengeListItem[]>();
  for (const challenge of challenges) {
    const key = challenge.category || UNCATEGORIZED;
    const bucket = groups.get(key) ?? [];
    bucket.push(challenge);
    groups.set(key, bucket);
  }
  for (const bucket of groups.values()) {
    bucket.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function ChallengeCard({ challenge }: Readonly<{ challenge: CtfChallengeListItem }>) {
  return (
    <Link
      to={ctfChallengeDetailPath(challenge.id)}
      className="block rounded-lg border border-border/60 p-4 transition-colors hover:border-border hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-medium">{challenge.name}</span>
        {challenge.solved ? (
          <Badge variant="secondary" className="gap-1">
            <CheckCircle2 className="size-3" aria-hidden="true" />
            Solved
          </Badge>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{challenge.points} pts</span>
        {challenge.difficulty ? <span>· {titleCase(challenge.difficulty)}</span> : null}
      </div>
    </Link>
  );
}

function ChallengesBody({ query }: Readonly<{ query: ReturnType<typeof useCtfChallenges> }>) {
  const grouped = useMemo(() => groupByCategory(query.data ?? []), [query.data]);

  if (query.isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((row) => (
          <Skeleton key={row} className="h-20 w-full" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load challenges</AlertTitle>
        <AlertDescription>Please retry.</AlertDescription>
      </Alert>
    );
  }

  if (grouped.length === 0) {
    return (
      <Card>
        <CardContent className="grid place-items-center px-6 py-16 text-center">
          <p className="text-sm font-medium">No challenges available yet</p>
          <p className="mt-1 text-sm text-muted-foreground">Challenges appear here once the event releases them.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-8">
      {grouped.map(([category, items]) => (
        <section key={category} aria-label={category}>
          <h2 className="mb-3 text-sm font-semibold">{titleCase(category)}</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((challenge) => (
              <ChallengeCard key={challenge.id} challenge={challenge} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function ChallengesPage() {
  const query = useCtfChallenges();
  const total = query.data?.length ?? 0;
  const solved = query.data?.filter((challenge) => challenge.solved).length ?? 0;

  return (
    <>
      <PageHeader
        title="Challenges"
        description={query.data ? `${solved} of ${total} solved` : "Browse available challenges"}
      />
      <ChallengesBody query={query} />
    </>
  );
}
