# installation

This package validates the root Shifter installation config, `shifter.yaml`.

`shifter.yaml` is a user-authored file at the repository root. It selects one
backend bundle and provides deployment-level settings. The installation package
is the authoritative parser for that file.

## Supported Backends

| Backend | Profiles | Required secrets | Settings validation |
| --- | --- | --- | --- |
| `aws` | `prod`, `dev`, `proof` | `django_secret_key`, `db_password` | Closed model: `region` (required). |
| `gcp` | `prod`, `dev` | `django_secret_key` | Any mapping (provisional until #729). |

Both the `aws` (#728) and `gcp` (#729) entries validate their `settings` against a closed
model and each secret reference against a machine-readable grammar; unknown keys fail fast.
For AWS, `region` is required (`proof` is an internal new-tenant readiness tier alongside
`prod`/`dev`). For GCP, `project_id` and `region` are required.

### GCP settings

| Key | Required | Notes |
| --- | --- | --- |
| `project_id` | yes | GCP project id (6-30 chars, a lowercase letter then lowercase letters/digits/hyphens, no trailing hyphen). |
| `region` | yes | GCP region/location, for example `us-central1`. |

The GCP `django_secret_key` reference must be a Google Secret Manager resource name
(`projects/<project>/secrets/<name>/versions/<version>`), a GitHub Actions secret name, an
environment variable, or the literal `prompt`.

`range_egress` is the shared, cross-backend egress policy (see [Render](#render)); it is
validated the same way for every backend and is not part of a backend's own settings model.

## Config File

Start from one of the checked examples:

```bash
cp shifter/installation/examples/aws.yaml shifter.yaml
uv run --project shifter/installation shifter-config validate shifter.yaml
```

Use `examples/gcp.yaml` for GCP.

## Root Fields

| Key | Required | Notes |
| --- | --- | --- |
| `version` | no | Defaults to `1`. Only `1` is accepted. |
| `backend` | yes | Must be `aws` or `gcp`. |
| `deployment.name` | yes | Lowercase letters, digits, and internal hyphens. Length: 1-40 characters. |
| `deployment.domain` | yes | Lowercase DNS hostname with at least two labels. IP literals, schemes, trailing dots, and bare hostnames are rejected. |
| `deployment.profile` | no | Defaults to `prod`. Must be allowed by the selected backend. |
| `secrets` | no | Mapping of logical secret name to a reference. Values must be references, not secret values. |
| `settings` | no | Backend-specific mapping. The root schema only checks that this is a mapping. |

Secret names must match `^[a-z][a-z0-9_]*$`.

Secret references must be single-line strings with no surrounding whitespace.
The root schema rejects recognizable raw secret material, including PEM blocks,
multi-line values, and implausibly long values. It cannot distinguish every short
secret value from a reference; `gitleaks` remains part of the enforcement path.

The literal value `prompt` is accepted for any required secret. It records that
the value must be supplied during deployment.

## Validation

Run validation from the repository root:

```bash
uv run --project shifter/installation shifter-config validate shifter.yaml
```

The command exits `0` when the config is valid. It exits `1` and prints all
detected issues when validation fails.

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

Validation messages are path-based and do not echo rejected input values.

## Render

`shifter-config render` turns the validated `settings.range_egress` policy into
the provider-specific Terraform bridge variables for the config's backend, so
the deployed firewall rules are generated from `shifter.yaml` rather than
hand-copied into a second allowlist (ADR-017-R4, issue #958).

```bash
# AWS: emits `victim_allowed_cidrs = [...]`
uv run --project shifter/installation shifter-config render shifter.yaml \
  --output platform/terraform/environments/<env>/range/victim_allowed_cidrs.auto.tfvars

# GCP: emits `range_egress_mode` + `range_egress_allowed_cidrs`
uv run --project shifter/installation shifter-config render shifter.yaml \
  --output platform/terraform/gcp/environments/gcp-dev/range_egress.auto.tfvars
```

The backend is read from `shifter.yaml`; the renderer emits the matching bridge
variables. Without `--output` the rendered tfvars is written to stdout. The
command exits `1` and prints the same sanitized issues as `validate` when the
config is invalid. See
[`docs/architecture/range-egress-ip-allowlist.md`](../../docs/architecture/range-egress-ip-allowlist.md)
for the full operator workflow.

## Runtime Inventory

Check the checked-in runtime-env inventory from the repository root:

```bash
uv run --project shifter/installation shifter-config runtime-inventory --check
```

The runtime-inventory check compares file paths and env-key names only. It
does not print values. Today it guards the GCP static runtime env, keeps the
tracked generated runtime stub assignment-free, records the generated renderer
key contract, and documents the boundary between the public `shifter.yaml`
installation config, the checked-in `.shifter.yaml` MCP ops policy, and
gitignored local `.env` files.

## Backend Bundle Contract

`contract.py` defines the machine-readable backend bundle contract.
`registry.py` contains the backend entries consumed by the schema and loader.

A backend bundle declares:

- backend identity and supported profiles
- required command-line tools
- required logical secrets and accepted reference grammar
- generated outputs, including destination and sensitivity
- validation checks and health checks
- cloud-neutral capabilities
- owned repository paths and docs

Generated outputs are classified as `public`, `secret-reference`, or
`secret-value`. A `secret-value` output may only be placed in a Kubernetes Secret
or provider secret store.

Validation commands are stored as argv arrays, not shell strings. Command specs
reject shell metacharacters, absolute paths, path traversal, and tokens with
internal whitespace.

## Published Contract

The contract is published as a committed, versioned JSON artifact under
`published_contract/` so downstream backend-bundle authors and tooling can build
against it without reading Shifter internals (issue #1323). `publication.py`
generates the artifact from `contract.py` and `registry.py`; it is never hand-edited.

```bash
# Regenerate the committed artifact from the code.
uv run --project shifter/installation shifter-config contract export

# Fail on drift, unversioned breaking changes, or non-conformant backends.
uv run --project shifter/installation shifter-config contract check
```

The `installation` test lane enforces the same three gates: drift (committed artifact
must match the code), breaking change (an incompatible shape change versus the current
version's immutable frozen snapshot `backend-bundle-contract.v<N>.json` requires a
`contract_version` bump and a migration note), and registry conformance (every published
backend record validates against the published JSON schema). See
`published_contract/MIGRATIONS.md` for the procedure to change the contract.

The published JSON schema encodes the contract's security-relevant validators (identifier
grammars, safe `argv` tokens, repository-relative paths, the secret-value destination rule).
To validate a candidate bundle authoritatively, call
`installation.validate_published_bundle(record)`. It runs the schema plus the full
`BackendBundle` contract (including the cross-collection invariants JSON Schema cannot
express), so a bundle it accepts is one the internal contract accepts too.

## Package Layout

| File | Purpose |
| --- | --- |
| `schema.py` | Root config model and root-field validators. |
| `loader.py` | YAML loading, duplicate-key checks, root validation, and backend validation dispatch. |
| `contract.py` | Backend bundle contract types and invariants. |
| `registry.py` | Supported backend bundle registry. |
| `publication.py` | Generate and check the published, versioned contract artifact. |
| `runtime_inventory.py` | Runtime config surface inventory and env-key drift checker. |
| `cli.py` | `shifter-config validate`, `render`, `runtime-inventory`, and `contract`. |
| `render.py` | Render `settings.range_egress` into provider Terraform bridge tfvars. |
| `errors.py` | Sanitized validation issue model. |
| `published_contract/` | Committed contract artifact, frozen per-version snapshots, and migration notes. |
| `examples/` | Valid AWS and GCP example configs. |
