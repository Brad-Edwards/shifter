# Root-Configured Backend Bundles

Status: current architecture, constrained by ADR-011

Tracking issue: <https://github.com/Brad-Edwards/shifter/issues/1109>

## Summary

Shifter uses one root installation config, `shifter.yaml`, to select the
deployment backend and deployment profile.

The implementation lives in `shifter/installation/`:

- `schema.py` validates root fields.
- `loader.py` reads YAML, rejects duplicate keys and merge keys, and dispatches
  backend checks.
- `contract.py` defines the backend bundle contract.
- `registry.py` contains the supported backend bundles.
- `runtime_inventory.py` records checked-in runtime config surfaces and validates
  env-key drift without reading values.
- `cli.py` exposes `shifter-config validate`.
- `examples/` contains validated AWS and GCP examples.

Contract publication guidance for issue #1323 lives in
`docs/architecture/backend-bundle-contract-publication-preflight-1323.md`.

Published operator docs:

- `shifter/installation/README.md`
- `shifter/shifter_platform/documentation/docs/technical/dev/installation-config.md`

## Root Config Boundary

`shifter.yaml` is user-authored installation intent. It is not a Terraform
output file, Helm values file, generated runtime environment file, Kubernetes
manifest, or CI branch selector.

The root config owns:

- schema version
- selected backend
- deployment name
- deployment domain
- deployment profile
- logical secret references
- backend-specific settings mapping

`.shifter.yaml` is a separate checked-in policy namespace for `mcp/ops`. It is
not the public installation config and must not become a deployment secret
store. Gitignored `.env` files remain local/operator inputs only; checked-in
runtime env files are either static non-secret overlays or generated
placeholders validated by the runtime inventory.

The root schema validates root shape. Backend bundles validate backend-owned
settings and secret reference grammar when they declare those validators.

## Supported Backends

| Backend | Profiles | Required secrets | Settings validation |
| --- | --- | --- | --- |
| `aws` | `prod`, `dev` | `django_secret_key`, `db_password` | Any mapping accepted by root-config validation. Deployment tooling validates consumed values. |
| `gcp` | `prod`, `dev` | `django_secret_key` | Any mapping accepted by root-config validation. Deployment tooling validates consumed values. |

## Validation

Run from the repository root:

```bash
uv run --project shifter/installation shifter-config validate shifter.yaml
uv run --project shifter/installation shifter-config runtime-inventory --check
```

Validation rejects:

- missing config files
- invalid YAML
- duplicate YAML mapping keys
- YAML merge keys (`<<`)
- non-mapping top-level YAML
- unknown top-level fields
- unknown `deployment` fields
- missing required fields
- unknown backend names
- unsupported profile/backend combinations
- malformed deployment names or domains
- malformed secret names or references
- missing required backend secrets
- secret names not used by the selected backend
- checked-in generated runtime env stubs with assignments
- duplicate keys between static runtime env files and renderer-owned keys
- unregistered checked-in runtime secret env assignments

Validation errors are path-based and do not echo rejected input values.

## Secret Handling

`shifter.yaml` stores references, not secret values.

Accepted reference forms are backend-described strings such as provider secret
names, GitHub Actions secret names, environment variable names, or the literal
`prompt`.

The schema rejects recognizable raw secret material, including PEM blocks,
multi-line values, and implausibly long values. Short raw values can look like
references, so `gitleaks` remains part of enforcement.

Generated backend outputs classify sensitive data as:

- `public`
- `secret-reference`
- `secret-value`

`secret-value` outputs may only be placed in a Kubernetes Secret or provider
secret store.

## Runtime Binding

Backend metadata declares generated outputs consumed by runtime processes. The
current registry declares `CLOUD_PROVIDER` for portal, worker, and provisioner
roles. Django and provisioner code still select cloud adapters from
`CLOUD_PROVIDER` at runtime through the existing cloud factory seams.

Backend selection is not derived from branch names.

## Backend Bundle Contract

A backend bundle declares:

- backend identity and supported profiles
- required command-line tools
- required logical secrets and accepted reference grammar
- generated outputs, destination, sensitivity, and consuming process roles
- validation checks
- health checks
- cloud-neutral capabilities
- backend-owned repository paths and docs

Validation command specs are argv arrays, not shell strings. The contract rejects
shell metacharacters, absolute host paths, path traversal, and tokens with
internal whitespace.

## Published Contract Artifact

The backend-bundle contract is published as a committed, versioned JSON artifact so
downstream backend-bundle authors and tooling can build against it without reading
Shifter internals (issue #1323, ADR-011-R8).

- `shifter/installation/published_contract/backend-bundle-contract.json` — the published
  artifact: the contract version, the supported versions, the `BackendBundle` JSON schema,
  and the registered backends. It is **generated** from `contract.py` and `registry.py`;
  never hand-edit it.
- `shifter/installation/published_contract/backend-bundle-contract.v<N>.json` — the
  immutable frozen snapshot of contract version `N`; the breaking-change gate compares the
  current artifact against the current version's snapshot, and `export` never overwrites one.
- `shifter/installation/published_contract/MIGRATIONS.md` — the per-version changelog and
  migration notes, and the procedure for changing the contract.

The published version is the backend `contract_version`
(`SUPPORTED_CONTRACT_VERSIONS`), independent of `RootConfig.version` and of the
`installation` package version.

Regenerate and check the artifact from the repository root:

```bash
uv run --project shifter/installation shifter-config contract export
uv run --project shifter/installation shifter-config contract check
```

The `installation` test lane enforces three gates, so the published contract cannot fall
behind the code or break silently:

- **drift** — the committed artifact must equal the freshly generated one;
- **breaking change** — a backward-incompatible shape change (removed field, removed enum
  value, newly required field) versus the current version's immutable frozen snapshot
  requires an explicit `contract_version` bump and a migration note;
- **registry conformance** — every published backend record validates against the published
  JSON schema.

A new backend bundle (for example a deferred Azure bundle) is written against this
published artifact — the JSON schema and the `aws`/`gcp` reference entries — plus a
registry entry and a worked `examples/` config. Authors do not need to read Shifter
runtime internals.

### Validating a candidate bundle

The published JSON schema encodes the security-relevant contract validators: identifier
grammars, safe command `argv` tokens (an executable at `argv[0]`, no shell metacharacters,
no absolute paths, no `..` traversal), repository-relative owned paths, the supported
contract versions, and the rule that a `secret-value` output may only target a Kubernetes
Secret or provider secret store. A downstream author can therefore validate a candidate
against the schema with any Draft 2020-12 validator.

`installation.validate_published_bundle(record)` is the **authoritative, parity-complete**
validator: it runs the published schema **and** the full `BackendBundle` contract, so it
additionally enforces the cross-collection invariants JSON Schema cannot express (unique
record names, every validation check's executable listed in `required_tools`). A bundle it
accepts cannot be one the internal contract would reject, closing the supply-chain gap where
a hostile bundle passes a public validator that omits Shifter's custom validators.

## Source Of Truth

Do not create a second root-config parser in scripts, Django settings,
Terraform, Helm, or examples. Import or execute the `shifter/installation`
package instead.

Do not treat CI branch names, Terraform environment directories, Helm values, or
generated env files as additional authoritative backend selectors.
