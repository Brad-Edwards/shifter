# Configurable sharing across ranges

This clarification to [#681](index.md) is part of the first model-access
delivery, including GCP qualification. Operators must be able to share some
or all configured model-access resources across a chosen collection of
ranges. Per-range grant identity is compatible with shared provider
credentials, model assignments, capacity and budgets.

## Choose the collection and the shared resources independently

The management surface exposes two choices: **which ranges** a binding
covers, and **what they share**. Supported collections are:

| Collection | Authoritative membership |
| --- | --- |
| Selected ranges | An explicit bounded set of stable logical range IDs, optionally chosen from CTF ranges across permitted events. |
| CTF event or cohort | All ranges bound to the selected event, or an explicit set of its participants/teams and their ranges; include-spares is an explicit setting. |
| A user's ranges | Current canonical range-owner user ID, across that user's permitted CTF and standalone ranges. Launching a range on someone's behalf does not make it the operator's range. |
| A group's ranges | A typed reference to an existing user group, CTF team, workspace or organization, resolved through its owning service and its documented range association. Group names are not identities. |
| Named collection | A model-access collection containing an explicit set or bounded union of the supported selectors, for example selected ranges from two events plus a research group's ranges. It grants no new IAM, workspace or event authority. |
| All ranges | Every eligible current/future range in this deployment, subject to the binding's membership mode and the range's own admission. It never crosses the ADR-054 customer/deployment boundary. |

For user groups, the initial adapter can resolve the incumbent Django
`auth.Group` membership through its owning identity boundary. CTF teams use
event-native team membership/range association; workspaces and organizations
use persisted range ownership/bindings and their service facades. These are
different group types, not aliases for one another. Unknown, deleted or
unintegrated group types reject; implementation cannot replace them with
name matching or untrusted labels.

| Shareable resource | Independent options |
| --- | --- |
| Model/tool profile | A common immutable logical profile and feature allowlist; members may have tighter independent constraints. |
| Provider identity and eligible shards | A shared broker-held provider account/credential/shard pool, or different pools for selected subsets/aliases. Sharing a pool need not put every member on the same shard. |
| Actual model routing | Separate assignment per range, one sticky assignment per user within the routing pool, or one sticky assignment for the entire sharing pool. Each logical model/tool alias can choose independently. |
| Capacity | A common committed capacity reservation with child draws, or separate commitments against the same real provider quota. |
| Spend, rate and concurrency | Shared pool accounts, independent per-range accounts, or pooled accounts with additional per-range limits. Each dimension is configured independently. |

Sharing all these dimensions is a supported configuration. The broker still
issues a distinct, generation-bound capability per range/workload. Reusing
one provider credential inside the broker does not require handing every
participant the same bearer token. Revoking one range therefore need not
rotate the shared provider identity or disable other members.

Examples that the UI and acceptance tests must support:

| Operator intent | Result |
| --- | --- |
| All ranges use one provider account, with individual spend limits | Shared provider pool; per-range routing/accounting as selected. |
| These 20 CTF ranges share everything | One profile, shared assignment per alias, provider identity, capacity and spend/rate/concurrency pools; distinct revocable range grants. |
| All of one user's ranges share a budget across two events | One persistent user budget account applies to every qualifying range; each event's own limits also apply. |
| A research group's ranges share their main model but have separate small-model allocations | Shared assignment for `coding-main`; per-range assignment for `coding-small`, with independently chosen budgets. |
| A cohort shares capacity and spend, with individual concurrency caps | One cohort capacity/spend pool plus separate concurrency ceilings; pooled capacity is reserved once. |
| Two selected events share a model pool but retain their own budgets | Both bindings reference the same routing/provider pool; event budget accounts remain distinct. |

## Binding contract and membership modes

A versioned `SharingBinding` identifies the deployment, canonical selector,
membership mode/revision, authorized publisher, immutable profile reference,
facet references, priority, effective interval and definition digest. A
`SharingPool` has a stable ID independent of binding/policy revisions; it owns
the selected common routing/capacity/account identities. Multiple bindings
may deliberately refer to the same pool. Reusing a profile alone does not
implicitly join a budget or routing pool.

Selectors are closed, typed data. Initial bounds are 32 atomic selectors per
collection and 1,000 explicit user/range/participant IDs per binding.
Automatic selectors may match a larger deployment population, resolved with
bounded pagination and an explicit assessment count. Support bounded union
with set semantics; no recursively nested groups, arbitrary query language,
SQL, executable expression or wildcard provider selector. A user-group
adapter's canonical membership semantics remain owned by that identity
system; this feature does not introduce transitive IAM groups.

Offer both membership modes:

- `snapshot`: freeze the resolved logical range/draw membership at publication
  and show that snapshot. Later group/event membership changes do not add
  members. The same authorized logical range can re-enroll a new generation,
  but replacements with a different identity need explicit inclusion.
- `dynamic`: apply to current and future eligible ranges. Before any new
  allocation or grant, resolve membership through the authoritative owner
  and persist a fresh versioned projection. Membership additions require
  normal policy/capacity admission; they cannot make a grant usable merely
  by changing a group row.

Default explicit range lists to `snapshot`; default owner/group/event/all-range
selectors to `dynamic`. The publisher sees and can change the mode before
publishing. An event's future participant/spare draw membership can be pinned
before range creation using the existing stable draw keys. A zero-member
dynamic collection is valid but allocates nothing; an empty snapshot requires
explicit acknowledgement and never means all ranges.

Removal, owner transfer, user disable, event membership revocation or binding
withdrawal invalidates affected active grants/projections in the same
authoritative transaction. Reassessment can issue a new grant under other
still-authorized bindings; the old epoch never survives by silently switching
pools. Unknown membership or an unavailable/stale projection fails closed.
Snapshot mode freezes inclusion, not user authorization: a snapshotted
participant who loses access must still be revoked.

Membership is produced by CTF/CMS/identity/workspace owners through permitted
downward bridges. Engine owns persisted binding/pool/projection and allocation
state; it never imports or calls CTF/CMS to discover current members. Reuse
the architecture's transactional authorization projection and invalidation
mechanism. Enumeration of all supported membership mutation paths and real
concurrent removal/invocation tests are release requirements. For large
collections, invalidate a shared membership revision synchronously; every
request checks that revision, while bounded workers reassess member grants.
Do not leave a usable stale-grant window while a fan-out job catches up.

## Overlap and precedence

A range may simultaneously match deployment, user, group, event and explicit
range bindings. The effective-policy preview lists every match and its
reason, membership revision and contribution. Do not assume these scopes
form a single hierarchy or use an undocumented last-match-wins rule.

All mandatory restrictions apply: intersect capability/model/data-region
allowlists, take the tightest deadlines/individual ceilings, and enforce
every distinct applicable budget/rate/concurrency account. An explicit deny
dominates. A more specific binding cannot bypass a deployment or user cap.

For a non-combinable choice, such as which routing pool supplies an alias,
use the binding's explicit operator-controlled priority (integer 0–1,000,
default 0). Highest priority selects the value for that facet/alias. Equal
priority with different values is an admission conflict; identical values
coalesce. A binding with no value for a facet contributes no override.
There is no priority-based bypass of hard restrictions or accounts.
Missing configuration must resolve to an explicit deployment default or
reject; it cannot silently allocate unlimited access.

