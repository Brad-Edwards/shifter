import { useId, useState, type FormEvent } from "react";

import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/errors";
import { useCreateCredential, useDeleteCredential, type CredentialCreateRequest } from "@/api/mission-control";
import type { CredentialCreateResponse } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { ConfirmDialog } from "./ConfirmDialog";

type CredentialType = CredentialCreateRequest["credential_type"];

const SLS_REGIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "americas", label: "Americas" },
  { value: "europe", label: "Europe" },
  { value: "japan", label: "Japan" },
  { value: "asiapacific", label: "Asia Pacific" },
];

interface FormErrors {
  name?: string;
  scmPinId?: string;
  scmPinValue?: string;
  slsRegion?: string;
  authcode?: string;
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
 * Add-credential form (#1370), matching `CredentialCreateSerializer`'s
 * type-conditional fields exactly (`templates/mission_control/credentials/add.html`):
 * SCM registration (folder/PIN id/PIN value/region) or a deployment profile
 * (authcode). No auto-retry on a server error (ADR-029); the form stays
 * filled and the error shows inline for a corrected resubmit.
 *
 * There is no `/api/v1` read endpoint for listing existing credentials
 * (`mission_control/api/urls.py` only has `POST credentials/` and
 * `POST credentials/<id>/delete/`), so this page cannot show or manage a
 * credential list — that gap is tracked by #1328/#1329. The credential id
 * returned on create is shown so it can be copied into the NGFW setup form,
 * which has the same picker gap.
 */
export function CredentialsPage() {
  const createCredential = useCreateCredential();
  const deleteCredential = useDeleteCredential();

  const [credentialType, setCredentialType] = useState<CredentialType | "">("");
  const [name, setName] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [scmFolderName, setScmFolderName] = useState("");
  const [scmPinId, setScmPinId] = useState("");
  const [scmPinValue, setScmPinValue] = useState("");
  const [slsRegion, setSlsRegion] = useState("");
  const [authcode, setAuthcode] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [created, setCreated] = useState<CredentialCreateResponse | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const typeId = useId();
  const nameId = useId();
  const expiresId = useId();
  const folderId = useId();
  const pinIdId = useId();
  const pinValueId = useId();
  const regionId = useId();
  const authcodeId = useId();

  const serverError =
    createCredential.error instanceof ApiError
      ? createCredential.error.message
      : createCredential.error
        ? "The credential could not be created."
        : null;

  function resetTypeFields() {
    setScmFolderName("");
    setScmPinId("");
    setScmPinValue("");
    setSlsRegion("");
    setAuthcode("");
    setErrors({});
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!credentialType) return;

    const nextErrors: FormErrors = {};
    if (!name.trim()) nextErrors.name = "Enter a display name.";
    if (credentialType === "scm") {
      if (!scmPinId.trim()) nextErrors.scmPinId = "Enter the PIN id.";
      if (!scmPinValue) nextErrors.scmPinValue = "Enter the PIN value.";
      if (!slsRegion) nextErrors.slsRegion = "Select a licensing region.";
    } else {
      if (!authcode.trim()) nextErrors.authcode = "Enter the VM-Series authcode.";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const body: CredentialCreateRequest =
      credentialType === "scm"
        ? {
            credential_type: "scm",
            name: name.trim(),
            expires_at: expiresAt || null,
            scm_folder_name: scmFolderName.trim(),
            scm_pin_id: scmPinId.trim(),
            scm_pin_value: scmPinValue,
            sls_region: slsRegion,
          }
        : {
            credential_type: "deployment_profile",
            name: name.trim(),
            expires_at: expiresAt || null,
            authcode,
          };

    createCredential.mutate(body, {
      onSuccess: (result) => {
        setCreated(result);
        setName("");
        setExpiresAt("");
        resetTypeFields();
        setCredentialType("");
      },
    });
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="Credentials" description="Store credentials for provisioning and integrations." />

      <Alert className="mb-6">
        <AlertTitle>Credential list is not yet available here</AlertTitle>
        <AlertDescription>
          There is no `/api/v1` read endpoint yet for listing existing credentials (#1328, #1329), so this page can
          only add a new credential. Note the id shown after creating one — the NGFW setup form needs it.
        </AlertDescription>
      </Alert>

      {created ? (
        <Alert className="mb-6">
          <AlertTitle>Credential created</AlertTitle>
          <AlertDescription>
            <p>
              &ldquo;{created.name}&rdquo; ({created.credential_type}) was created with id{" "}
              <span className="font-mono">{created.id}</span>.
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => setDeleteOpen(true)}
            >
              Delete this credential
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {serverError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not create this credential</AlertTitle>
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={onSubmit} noValidate>
        <Card>
          <CardContent className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor={typeId}>Credential type</Label>
              <Select
                value={credentialType}
                onValueChange={(value) => {
                  setCredentialType(value as CredentialType);
                  resetTypeFields();
                }}
              >
                <SelectTrigger id={typeId} className="w-full">
                  <SelectValue placeholder="Select credential type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="scm">SCM Registration</SelectItem>
                  <SelectItem value="deployment_profile">Deployment Profile</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {credentialType ? (
              <>
                <div className="flex flex-col gap-2">
                  <Label htmlFor={nameId}>Display name</Label>
                  <Input
                    id={nameId}
                    placeholder="e.g., Production Credential"
                    maxLength={100}
                    value={name}
                    aria-invalid={errors.name ? true : undefined}
                    aria-describedby={errors.name ? `${nameId}-e` : undefined}
                    onChange={(event) => setName(event.target.value)}
                  />
                  <FieldError id={`${nameId}-e`} message={errors.name} />
                </div>

                {credentialType === "scm" ? (
                  <>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={folderId}>Folder name</Label>
                      <Input
                        id={folderId}
                        placeholder='e.g., Shared/Firewall (defaults to "All Firewalls")'
                        value={scmFolderName}
                        onChange={(event) => setScmFolderName(event.target.value)}
                      />
                    </div>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={pinIdId}>PIN id</Label>
                      <Input
                        id={pinIdId}
                        placeholder="e.g., pin-12345"
                        value={scmPinId}
                        aria-invalid={errors.scmPinId ? true : undefined}
                        aria-describedby={errors.scmPinId ? `${pinIdId}-e` : undefined}
                        onChange={(event) => setScmPinId(event.target.value)}
                      />
                      <FieldError id={`${pinIdId}-e`} message={errors.scmPinId} />
                    </div>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={pinValueId}>PIN value</Label>
                      <Input
                        id={pinValueId}
                        type="password"
                        value={scmPinValue}
                        aria-invalid={errors.scmPinValue ? true : undefined}
                        aria-describedby={errors.scmPinValue ? `${pinValueId}-e` : undefined}
                        onChange={(event) => setScmPinValue(event.target.value)}
                      />
                      <FieldError id={`${pinValueId}-e`} message={errors.scmPinValue} />
                    </div>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={regionId}>Licensing region</Label>
                      <Select value={slsRegion} onValueChange={setSlsRegion}>
                        <SelectTrigger
                          id={regionId}
                          className="w-full"
                          aria-invalid={errors.slsRegion ? true : undefined}
                          aria-describedby={errors.slsRegion ? `${regionId}-e` : undefined}
                        >
                          <SelectValue placeholder="Select region…" />
                        </SelectTrigger>
                        <SelectContent>
                          {SLS_REGIONS.map((region) => (
                            <SelectItem key={region.value} value={region.value}>
                              {region.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FieldError id={`${regionId}-e`} message={errors.slsRegion} />
                    </div>
                  </>
                ) : (
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={authcodeId}>VM-Series authcode</Label>
                    <Input
                      id={authcodeId}
                      type="password"
                      placeholder="Enter your authcode"
                      value={authcode}
                      aria-invalid={errors.authcode ? true : undefined}
                      aria-describedby={errors.authcode ? `${authcodeId}-e` : undefined}
                      onChange={(event) => setAuthcode(event.target.value)}
                    />
                    <FieldError id={`${authcodeId}-e`} message={errors.authcode} />
                  </div>
                )}

                <div className="flex flex-col gap-2">
                  <Label htmlFor={expiresId}>Expiration date</Label>
                  <Input
                    id={expiresId}
                    type="date"
                    value={expiresAt}
                    onChange={(event) => setExpiresAt(event.target.value)}
                  />
                </div>
              </>
            ) : null}
          </CardContent>
          <CardFooter className="justify-end">
            <Button type="submit" disabled={!credentialType || createCredential.isPending}>
              {createCredential.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
              {createCredential.isPending ? "Saving…" : "Save credential"}
            </Button>
          </CardFooter>
        </Card>
      </form>

      {created ? (
        <ConfirmDialog
          open={deleteOpen}
          title={`Delete "${created.name}"?`}
          confirmLabel="Delete"
          destructive
          pending={deleteCredential.isPending}
          error={deleteCredential.error}
          onOpenChange={setDeleteOpen}
          onConfirm={() => {
            deleteCredential.mutate(created.id, {
              onSuccess: () => {
                setDeleteOpen(false);
                setCreated(null);
              },
            });
          }}
        >
          This cannot be undone.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
