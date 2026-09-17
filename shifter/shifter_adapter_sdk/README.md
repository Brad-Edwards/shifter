# Shifter adapter SDK

Public contracts for independently packaged Shifter adapters. The SDK contains
no scenario implementation and imports no Shifter application modules.

The verification API is available under
`shifter_adapter_sdk.verification`. Installations select a distribution version
and entry point explicitly; discovery does not execute adapter code.

The optional `preparation` extra provides the closed artifact preparation result
protocol under `shifter_adapter_sdk.preparation`. Successful receipts must carry
phase-specific evidence; a failure carries only a bounded reason code. Receipt
validation does not authorize execution or admit an artifact.

Build with `uv build`. Test installation in a separate environment against the
built wheel, without adding the Shifter checkout to `PYTHONPATH`.

The optional `runtime` extra provides the independent runtime-plugin protocol.
An author image installs this extra and its own distribution, exposing a
zero-argument factory in `shifter.runtime.plugins`. The returned object implements
`plan(RuntimeInput) -> RuntimePlan`. Its container entrypoint is
`python -m shifter_adapter_sdk.worker`; installation selects the exact distribution,
version and entry point and pins the image by digest.

The worker receives `SHIFTER_PLUGIN_INPUT`, checks packaging compatibility for an
`inspect` request, and otherwise emits a bounded guest-action plan. It needs no
network, cloud credentials, platform modules, root privileges, or writable root
filesystem. It runs as UID/GID 65532 and may write only to its bounded `/tmp` mount.
Plugin authors must build images that run under these constraints. Host callers
use `parse_result` with their original input to reject replay and undeclared
targets. Neither a plan nor a compatibility response proves guest readiness.

The initial RAES GCE host asks for `validate`, `configure` and `verify` plans before
creating resources. These requests contain declared node identities and OS
families, not realized IP addresses, keys, provider credentials or secret URLs.
Validation cannot contain guest actions; verification must contain at least one.
The host executes configuration and verification against its own guest map and
uses exit status as evidence. Each binding currently targets exactly one guest.
Guest-only cleanup is performed by core range destruction, independently of the
image registry.

An action may request `runtime_values`, a map from local names to closed
`GuestValueReference(binding=..., field=...)` references. Supported fields are
`private_address` and `participant_ssh_public_key`. The host resolves these from
the exact realized guest bindings and supplies bounded base64 JSON in
`SHIFTER_RUNTIME_VALUES_B64` to that guest action. All references are checked
before the first action; participant keys require declared SSH access and a
verified account credential. The management key is never a fallback. The isolated
worker does not receive these values. Guest Python code can use the standard
library-only `shifter_adapter_sdk.guest.read_runtime_values()` helper when the SDK
is installed, or decode the documented JSON transport directly. Treat values as
data rather than interpolating them into shell source.

Other capabilities and runtime input projections require host
support; an author must not assume they are available merely because installation
compatibility passed.

See the repository's external scenario runtime design for supported integration
and remaining extraction work. This source tree is buildable; it is not evidence
that a package release has been published.

## Releasing the SDK

The SDK has its own version in `pyproject.toml`, independent of platform release
tags. The `Publish adapter SDK` workflow accepts that exact version from `main`,
runs the SDK checks, builds the wheel and source distribution, and verifies the
built wheel in a fresh environment. Its publishing job downloads the exact build
artifact by ID; it does not check out or rebuild source.

Before the first release, configure the PyPI project `shifter-adapter-sdk` with
the GitHub trusted publisher for `Brad-Edwards/shifter`, workflow
`adapter-sdk-release.yml`, environment `adapter-sdk-pypi`. Configure that GitHub
environment's release reviewers and restrict it to `main`. The workflow uses
short-lived OIDC authorization and has no package-index token fallback.

After merging an approved SDK version, dispatch the workflow from `main` with
the exact committed version. A successful publication makes
`pip install 'shifter-adapter-sdk[runtime]==<version>'` available to pack authors.
Preparing this workflow does not create the publisher or publish a package.
