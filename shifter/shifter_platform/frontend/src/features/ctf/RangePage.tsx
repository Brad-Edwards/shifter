import { useCtfRangeStatus, useRangeAccess } from "@/api/ctf";
import { describeMutationError } from "@/api/errors";
import type { CtfRangeStatus } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { titleCase } from "./format";

const READY = "ready";

function RangeAccess({ status }: Readonly<{ status: CtfRangeStatus }>) {
  const access = useRangeAccess();
  const error = describeMutationError(access.error, "Could not start range access.");

  if (status.status !== READY) {
    return (
      <p className="mt-4 text-sm text-muted-foreground">
        Range access becomes available once your range is ready.
      </p>
    );
  }

  return (
    <div className="mt-4">
      <Button
        onClick={() =>
          access.mutate(undefined, {
            onSuccess: (result) => {
              if (result.redirect) globalThis.location.assign(result.redirect);
            },
          })
        }
        disabled={access.isPending}
      >
        {access.isPending ? "Connecting…" : "Access range"}
      </Button>
      {access.data ? <p className="mt-2 text-sm text-muted-foreground">{access.data.message}</p> : null}
      {error ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

export function RangePage() {
  const query = useCtfRangeStatus();

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="Range" />
        <Skeleton className="h-40 w-full" />
      </>
    );
  }

  if (query.isError || !query.data) {
    return (
      <>
        <PageHeader title="Range" />
        <Alert variant="destructive">
          <AlertTitle>Could not load range status</AlertTitle>
          <AlertDescription>Please retry.</AlertDescription>
        </Alert>
      </>
    );
  }

  const status = query.data;

  return (
    <>
      <PageHeader title="Range" description="Your dedicated range for this event" />
      <Card>
        <CardContent>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Status</span>
            <Badge variant="secondary">{titleCase(status.status)}</Badge>
          </div>
          {status.range_instance_id !== null ? (
            <p className="mt-2 text-sm text-muted-foreground">Range instance #{status.range_instance_id}</p>
          ) : null}
          <RangeAccess status={status} />
        </CardContent>
      </Card>
    </>
  );
}
