import { useState } from "react";

import { describeMutationError } from "@/api/errors";
import {
  drainBinding,
  previewEffectivePolicy,
  previewSelector,
  publishBinding,
  validateBinding,
  type BindingRevision,
  type EffectivePolicyPreview,
  type SelectorPreview,
} from "@/api/model-access";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

/** Which ranges a binding covers. */
const SELECTOR_KINDS = [
  "all_ranges",
  "selected_ranges",
  "ctf_event",
  "ctf_cohort",
  "ctf_team",
  "user",
  "auth_group",
  "workspace",
  "organization",
] as const;

/** Which facets are shared. Unchecked facets stay per-range/individual. */
const FACETS = ["profile", "provider_identity", "routing", "capacity", "spend", "rate", "concurrency"] as const;

type FacetState = Record<(typeof FACETS)[number], boolean>;

const EMPTY_FACETS: FacetState = {
  profile: false,
  provider_identity: false,
  routing: false,
  capacity: false,
  spend: false,
  rate: false,
  concurrency: false,
};

function splitIds(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Operator/organizer model-access sharing management (M09, #2126 / PLAT-202).
 *
 * Compose which ranges and which facets to share, preview the matched members,
 * then publish or drain a binding under an optimistic definition-revision fence.
 * Publication re-resolves membership and authority server-side; a stale revision
 * returns 409 and the operator must reload before retrying. The six sharing.md
 * examples are expressed as combinations of the selector, facet and accounting
 * controls below.
 */
export function ModelAccessSharing() {
  const [kind, setKind] = useState<(typeof SELECTOR_KINDS)[number]>("all_ranges");
  const [ids, setIds] = useState("");
  const [includeSpares, setIncludeSpares] = useState(false);
  const [facets, setFacets] = useState<FacetState>(EMPTY_FACETS);
  const [membershipMode, setMembershipMode] = useState<"snapshot" | "dynamic">("dynamic");

  const [bindingId, setBindingId] = useState("");
  const [poolId, setPoolId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [priority, setPriority] = useState(0);
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveUntil, setEffectiveUntil] = useState("");
  const [routingRevision, setRoutingRevision] = useState(1);
  const [providerPoolRef, setProviderPoolRef] = useState("");
  const [spendAccount, setSpendAccount] = useState("");
  const [expectedRevision, setExpectedRevision] = useState(0);

  const [preview, setPreview] = useState<SelectorPreview | null>(null);
  const [valid, setValid] = useState<boolean | null>(null);
  const [subjectRef, setSubjectRef] = useState("");
  const [policy, setPolicy] = useState<EffectivePolicyPreview | null>(null);
  const [revision, setRevision] = useState<BindingRevision | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [stale, setStale] = useState(false);
  const [pending, setPending] = useState(false);

  function selectorPayload() {
    return { kind, ids: splitIds(ids), include_spares: includeSpares };
  }

  function chosenFacets(): string[] {
    return FACETS.filter((facet) => facets[facet]);
  }

  function bindingPayload() {
    return {
      sharing_binding_id: bindingId,
      selector: selectorPayload(),
      membership_mode: membershipMode,
      profile_id: profileId || null,
      sharing_pool_id: poolId,
      facets: chosenFacets(),
      priority,
      effective_from: effectiveFrom,
      effective_until: effectiveUntil,
    };
  }

  function poolPayload() {
    return {
      sharing_pool_id: poolId,
      routing_revision: routingRevision,
      provider_pool_ref: providerPoolRef || null,
      spend_account_refs: spendAccount ? [spendAccount] : [],
    };
  }

  function begin() {
    setError(null);
    setStale(false);
    setPending(true);
  }

  function fail(caught: unknown) {
    const status = (caught as { status?: number })?.status;
    if (status === 409) setStale(true);
    else setError(caught);
  }

  async function runPreview() {
    begin();
    setPreview(null);
    try {
      setPreview(await previewSelector(selectorPayload()));
    } catch (caught) {
      fail(caught);
    } finally {
      setPending(false);
    }
  }

  async function runValidate() {
    begin();
    setValid(null);
    try {
      await validateBinding(bindingPayload(), poolPayload());
      setValid(true);
    } catch (caught) {
      setValid(false);
      fail(caught);
    } finally {
      setPending(false);
    }
  }

  async function runPolicyPreview() {
    begin();
    setPolicy(null);
    try {
      setPolicy(await previewEffectivePolicy({ owner: "deployment", reference: subjectRef }));
    } catch (caught) {
      fail(caught);
    } finally {
      setPending(false);
    }
  }

  async function runPublish() {
    begin();
    setRevision(null);
    try {
      setRevision(await publishBinding(bindingPayload(), poolPayload(), expectedRevision));
    } catch (caught) {
      fail(caught);
    } finally {
      setPending(false);
    }
  }

  async function runDrain() {
    begin();
    setRevision(null);
    try {
      setRevision(await drainBinding(bindingId, expectedRevision));
    } catch (caught) {
      fail(caught);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="space-y-6" aria-labelledby="model-access-sharing-heading">
      <PageHeader
        title="Model-access sharing"
        description="Share model profiles, provider identity, capacity and budgets across selected ranges, users, groups or collections."
      />
      <h2 id="model-access-sharing-heading" className="sr-only">
        Model-access sharing
      </h2>

      {stale ? (
        <Alert variant="destructive">
          <AlertDescription role="alert">Definition changed. Reload this binding before saving.</AlertDescription>
        </Alert>
      ) : null}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription role="alert">
            {describeMutationError(error, "The sharing request could not be completed.")}
          </AlertDescription>
        </Alert>
      ) : null}

      <fieldset className="space-y-3">
        <legend className="font-medium">Which ranges</legend>
        <Label htmlFor="selector-kind">Scope</Label>
        <select
          id="selector-kind"
          className="block rounded border bg-background p-2"
          value={kind}
          onChange={(event) => setKind(event.target.value as (typeof SELECTOR_KINDS)[number])}
        >
          {SELECTOR_KINDS.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        {kind !== "all_ranges" ? (
          <>
            <Label htmlFor="selector-ids">Identifiers (comma or space separated)</Label>
            <textarea
              id="selector-ids"
              className="block w-full rounded border bg-background p-2"
              value={ids}
              onChange={(event) => setIds(event.target.value)}
            />
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={includeSpares} onChange={(e) => setIncludeSpares(e.target.checked)} />
              Include spare ranges
            </label>
          </>
        ) : null}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="font-medium">Which facets to share</legend>
        {FACETS.map((facet) => (
          <label key={facet} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={facets[facet]}
              onChange={(event) => setFacets({ ...facets, [facet]: event.target.checked })}
            />
            {facet}
          </label>
        ))}
        <Label htmlFor="membership-mode">Membership</Label>
        <select
          id="membership-mode"
          className="block rounded border bg-background p-2"
          value={membershipMode}
          onChange={(event) => setMembershipMode(event.target.value as "snapshot" | "dynamic")}
        >
          <option value="dynamic">dynamic</option>
          <option value="snapshot">snapshot</option>
        </select>
      </fieldset>

      <div>
        <Button type="button" onClick={runPreview} disabled={pending}>
          Preview matched ranges
        </Button>
        {preview ? (
          <output className="mt-2 block">
            Matched {preview.matched} range{preview.matched === 1 ? "" : "s"}.
          </output>
        ) : null}
      </div>

      <div>
        <Button type="button" onClick={runValidate} disabled={pending || !bindingId || !poolId}>
          Validate draft
        </Button>
        {valid === true ? <output className="mt-2 block">Draft is valid.</output> : null}
        {valid === false ? (
          <p role="alert" className="mt-2 text-sm">
            Draft is not valid. Correct the fields and retry.
          </p>
        ) : null}
      </div>

      <fieldset className="space-y-3">
        <legend className="font-medium">Preview effective policy for a range</legend>
        <Label htmlFor="subject-ref">Range reference</Label>
        <input
          id="subject-ref"
          className="block rounded border bg-background p-2"
          value={subjectRef}
          onChange={(event) => setSubjectRef(event.target.value)}
        />
        <Button type="button" onClick={runPolicyPreview} disabled={pending || !subjectRef}>
          Preview effective policy
        </Button>
        {policy ? (
          <div className="space-y-1 text-sm">
            <p>
              {policy.is_admissible ? "Admissible" : "Not admissible"}
              {policy.stale ? " · stale membership" : ""}.
            </p>
            {policy.conflicts.length ? (
              <p role="alert">Priority conflicts: {policy.conflicts.map((conflict) => conflict.code).join(", ")}.</p>
            ) : (
              <p>No priority conflicts.</p>
            )}
            <ul className="list-disc pl-5">
              {policy.contributions.map((contribution) => (
                <li key={contribution.sharing_binding_id}>
                  {contribution.sharing_binding_id} (revision {contribution.membership_revision}, priority{" "}
                  {contribution.priority}): {contribution.facets.join(", ")}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="font-medium">Publish</legend>
        <Label htmlFor="binding-id">Binding ID</Label>
        <input id="binding-id" className="block rounded border bg-background p-2" value={bindingId} onChange={(e) => setBindingId(e.target.value)} />
        <Label htmlFor="pool-id">Pool ID</Label>
        <input id="pool-id" className="block rounded border bg-background p-2" value={poolId} onChange={(e) => setPoolId(e.target.value)} />
        <Label htmlFor="profile-id">Profile (optional)</Label>
        <input id="profile-id" className="block rounded border bg-background p-2" value={profileId} onChange={(e) => setProfileId(e.target.value)} />
        <Label htmlFor="provider-pool-ref">Shared provider account (optional)</Label>
        <input id="provider-pool-ref" className="block rounded border bg-background p-2" value={providerPoolRef} onChange={(e) => setProviderPoolRef(e.target.value)} />
        <Label htmlFor="spend-account">Shared spend account (optional)</Label>
        <input id="spend-account" className="block rounded border bg-background p-2" value={spendAccount} onChange={(e) => setSpendAccount(e.target.value)} />
        <Label htmlFor="priority">Priority</Label>
        <input id="priority" type="number" className="block rounded border bg-background p-2" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
        <Label htmlFor="routing-revision">Routing revision</Label>
        <input id="routing-revision" type="number" className="block rounded border bg-background p-2" value={routingRevision} onChange={(e) => setRoutingRevision(Number(e.target.value))} />
        <Label htmlFor="effective-from">Effective from</Label>
        <input id="effective-from" className="block rounded border bg-background p-2" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />
        <Label htmlFor="effective-until">Effective until</Label>
        <input id="effective-until" className="block rounded border bg-background p-2" value={effectiveUntil} onChange={(e) => setEffectiveUntil(e.target.value)} />
        <Label htmlFor="expected-revision">Expected definition revision</Label>
        <input id="expected-revision" type="number" className="block rounded border bg-background p-2" value={expectedRevision} onChange={(e) => setExpectedRevision(Number(e.target.value))} />
        <div className="flex gap-2">
          <Button type="button" onClick={runPublish} disabled={pending || !bindingId || !poolId}>
            Publish binding
          </Button>
          <Button type="button" variant="destructive" onClick={runDrain} disabled={pending || !bindingId}>
            Drain binding
          </Button>
        </div>
        {revision ? (
          <output className="block">
            Binding {revision.sharing_binding_id} is now {revision.state} at revision {revision.definition_revision}.
          </output>
        ) : null}
      </fieldset>
    </section>
  );
}
