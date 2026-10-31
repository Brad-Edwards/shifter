import { useEffect, useState } from "react";

import { useChangeCtfUsername, useCtfProfile, useUpdateCtfProfile } from "@/api/ctf";
import { describeMutationError } from "@/api/errors";
import type { CtfParticipantProfile } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import { browserTimeZone } from "./admin/communications/time";

function MutationError({ error }: Readonly<{ error: unknown }>) {
  const message = describeMutationError(error, "That did not work. Try again.");
  if (!message) return null;
  return <p className="mt-2 text-xs text-destructive">{message}</p>;
}

/** IANA names the browser knows, for input suggestions; empty when unsupported. */
function supportedTimeZones(): string[] {
  return typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
}

/** Display name + affiliation + time zone form (CTF-610, #582). */
function ProfileForm({ profile }: Readonly<{ profile: CtfParticipantProfile }>) {
  const update = useUpdateCtfProfile();
  const [name, setName] = useState(profile.name);
  const [affiliation, setAffiliation] = useState(profile.affiliation);
  const [timeZone, setTimeZone] = useState(profile.time_zone);
  const [zones] = useState(supportedTimeZones);
  const browserZone = browserTimeZone();
  useEffect(() => {
    setName(profile.name);
    setAffiliation(profile.affiliation);
    setTimeZone(profile.time_zone);
  }, [profile.name, profile.affiliation, profile.time_zone]);

  const dirty =
    name.trim() !== profile.name ||
    affiliation.trim() !== profile.affiliation ||
    timeZone.trim() !== profile.time_zone;
  return (
    <Card>
      <CardContent>
        <h2 className="text-sm font-semibold">Profile</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Your display name appears on the scoreboard; affiliation is your team, region, or organization.
        </p>
        <form
          className="mt-3 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim() && dirty) {
              update.mutate({ name: name.trim(), affiliation: affiliation.trim(), time_zone: timeZone.trim() });
            }
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="account-name">Display name</Label>
            <Input id="account-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="account-affiliation">Affiliation</Label>
            <Input
              id="account-affiliation"
              value={affiliation}
              onChange={(event) => setAffiliation(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="account-time-zone">Time zone</Label>
            <div className="flex gap-2">
              <Input
                id="account-time-zone"
                list="account-time-zones"
                value={timeZone}
                placeholder="Event time zone"
                aria-describedby="account-time-zone-help"
                onChange={(event) => setTimeZone(event.target.value)}
              />
              {browserZone && browserZone !== timeZone ? (
                <Button type="button" variant="outline" onClick={() => setTimeZone(browserZone)}>
                  Use {browserZone}
                </Button>
              ) : null}
            </div>
            <datalist id="account-time-zones">
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            <p id="account-time-zone-help" className="text-xs text-muted-foreground">
              Reminder emails show the event start in this IANA time zone; blank uses the event time zone.
            </p>
          </div>
          <div>
            <Button type="submit" disabled={!name.trim() || !dirty || update.isPending}>
              Save profile
            </Button>
          </div>
        </form>
        <MutationError error={update.error} />
      </CardContent>
    </Card>
  );
}

/** Self-service login-handle change (#1593); isolated CTF accounts only. */
function UsernameForm({ profile }: Readonly<{ profile: CtfParticipantProfile }>) {
  const change = useChangeCtfUsername();
  const [username, setUsername] = useState(profile.username ?? "");
  useEffect(() => setUsername(profile.username ?? ""), [profile.username]);

  if (!profile.username) return null;
  return (
    <Card>
      <CardContent>
        <h2 className="text-sm font-semibold">Login username</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          You sign in with this handle. It must start with <code className="font-mono">range-</code> and stay
          globally unique; you will use the new handle at your next login.
        </p>
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (username.trim() && username.trim() !== profile.username) {
              change.mutate({ username: username.trim() });
            }
          }}
        >
          <div className="flex min-w-56 flex-1 flex-col gap-1">
            <Label htmlFor="account-username">Username</Label>
            <Input id="account-username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </div>
          <Button
            type="submit"
            variant="outline"
            disabled={!username.trim() || username.trim() === profile.username || change.isPending}
          >
            Change username
          </Button>
        </form>
        <MutationError error={change.error} />
      </CardContent>
    </Card>
  );
}

export function AccountPage() {
  const query = useCtfProfile();

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="Account" />
        <Skeleton className="h-40 w-full" />
      </>
    );
  }

  if (query.isError || !query.data) {
    return (
      <>
        <PageHeader title="Account" />
        <Alert variant="destructive">
          <AlertTitle>Could not load your account</AlertTitle>
          <AlertDescription>Please retry.</AlertDescription>
        </Alert>
      </>
    );
  }

  const profile = query.data;
  return (
    <>
      <PageHeader
        title="Account"
        description={`${profile.event.name} · ${profile.score} points · ${profile.solve_count} solves`}
      />
      <div className="space-y-4">
        {profile.role === "observer" ? (
          <Badge variant="secondary">Observer — you can watch this event but not submit flags</Badge>
        ) : null}
        <ProfileForm profile={profile} />
        <UsernameForm profile={profile} />
      </div>
    </>
  );
}
