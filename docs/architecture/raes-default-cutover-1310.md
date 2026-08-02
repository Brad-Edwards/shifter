# RAES default cutover — design (#1310)

Status: mechanism delivered; **activation deferred**. This note records the design
of the ADR-024 default cutover of the stable public scenario id `polaris` to the
RAES-native path. Binding rules: ADR-024 (parity-gated reversible cutover),
ADR-031-R6 (registry-owned source routing + persisted-kind lifecycle).

## Problem

RAES is meant to become the direct realization path (ADR-031). But dev routes to
RAES by *membership* (`_is_raes_scenario`: a `RaesPackageSource` whose
`scenario_id` equals the launch id exists). A legacy public id like `polaris`
therefore cannot be cut over to RAES that way — registering a `polaris` source
collides with the legacy `polaris` scenario (the no-shadow guard rejects it).

## Mechanism (delivered)

A validated **catalog source-route selector** maps a stable public id to a
*distinct* registered RAES source id, so the legacy public id resolves to RAES
with no collision:

- `SHIFTER_RAES_CATALOG_CUTOVERS=polaris=polaris-raes` — parsed in
  `config/_raes_settings.py` to an immutable map; strict slug-pair grammar,
  distinct/unique ids, fail-closed two-key posture (a non-empty route requires
  `SHIFTER_RAES_NATIVE_PROVISIONING`); `ImproperlyConfigured` on bad shape.
- **Registry owns resolution** (`cms/scenarios/cutover.py`): `resolve_launch` is
  the single decision both the catalog projection (`apply_cutover_routes` in
  `list_all_scenarios`) and `create_range_dispatch` consume. A routed public id
  becomes the RAES-backed launch choice keeping its public id + `ScenarioMetadata`
  access; the internal source id is suppressed as a second choice; a
  dangling/non-conformant route is non-launchable (never silent legacy).
- **Dispatch** consumes that one resolution (no second `_is_raes_scenario`
  decision); `_create_raes_native_range_impl` persists/correlates the public id
  while loading the distinct internal source.
- **Lifecycle** (`engine/services/_range_by_request.py`): teardown selects
  `start_raes_range_teardown` vs `start_range_teardown` from the persisted
  `range_config.kind` (`raes_provisioning_plan`), never the selector, so an
  existing RAES range stays destroyable through rollback.
- **Fleet-uniform delivery**: both env vars reach every process — GCP Kustomize
  `platform-runtime.env` + Helm `runtimeEnv`; AWS SSM module → `user_data.sh`
  bootstrap and `deploy_portal.sh` redeploy, threaded through dev/proof/prod.

## Posture / rollback

Shipped default is the **preserved-legacy posture** (empty route, native off) —
nothing changes at merge. Rollback = empty the route, then disable the native
flag, fleet-wide → a new `polaris` launch resolves to legacy while existing RAES
ranges remain destroyable. The selector + flag are the temporary, reversible
rollback line converging on the ADR-031 one-direct-path target; they are expected
to be retired in a near-future issue.

## Deferred to the scenario-pack plugin work

Activating the default (setting the two env vars per environment) requires a
conformance-passed `polaris-raes` pack registered through the canonical
`register_pack`/`bootstrap_inbox_catalog` path. That, plus the
verification→conformance-promotion→launchability bridge (no promotion path exists
today; registration lands `pending`), is the scenario-pack plugin work
(ADR-033/034/041) and is out of scope here. Until it lands, a routed `polaris`
fails closed (non-launchable) — accepted for dev tenants.

## Known limitation (cosmetic, inert until activation)

The scenario-**detail** API (`cms/api/views.py` `ScenarioResourceView.get` via
`get_scenario_detail`) is not route-aware: while a route is active it would return
the legacy structural detail for a routed public id. This is display-only — launch
and access are correct (both go through the overlay-aware `list_all_scenarios` /
`_assert_scenario_launchable`) — and inert while the shipped route is empty. It is
tracked for the display-surface pass that accompanies activation.
