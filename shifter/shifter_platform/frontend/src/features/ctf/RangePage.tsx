import { useCtfRangeStatus, useRangeAccess, useVpnProfileDownload } from "@/api/ctf";
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

function VpnProfileDownload({ status }: Readonly<{ status: CtfRangeStatus }>) {
  const download = useVpnProfileDownload();
  const error = describeMutationError(download.error, "Could not download the VPN profile.");

  if (status.status !== READY || !status.vpn_profile_available) return null;

  return (
    <div className="mt-3">
      <Button variant="secondary" onClick={() => download.mutate()} disabled={download.isPending}>
        {download.isPending ? "Downloading…" : "Download VPN profile"}
      </Button>
      <p className="mt-2 text-sm text-muted-foreground">
        This file is a private credential. Import it into a standard OpenVPN client and store it securely.
      </p>
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
          {status.range_instance_id === null ? null : (
            <p className="mt-2 text-sm text-muted-foreground">Range instance #{status.range_instance_id}</p>
          )}
          <RangeAccess status={status} />
          <VpnProfileDownload status={status} />
        </CardContent>
      </Card>
    </>
  );
}
