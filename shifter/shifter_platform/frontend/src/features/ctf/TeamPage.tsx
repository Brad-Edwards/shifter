import { useCtfTeam } from "@/api/ctf";
import { ApiError } from "@/api/errors";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function TeamPage() {
  const query = useCtfTeam();

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="Team" />
        <Skeleton className="h-40 w-full" />
      </>
    );
  }

  // A 404 is the server's "not on a team" signal (solo events / unassigned).
  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <>
        <PageHeader title="Team" />
        <Card>
          <CardContent className="grid place-items-center px-6 py-16 text-center">
            <p className="text-sm font-medium">You are not on a team</p>
            <p className="mt-1 text-sm text-muted-foreground">
              This is a solo event, or you have not been assigned to a team yet.
            </p>
          </CardContent>
        </Card>
      </>
    );
  }

  if (query.isError || !query.data) {
    return (
      <>
        <PageHeader title="Team" />
        <Alert variant="destructive">
          <AlertTitle>Could not load your team</AlertTitle>
          <AlertDescription>Please retry.</AlertDescription>
        </Alert>
      </>
    );
  }

  const team = query.data;
  const count = team.members.length;

  return (
    <>
      <PageHeader title={team.name} description={`${count} ${count === 1 ? "member" : "members"}`} />
      <Card>
        <CardContent>
          <ul className="divide-y divide-border/60">
            {team.members.map((member) => (
              <li key={member.id} className="py-2 text-sm">
                {member.name}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </>
  );
}