Resolve before capacity effects, and persist the full effective binding and
pool reference set plus revisions. Cross-event or deployment-wide bindings
require an operator with authority over the entire selected collection;
an event organizer can publish only within that event's delegated envelope.
An ordinary user cannot increase their allowance by joining a self-service
group. Funding eligibility requires operator-managed membership or a
separate approved spending-eligibility projection; self-service membership
alone cannot activate a funded binding. Group membership is an applicability
predicate, not permission to publish policies, enroll a range, view other
members or increase spending.

## Allocation and accounting invariants

Routing pools select `per_range`, `per_user` or `per_pool` assignment for
each alias. `per_range` uses the existing stable draw UUID. The other modes
use an Engine-persisted allocation-group UUID keyed by routing-pool revision
and canonical owner identity, or by routing-pool revision alone. Substitute
that UUID for the draw UUID in ADR-060's exact rendezvous encoding; retries
and later group members use the persisted assignment. The canonical pool
record separates the key namespaces and stores the affinity kind.

Allocate once for a shared assignment and serialize concurrent first use.
A new member whose effective policy cannot use the pinned shared shard is
rejected or requires an explicitly revised binding/pool. It cannot move
existing members or silently receive a private alternate assignment. Group
membership changes do not reshuffle remaining members. A pool routing change
creates a new allocation revision with explicit drain/re-admission; its
financial account IDs and prior spend survive that change.

A shared capacity reservation is committed once per pool/metric/window.
Event and range draws reference that commitment; do not subtract both the
parent and its child draws as separate commitments against provider quota.
Independent reservations sharing a provider account still consume that real
quota separately. Admission of overlapping event/cohort demand must account
for shared versus dedicated capacity explicitly, rather than multiplying a
pool's declared capacity by the number of matching selectors or members.

For each request, compute the **set** of applicable account IDs, lock once
in canonical order, and reserve/settle each distinct account once. Matching
the same pool through a user group and event cannot double-charge it. Two
different account caps both constrain the call; their overlapping constraint
ledgers must not be summed as if they represented two provider charges.
Usage reporting derives actual cost from unique request/settlement records.

Shared-only budgets are valid: members spend from the common balance without
an artificial equal per-range partition. Pooled-plus-individual mode adds a
range allowance. Finite deployment ceilings and per-request/transport bounds
still apply. A shared member can exhaust the available pool unless the
operator configures individual limits; the UI makes that choice explicit.
No implicit fair-share scheduler is promised.

Requests retain their original pool/account vector through settlement,
disconnect, revocation and reconciliation. Removing a member, changing a
selector or moving a range to another pool does not refund prior spend,
transfer in-flight liability, reset a user/deployment cap or erase evidence.
Drain/deletion waits for all child allocations and unresolved liabilities;
a tombstone retains original references until they can be safely retired.

## Management and qualification

Add scope selection, snapshot/dynamic mode, independently editable sharing
facets, priority and effective-policy preview to the same management API/UI
work as the original design. Include explicit selected CTF ranges through
the existing event/range picker, user and typed group selectors, and an
operator-only all-ranges option. Preview matched/excluded counts, conflicts,
shared remaining balances, individual limits and the proposed effect on
active ranges before publication. Require revision comparison on publish;
preview is not an authorization token and stale membership is rechecked.

Audit binding publication, membership changes, overlap decisions and pool
drain with safe IDs/revisions. Operators can inspect shared balances and
content-free attribution; organizers/users see only authorized contributions
and permitted totals, never other members' identities or raw usage by
inference through an unrestricted group roster. Preserve the privacy policy
for small-cohort aggregate disclosure.

Qualification must demonstrate every collection type, partial/all sharing,
mixed alias affinity, overlap deduplication, conflicting priority, concurrent
first use, concurrent spend, membership addition/removal, user transfer,
expiry and cleanup. Include a user with ranges in two CTF events and a
non-CTF range, a user group spanning authorized events, selected range sets
and a deployment-wide policy. Prove one range can be revoked while its
neighbors continue on the same provider identity and pool.
