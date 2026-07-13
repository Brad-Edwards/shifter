# Backend-bundle contract migrations

This directory holds the published, versioned backend-bundle contract (issue #1323).

- `backend-bundle-contract.json`: the current published artifact (the contract version, the
  supported versions, the `BackendBundle` JSON schema, and the registered backends). It is
  **generated** from `installation.contract` and `installation.registry`; never hand-edit
  it. Regenerate with `shifter-config contract export`. CI (`installation` test lane) fails
  if it drifts from the code.
- `backend-bundle-contract.v<N>.json`: the **frozen** snapshot of contract version `N`,
  minted the first time version `N` is published. Snapshots are immutable: `export` never
  overwrites one, so a published version's shape cannot silently change. The breaking-change
  gate compares the current artifact against the frozen snapshot of the current version.

The published version is the backend `contract_version`
(`installation.contract.SUPPORTED_CONTRACT_VERSIONS`), independent of `RootConfig.version`
and of the `installation` package version.

## Changing the contract

- **Additive change** (new optional field, new enum value, new backend): edit
  `contract.py` / `registry.py`, run `shifter-config contract export`, and commit the
  updated `backend-bundle-contract.json`. The current version's frozen snapshot stays as-is
  (the change remains backward-compatible with it), so no version bump is required.
- **Backward-incompatible change** (remove a field, remove an enum value, make a field
  required): the breaking-change gate fails against the frozen snapshot unless you, in the
  same change:
  1. bump the version by adding the new value to `SUPPORTED_CONTRACT_VERSIONS` in
     `contract.py` and `_CONTRACT_VERSION` in `registry.py`;
  2. add a `## Contract version <N>` migration note below describing the break and how a
     consumer migrates;
  3. run `shifter-config contract export`, which regenerates the artifact and mints the new
     `backend-bundle-contract.v<N>.json` frozen snapshot. The prior version's snapshot is
     left untouched.

Run `shifter-config contract check` to verify drift, compatibility, and registry
conformance locally before pushing.

## Contract version 1

Initial publication of the backend-bundle contract as a committed, versioned artifact.
Covers the `aws` and `gcp` bundles. No prior version; nothing to migrate.

## Contract version 2

Completed the GCP backend bundle (#729). The `gcp` bundle's published `settings_schema`
changed from `null` (any settings mapping accepted) to a closed model (`GcpBackendSettings`:
required `project_id` and `region`, the composed `range_egress` policy, and
`additionalProperties: false`). The `gcp` `django_secret_key` reference also gained an
enforced `reference_pattern`, and the generated runtime-env projection is now enumerated.

This is a **backward-incompatible** change to the GCP operator-facing settings surface: a
`shifter.yaml` that was valid under version 1's accept-any GCP settings — one missing
`project_id` / `region`, or carrying keys outside the model — now fails validation. Per the
compatibility rules above, a narrowing is a version bump, not an additive change, even
though the mechanical schema-diff gate could not infer the narrowing from a `null` prior
schema (the gate now flags a `null` → concrete `settings_schema` transition as breaking so
this cannot ship un-versioned again).

Migration for GCP deployments: set `settings.project_id` and `settings.region`, and remove
any settings keys outside `project_id`, `region`, and `range_egress`. See `examples/gcp.yaml`.
The `aws` bundle is unchanged (still accept-any until #728); its records are re-published
under version 2 without a shape change.
