# Registering content packs

Shifter has one way to add scenario content: register a pack. It does not matter
whether the pack shipped in-box, came from a public source, was licensed and
private, or was authored by the operator. The platform never asks how the pack
was obtained. Acquisition and any entitlement are handled out-of-band, before the
pack reaches Shifter (ADR-034).

A pack is registered as a provenance-only reference: Shifter records where the
pack lives (`package_ref`), its version and digest, its contract and profile, and
bounded provenance. It does not copy the pack body into the catalog. Pack content
is defined by the `aces-scenario-packs` contract, and Shifter validates each
incoming pack against that contract as foreign input before accepting it. A
broken, malformed, or non-conformant pack is rejected.

There are three entrypoints onto the same registration service. All of them are
source-agnostic and entitlement-blind; the only access check is who is authorized
to register content (active staff or Threat Research membership, or a token with
the `cms:authoring:write` scope), never whether they were entitled to obtain the
pack.

## API

`POST /api/v1/cms/catalog/packs/` with the `cms:authoring:write` scope:

```json
{
  "scenario_id": "example-pack",
  "source_kind": "repo",
  "contract_kind": "aces",
  "contract_profile": "shifter",
  "package_ref": "scenario-dev/example-pack",
  "package_version": "0.1.0",
  "package_digest": "sha256:<64 hex>",
  "provenance": {"repo": "acme/catalog"}
}
```

A `201` returns the registered `scenario_id`, `source_kind`, and
`conformance_status`. Validation and conflict failures return the standard error
envelope.

## CLI

```sh
python manage.py register_pack \
  --scenario-id example-pack \
  --source-kind repo \
  --package-ref scenario-dev/example-pack \
  --package-version 0.1.0 \
  --package-digest "sha256:<64 hex>" \
  --actor <username>
```

## In-box catalog

The packs Shifter ships by default are declared in
`cms/scenarios/inbox_packs/manifest.yaml` and registered through the same service
by `python manage.py bootstrap_inbox_catalog --actor <username>`. There is no
privileged load path for the in-box catalog: it is registered exactly the way an
operator registers their own content. The bootstrap is idempotent, so it is safe
to run after each deploy. No conformant default packs ship yet, so the manifest
is currently empty.

## Resolution and launchability

`repo` packs resolve under `ACES_PACKAGE_ROOT` with containment enforcement, and
the catalog id must equal the pack's own validated identity (`pack.yaml` name),
so one immutable pack cannot be registered under arbitrary aliases. `object`
(object-storage) packs are registrable but are not launchable until an object
resolver with equivalent containment and immutable-identity guarantees exists
(#1567).

Registration is not conformance and is not launchability. A caller cannot assert
that a pack has passed conformance: every registration lands non-passed, and
conformance is promoted out of band by a trusted conformance process. A
registered pack may remain review-only or non-realizable, and launchability
continues to be decided by the registry.
