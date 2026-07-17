import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { ReactNode } from "react";

import { useAdminUser, useGrantOrganizer, useSetUserActive, useSoftDeleteUser } from "@/api/administer";
import { ApiError } from "@/api/errors";
import type { AdminUserDetail } from "@/api/types";
import { useBootstrapContext } from "@/app/bootstrap-context";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { AccountOriginBadge, AccountStatusBadge, RoleBadge } from "./badges";
import { ConfirmDialog } from "./ConfirmDialog";
import { accountOriginLabel, formatTimestamp, titleCase } from "./format";
import { usersListPath } from "./routes";

type DialogKind = "set-active" | "delete" | "grant-organizer";

export function UserDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const query = useAdminUser(id, Number.isFinite(id));

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    const forbidden = query.error instanceof ApiError && query.error.status === 403;
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="space-y-4">
        <PageHeader title="User" />
        <Alert variant="destructive">
          <AlertTitle>
            {forbidden ? "You do not have permission to view this user" : notFound ? "User not found" : "Could not load user"}
          </AlertTitle>
          <AlertDescription>
            <Link className="underline" to={usersListPath()}>
              Back to users
            </Link>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <UserDetail user={query.data} />;
}

function UserDetail({ user }: Readonly<{ user: AdminUserDetail }>) {
  const bootstrap = useBootstrapContext();
  const [dialog, setDialog] = useState<DialogKind | null>(null);

  const setActive = useSetUserActive(user.id);
  const softDelete = useSoftDeleteUser(user.id);
  const grantOrganizer = useGrantOrganizer(user.id);

  const canChange = bootstrap.permissions.can_change_users;
  const canDelete = bootstrap.permissions.can_delete_users;
  const isSelf = bootstrap.principal.id === user.id;

  function close() {
    setActive.reset();
    softDelete.reset();
    grantOrganizer.reset();
    setDialog(null);
  }

  const showLifecycle = canChange && !user.is_deleted;

  return (
    <>
      <PageHeader
        title={user.display_name}
        description={user.email || user.username}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {showLifecycle ? (
              <Button
                variant="outline"
                size="sm"
                disabled={isSelf && user.is_active}
                title={isSelf && user.is_active ? "You cannot deactivate your own account." : undefined}
                onClick={() => setDialog("set-active")}
              >
                {user.is_active ? "Deactivate" : "Activate"}
              </Button>
            ) : null}
            {showLifecycle && !user.is_ctf_organizer ? (
              <Button variant="outline" size="sm" onClick={() => setDialog("grant-organizer")}>
                Grant CTF Organizer
              </Button>
            ) : null}
            {canDelete && !user.is_deleted ? (
              <Button
                variant="destructive"
                size="sm"
                disabled={isSelf}
                title={isSelf ? "You cannot delete your own account." : undefined}
                onClick={() => setDialog("delete")}
              >
                Delete
              </Button>
            ) : null}
          </div>
        }
      />

      {user.is_deleted ? (
        <Alert className="mb-4">
          <AlertTitle>This account is deleted</AlertTitle>
          <AlertDescription>The account has been soft-deleted and can no longer sign in.</AlertDescription>
        </Alert>
      ) : null}

      <Card className="p-6">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
          <Field label="Username">{user.username}</Field>
          <Field label="Email">{user.email || "—"}</Field>
          <Field label="Status">
            <AccountStatusBadge isActive={user.is_active} isDeleted={user.is_deleted} />
          </Field>
          <Field label="Account origin">
            <AccountOriginBadge origin={user.account_origin} />
          </Field>
          <Field label="Account type">{titleCase(user.user_type.replace(/_/g, " "))}</Field>
          <Field label="Roles">
            <div className="flex flex-wrap gap-1.5">
              {user.is_superuser ? <RoleBadge label="Superuser" /> : null}
              {user.is_staff ? <RoleBadge label="Staff" /> : null}
              {user.is_ctf_organizer ? <RoleBadge label="Organizer" /> : null}
              {!user.is_superuser && !user.is_staff && !user.is_ctf_organizer ? (
                <span className="text-sm text-muted-foreground">None</span>
              ) : null}
            </div>
          </Field>
          <Field label="Groups">
            {user.groups.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {user.groups.map((group) => (
                  <RoleBadge key={group} label={group} />
                ))}
              </div>
            ) : (
              <span className="text-sm text-muted-foreground">None</span>
            )}
          </Field>
          {user.is_ctf_organizer ? (
            <Field label="Organizer grant source">
              {user.organizer_grant_source ? accountOriginLabel(user.organizer_grant_source) : "—"}
            </Field>
          ) : null}
          <Field label="Joined">{formatTimestamp(user.date_joined)}</Field>
          <Field label="Last login">{formatTimestamp(user.last_login)}</Field>
        </dl>
      </Card>

      <ConfirmDialog
        open={dialog === "set-active"}
        title={user.is_active ? "Deactivate account?" : "Activate account?"}
        confirmLabel={user.is_active ? "Deactivate" : "Activate"}
        destructive={user.is_active}
        pending={setActive.isPending}
        error={setActive.error}
        onOpenChange={(open) => !open && close()}
        onConfirm={() => setActive.mutate(!user.is_active, { onSuccess: () => setDialog(null) })}
      >
        {user.is_active
          ? "The user will be signed out and blocked from signing in until reactivated. This does not delete or anonymize the account."
          : "The user will be able to sign in again."}
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "grant-organizer"}
        title="Grant CTF Organizer?"
        confirmLabel="Grant"
        pending={grantOrganizer.isPending}
        error={grantOrganizer.error}
        onOpenChange={(open) => !open && close()}
        onConfirm={() => grantOrganizer.mutate(undefined, { onSuccess: () => setDialog(null) })}
      >
        This adds the user to CTF Organizer as a local grant. It is additive and audited; provider-managed membership is
        unaffected. Removing organizer access is not available here.
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "delete"}
        title="Delete account?"
        confirmLabel="Delete"
        destructive
        pending={softDelete.isPending}
        error={softDelete.error}
        onOpenChange={(open) => !open && close()}
        onConfirm={() => softDelete.mutate(undefined, { onSuccess: () => setDialog(null) })}
      >
        The account is soft-deleted and can no longer sign in. This does not permanently erase, anonymize, or unbind the
        provider identity.
      </ConfirmDialog>
    </>
  );
}

function Field({ label, children }: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm">{children}</dd>
    </div>
  );
}
