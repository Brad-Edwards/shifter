# Model sources for scenarios and CTFs

A model source connects an approved model to an account that pays for its use.
It is independent of where the range or Shifter runs. A Vertex source may use
Shifter's own GCP project, another GCP project, or a model account elsewhere.
The broker handles provider authentication; participants receive only scoped,
revocable access for their range.

These controls require an enabled model broker and an approved scenario model
profile. A source marked **Configured** has been stored successfully; that label
is not a live provider test or a guarantee of quota. Deployment qualification
is tracked separately in the [operations guide](../ops/model-access.md).

## Connect a source

Organization administrators open **Administer → Model sources**, choose an
organization, and select **Add model source**. Choose the provider, model and
account identity, then enter the context limit, application token ceiling,
prices and price expiry. Cloud sources also require their explicit region and
invocation identity. Workload identity uses the broker's approved cloud grants;
otherwise supply a provider credential in the form. Secrets are write-only and
are cleared after every attempted submission. An edit without new credentials
retains the current credential.

Source registration does not give anyone permission to spend. Explicitly allow
organization members or select individual users. A source is selectable only
while the user also belongs to that tenant. Administering sources and being
allowed to use their funds are separate permissions.

| Source | Configuration and behavior |
| --- | --- |
| Vertex | Approved project, service account, model, invocation region and counting region. The platform project is a valid choice. |
| Bedrock | Approved AWS invocation role, model and region. The hosting cloud need not be AWS. |
| Direct Anthropic | API key and pinned model at the provider's fixed API origin. |
| OpenAI | API key and pinned model; the broker translates the supported text and local-tool protocol to Responses. |
| OpenRouter | API key, model and one explicit upstream provider; automatic provider fallback is disabled. |

Direct API sources use **provider-managed** geography; they do not promise a
cloud region. Both deployment policy and the scenario must allow that geography.
OpenRouter does not expose token counting through this integration. It requires
a scenario input allowance covering the source's full context window; scenarios
requiring token counting cannot use it. Unsupported choices are omitted from the
scenario picker. Hosted provider tools, arbitrary API URLs and platform
administration tools are not granted by selecting a model source.

The token ceiling is an application limit, not purchased provider quota.
Sources using the same cloud account and region share capacity accounting;
renaming them does not create capacity. For direct providers, use the same quota
identity for sources that share a provider quota. Prices use micro-units of the
selected currency per million tokens. A zero price explicitly means no monetary
charge is reserved for that component; it does not disable token or rate limits.
An expired price prevents new admission.

## Choose sources at launch

Select a workspace and scenario in the range launch form. For each model alias,
keep the deployment default or select an authorized source. Where the scenario
and deployment allow weighted allocation, select several sources and their
relative weights. For example, weights 2 and 1 express a preferred two-to-one
distribution across many allocations, not an exact participant count.

Allocation checks capacity and policy before launch. Source choices do not
replace shared event/user budgets, per-range limits, allowed regions or provider
pool restrictions. A retry preserves the original launch intent. The broker
never retries a potentially billed request on another source automatically.

## Configure a CTF

The event create/edit form provides the same model-source picker. An organizer
with event configuration permission and source-use authority selects sources
for the event's aliases. Participants use this event sponsorship without
receiving the organizer's credentials or source administration permission.
If no explicit model-demand declaration is supplied, admission derives it from
the scenario's verified needs and the event's participant ceiling.

With independent weighted routing, the event reserves capacity across the
selected accounts in proportion to their weights, rounded to whole participants.
Every account's share must fit before the first launch. Accounts sharing the
same provider quota still count against that single limit. A policy that keeps
the whole group on one source requires that source to support the whole group.

Changing event defaults applies to subsequent preparation and fences affected
authority. It does not silently rewrite allocations already running. Use the
range administration controls for explicit changes to existing ranges. The
event's workspace remains fixed after creation.

## Change an existing range

Under **Model sources → Range model sources**, choose the range and edit its
assignments. Save compares the revision displayed in the form; if another
administrator changed it, reload before trying again.

The change revokes old grants before attempting replacement admission. Existing
spend and unresolved provider charges remain. A successful admission reports
refresh pending until the guest exchanges its refresh credential. It keeps the
original range operation and hard expiry. If admission fails, the range remains
blocked and the page displays the reason; retry after correcting the source or
capacity. Failure does not reactivate the old grant.

Disabling or rotating a source fences dependent grants too. **Retire unused
credentials** deletes obsolete stored versions only after the grace period and
when no active allocation or unresolved usage needs them. Current credentials
remain, including for a disabled source that may be re-enabled. Failed cleanup
can be retried from the same tenant page.

For identity, accounting and provider extension details, see
[tenant source management](../architecture/model-access/source-management.md).
