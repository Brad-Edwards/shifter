# GCP GCE range-cell deploy runbook

Part of the Shifter deploy and operations docs; start at the [documentation home](../index.md).

The GCP range backend defaults to the GCE range-cell path, the approved GCP
live-fire backend (ADR-030). This runbook covers enabling it in a real
environment and mapping images.

For the backend design see
`docs/architecture/gcp-range-cell-backend-preflight-1341.md`. For image builds see
`docs/architecture/gcp-guest-images.md`.

## Backend selection

`GCP_RANGE_BACKEND` selects the GCP range backend:

- `gce` (default): provision each range as an isolated GCE range cell. This is
  the only approved GCP **live-fire** backend (ADR-030).
- `gdc`: the retained GDC VM Runtime path (**development/validation only**). It is
  **not** a live-fire rollback: normal Mission Control and CTF range provisioning
  fails closed on `gdc` (the CMS service gate rejects the launch and the
  provisioner independently denies a live-fire GDC apply; issue #1348). Do not set
  `GCP_RANGE_BACKEND=gdc` to "roll back" a live-fire environment when GCE is
  unhealthy. A GCE availability problem must be fixed on the GCE path, never by
  downgrading containment.

The default lives in `config.py` (`get_gcp_range_backend`) and the generated
runtime config (`scripts/gcp/render_runtime_env.py`). The GDC configuration block
is retained in the rendered contract and is inert while the backend is `gce`.

### Switching the selector on an environment with existing GDC ranges

New GCP ranges persist the admitted `range_backend` and
`instantiation_purpose` as write-once Engine ownership state. Destroy and
provision, including artifact validation, variable shaping, configuration
loading, and provision-failure compensation, route from that per-range binding.
Changing the deploy-wide selector does not reclassify an existing range:
teardown continues to route from persisted ownership. A provision whose binding
no longer matches the selector fails closed rather than silently re-routing.
Both the legacy RangeSpec and RAES lifecycle paths require a valid persisted
backend/purpose pair and fail closed if it is absent or incompatible.

Legacy ranges created before the binding was introduced may have NULL ownership
fields. Their destroy path resolves only from durable instance-state evidence;
it never guesses from the current selector. If the evidence is absent or
ambiguous, cleanup fails closed and retains state. While the historical
selector is known, an operator can repair the row with
`manage.py backfill_range_backend_binding --range-id <id> --backend <gdc|gce>`
and retry. Do not switch selectors and then infer a legacy range's owner from
its scenario name, topology, or the backend that happens to be healthy.

The environment setting is an operator/backend-policy input for new
provisioning, not scenario metadata or ownership evidence for teardown. The
closed range-instantiation policy decides which launches may use each backend;
see
`docs/technical/platform_infrastructure/range-instantiation-policy.md`. Once a
request has been admitted to the GCP VM range-cell contract, the
provisioner refuses to route it to GDC, GKE, or the legacy Terraform path; an
operator must not treat `gdc` as a per-request fallback for a contract-tagged
live-fire user range.

## Scenario-to-cell contract

The boundary is the closed, versioned `shifter.gcp-vm-range-cell` contract in
`shifter/shifter_platform/shared/range_cells.py`. Its responsibilities are:

- The scenario producer owns VM count and roles, containers or nested
  Kubernetes, topology and connectivity, ports and DNS, fixed addresses,
  images, startup/bootstrap behavior, services, and validation. The existing
  wrapped `RangeSpec` is validated by its canonical Pydantic contract before the
  Engine persists it as an immutable SHA-256-bound artifact. The standalone
  provisioner verifies that producer-minted digest without loading the scenario
  schema graph; the platform does not copy scenario fields into a universal
  placement model.
- The platform owns admission to the approved GCP/GCE live-fire capability,
  operation and cell identity, allocated network bindings, isolation, resource
  membership and ownership, lifecycle/recovery state, logical access, and
  cleanup. Allocated CIDRs are bindings and are not written back into the
  scenario artifact. A later destroy rehydrates those bindings from the
  platform allocation table when available and otherwise uses the validated
  authored membership and deterministic resource identity; it never requires a
  blank CIDR to pass request validation.
- The outer request and result reject unknown fields and versions. A digest or
  backend mismatch, malformed/duplicate membership, or missing/foreign network
  binding fails before any Compute Engine or Secret Manager mutation. Results
  reject dangling access targets and inline credentials, trigger cell cleanup
  if output validation fails, and expose credential references rather than
  credential values. Participant access is a closed scenario declaration keyed
  by authored member plus `ssh` or `rdp` channel. The result must match that
  declaration exactly. Participant SSH keys are distinct from host-management
  setup keys, and host/bootstrap credential references never enter the closed
  access result.

`gcp_range_cell_scenario.py` is the compatibility adapter for the current
legacy `RangeSpec`; it owns role/image/host-access interpretation. A future
scenario artifact can use a new discriminator/version adapter while retaining
the same cell lifecycle contract. See
`docs/architecture/scenario-gcp-range-cell-contract-preflight-1344.md` for the
full boundary analysis.

### Capability failures and diagnostics

Capability checks run while rendering the closed request, before Compute,
Secret Manager, or bootstrap mutation. Their stable failure classes are:

- `unsupported-capability`: the composition needs an unimplemented GCE
  capability, currently NGFW range attachment or a configured image profile
  whose `bootstrap_capability` has no GCE realizer. Fix the implementation; do
  not retry on GDC or pods.
- `prerequisite`: the approved route is valid but a required input is absent,
  including a missing exact image mapping or a domain-intent DC whose
  pre-promoted image metadata does not exactly match the authored DNS and
  NetBIOS identity. Fix configuration or the scenario artifact, then launch a
  new range.
- `identity-or-policy`: the persisted backend/purpose pair is not admitted for
  a normal live-fire range, notably `gdc` with `live_fire`.

The current built-in support/evidence projection is in
[`docs/scenarios/index.md`](../scenarios/index.md#gcp-vm-range-cell-support).
It is documentation only; do not reproduce it as a scenario-ID branch or CMS
allowlist. Destroy renders with image and provision-capability checks disabled,
so a newly unsupported composition can still be cleaned up deterministically.
That recovery also tolerates a missing CIDR or OpenVPN gateway-pool binding when
provision failed before allocation: deletion uses deterministic owned-resource
names and remains idempotent. If the launcher reports the canonical command
grammar denial, verify that the base and Helm `restrict-provisioner-jobs`
policies accept the optional UUID-validated `--operation-id` suffix; do not
disable admission or remove the suffix.

## Required configuration

Set the GCE range-cell variables documented in
`docs/dev/deploy-secrets.md` ("GCE range-cell backend variables"). The
minimum for a live range is: `RANGE_NETWORK_ZONE`, `GCP_RANGE_LINUX_IMAGE`,
and any additional guest image profiles required by the authored topology.
Native guests do not receive a host service account. Plugin installation and
compatibility are separate from range readiness; see the
[external runtime boundary](../architecture/external-scenario-runtime-design.md).

The deployment `shifter.yaml` must also set
`settings.dynamic_secret_project_id` to the pre-existing, deployment-only
range-secret project. This is distinct from the guest compute project. Broker
model invocation may use that project, the platform project, or another
administrator-selected project while retaining separate least-privilege
identities. New guest, RAES/GDC, VM-Series
and VPN secrets use canonical deployment- and audience-prefixed names in that
project; persisted full references remain authoritative for guest access.

Direct guest provider credentials are retired. Applying the deployment removes
the old invocation service account and its provisioner key-admin grants. Revoke
any externally owned shared keys through their owner. Legacy range teardown
removes only the range's stored copies; it never reads or reissues provider keys.

When GDC access/image inputs are enabled, declare the
full versionless refs under `settings.provisioner_static_secret_refs`. Do not add
a broad Secret Manager role to compensate for a missing entry. The migration,
permission-probe, quota, audit-cost, and revocation procedure is documented in
[`platform/terraform/gcp/README.md`](../../platform/terraform/gcp/README.md#deployment-scoped-range-secret-project).

`GCP_RANGE_CELL_PROJECT_ID` defaults to the project parsed from the range VPC
self-link, so the range backend targets the real range project even when the
control-plane `GCP_PROJECT_ID` is a deploy-overlay placeholder.

## Network mode

`GCP_RANGE_CELL_NETWORK_MODE` selects how range guests are networked:

- `shared-vpc` (default): each range gets its own subnet in the pre-existing,
  platform-peered range VPC (`RANGE_NETWORK_ID`/`RANGE_VPC_ID`). This mirrors the
  AWS shared-VPC + per-range-subnet model, so the provisioner reaches guests over
  the existing platform↔range peering. Isolation is by per-range subnet and
  target-tag firewall rules (see `docs/architecture/range-isolation-model.md`).
  Requires `RANGE_NETWORK_ID` (or `RANGE_VPC_ID`); both are rendered from the
  `range_network_id` Terraform output.
- `vpc-per-range`: each range mints its own isolated VPC. This gives VPC-hard
  isolation but currently has **no provisioner reachability path** (no peering or
  IAP is created), so guests are unreachable and ranges cannot reach READY. It is
  retained as a selectable mode for a future peering/IAP implementation; do not
  use it for live deployments yet.

## Legacy RangeSpec image mapping

The scenario-owned legacy `RangeSpec` adapter resolves current instances to one
of four approved image profiles by role and OS (`GCERangeCellConfig.get_profile`):

| Instance | Profile | Variable |
|---|---|---|
| Domain controller (`role=dc`) | dc | `GCP_RANGE_DC_IMAGE` |
| Kali / attacker | kali | `GCP_RANGE_KALI_IMAGE` |
| Windows guest | windows | `GCP_RANGE_WINDOWS_IMAGE` |
| Everything else (Linux host) | linux | `GCP_RANGE_LINUX_IMAGE` |

Instances without an `ami_key` continue to use those four defaults. An instance
with an `ami_key` requires an exact entry in
`GCP_RANGE_IMAGE_KEY_PROFILES_JSON` under its derived profile class. Each entry
is complete. Normal image profiles declare image, machine type, disk size, disk
type, and typed bootstrap capability. Exact machine-image profiles declare the
machine image, machine type, host-management login, participant
container/account, participant-readiness contract, and the immutable image's
readiness-manifest digest. Pre-promoted domain profiles also declare their baked
DNS and NetBIOS identity. Unknown keys and keys placed under the wrong class
fail before any Compute or secret client is created; they never fall back to
the default role image. The adapter routes host access and bootstrap from this
trusted profile metadata, never from a scenario or image-name literal.

Profiles resolved for either legacy `RangeSpec` instances or RAES nodes that
require participant web research may set
`"allow_public_web_egress": true`. The range-cell backend then adds a
range-owned egress rule allowing only TCP 80/443 to public IPv4 through the
shared VPC's Cloud NAT. The default is `false`; the per-range default-deny rule
remains in place, and unrelated profiles receive no public egress allowance.

The value is a non-secret JSON object, limited to 32,768 bytes and 64 total
entries. Profile classes and fields are closed. Logical keys must be lowercase
letters, digits, and hyphens. For example:

```json
{
  "kali": {
    "example-desktop": {
      "source_image": "projects/PROJECT/global/images/example-desktop-v1",
      "machine_type": "e2-standard-4",
      "disk_size_gb": 80,
      "disk_type": "pd-balanced",
      "bootstrap_capability": "standard"
    }
  },
  "dc": {
    "example-directory": {
      "source_image": "projects/PROJECT/global/images/example-directory-v1",
      "machine_type": "e2-standard-4",
      "disk_size_gb": 100,
      "disk_type": "pd-balanced",
      "bootstrap_capability": "prepromoted-domain-controller",
      "domain_dns_name": "example.test",
      "domain_netbios_name": "EXAMPLE"
    }
  }
}
```

The supported bootstrap capabilities are `standard`,
`prepromoted-domain-controller`, and
`preconfigured-machine-host`. Other well-formed capability values remain
parseable so the adapter can return the stable `unsupported-capability` result
when a scenario selects them.

An exact machine-image entry uses this conditional shape:

```json
{
  "kali": {
    "nested-host": {
      "source_machine_image": "projects/PROJECT/global/machineImages/nested-host-v1",
      "machine_type": "n2-standard-8",
      "bootstrap_capability": "preconfigured-machine-host",
      "participant_container_name": "participant-desktop",
      "participant_username": "operator",
      "participant_readiness_contract": "participant-readiness/v1",
      "participant_readiness_manifest_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "host_ssh_username": "hostadmin",
      "host_ssh_port": 2222,
      "allow_public_web_egress": true
    }
  }
}
```

Machine-image profiles inherit captured disks only. Clone requests replace
metadata, network interfaces, external-IP posture, identity, labels, tags, and
machine type. Every attached disk is set to auto-delete after create and again
before destroy. The image must publish its participant RDP endpoint on host port
3389 and create `/run/shifter/preconfigured-range-host.ready` only after its
contained workload is ready. That marker proves boot liveness, not participant
readiness. After installing the participant credential, the provisioner runs

`/usr/local/libexec/shifter-participant-readiness --contract participant-readiness/v1 --manifest-sha256 <digest>`

inside the configured container as the configured participant user. The image
owns that fixed executable and manifest. It must exercise the real browser
launcher with disposable state, validate browser trust/start state/clean home
and projected material, emit no sensitive output, and return nonzero on any
mismatch. Shifter discards its output and exposes only bounded pass/failure
codes. Do not qualify a fresh profile for participant use from the marker
alone; the immutable image, participant canary, content-readback, egress, and
shared-service evidence required by
`docs/architecture/nested-ctf-participant-readiness-preflight-1910.md` must also
pass before the range is handed off.

The participant-qualification record must bind the exact machine-image resource,
profile key/fingerprint, canary contract and manifest digest; content-readback
and participant-canary reason codes; the exact opt-in web rule and regional NAT
(plus a non-opt-in negative); and the generation-bound model grant, broker
destination, broker-only invocation identity, and post-destroy revocation. Keep
URLs, CA material, mission content, model bodies, receipt bytes, guest output,
IAM policy dumps, and provider response bodies out of that evidence. The current
boot marker alone is not a release qualification record.

`prepromoted-domain-controller` profile is usable only when both domain fields
match the scenario's `dc_config` (case-insensitively, with an optional trailing
DNS dot).

The provisioner records a bounded key/profile fingerprint on each new VM and
in existing provider metadata. Reconciliation rejects a keyed same-name VM if
that binding differs from the current plan. Destroy remains independent of the
mapping. Configure the keyed map and validate both keyed and unkeyed launches
before returning the default Kali/DC variables from a temporary single-scenario
workaround to their generic families. Cross-project image families require the
narrow image-project grant for the provisioner GSA; do not broaden portal or
launcher identities.

Images are native GCE images referenced by family:
`projects/<project>/global/images/family/shifter-<type>`. Unlike the GDC path
there is no qcow2 export or CDI import.

## Service accounts

Native range guests receive no platform cloud identity. Exact preconfigured
machine-image profiles may use the bounded range-host identity pool selected by
the range allocation slot. Pool identities have no roles by default; they are
host-infrastructure identities, not per-range model authorization.

New ranges no longer mint provider service-account keys or copy a shared model
key into guests. Legacy key revocation remains part of teardown. Model access
must use the broker contract in ADR-059; adapter compatibility does not establish
that the broker lifecycle or a particular provider has been qualified.

## Baking a new pre-promoted DC image

Domain controllers are **pre-promoted at bake time** so a range boots an
already-promoted DC with no per-range promotion (promotion takes ~15-20 minutes
and would dominate time-to-serve). One parameterized Packer template,
`shifter/packer/gcp/dc-prebaked.pkr.hcl`, bakes many DC images. At range setup
the DC is only **verified** (that it is already the expected promoted domain),
never promoted.

Runtime DC mutation is **disabled and unreachable**. Two paths are locked off,
each retained as code for a future, explicitly authorized decision but selectable
by nothing:

- **Runtime promotion**: `_should_promote_dc_at_runtime` always returns `False`
  (no provider default, no `DC_RUNTIME_PROMOTION` env escape hatch). A DC image
  that is not actually pre-promoted fails verification at setup rather than
  silently promoting.
- **Runtime bootstrap/rename**: `_should_run_dc_bootstrap_plan` always returns
  `False` (no provider default, no `DC_BOOTSTRAP_VIA_SETUP_PLAN` env escape
  hatch). The DC `BootstrapPlan` renames the guest, which would mutate a
  pre-promoted DC's AD identity; the provisioner instead gets SSH to the DC from
  the guest metadata startup script (host key + `administrators_authorized_keys`).

The generic template requires an explicitly selected profile containing the DNS
name, NetBIOS name, image purpose and content seed path. The checked-in example
profile documents these inputs; there is no private default. Core's image build
workflow rejects missing or unknown profiles before cloud authentication.

Pack-specific profiles, content seeds, container stacks and image recipes belong
in the pack owner's repository and build pipeline. Publish their exact image
identities through the supported image/preparation contract. Do not add a pack
name to core workflow choices or dispatch on an image alias in the provisioner.
The consuming scenario's domain intent must match the baked image identity.

## NGFW

There is no GCE-native NGFW (Palo Alto VM-Series) path; that path exists only
under the GDC backend. A GCP range that requests an NGFW is not supported while
the backend is `gce`.
