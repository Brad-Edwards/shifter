# Native CTF scenario content

Shifter can populate a native CTF event from a private, digest-pinned content
bundle when the event's scenario is selected. The platform creates the
challenges, flags, hints, and prerequisite graph in the same database
transaction as the event. No content import or plugin is required.

This is an operator publication flow. Shifter deliberately does not expose an
upload API for challenge bodies or flag material.

## Contracts

The deployment reference catalog uses
`shifter-ctf-content-references/v1`:

```json
{
  "contract": "shifter-ctf-content-references/v1",
  "references": [
    {
      "scenario_id": "example-lab",
      "object_key": "ctf/content-bundles/example-lab/sha256-digest.json",
      "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

The referenced object uses `shifter-ctf-content/v1`:

```json
{
  "contract": "shifter-ctf-content/v1",
  "scenario_id": "example-lab",
  "challenges": [
    {
      "id": "challenge-001",
      "name": "Example challenge",
      "description": "Participant-facing task.",
      "category": "Discovery",
      "points": 100,
      "difficulty": "easy",
      "order": 1,
      "flags": [
        {
          "type": "http",
          "url": "https://validator.internal.example/verify",
          "method": "POST",
          "timeout": 5,
          "headers": {},
          "case_sensitive": true,
          "order": 0
        }
      ],
      "hints": [],
      "prerequisites": []
    }
  ]
}
```

The parser rejects unknown fields, duplicate JSON keys and identities, unsafe
regular expressions or HTTP validators, malformed prerequisite graphs, and
content over the configured bounds. Bundle challenge IDs are stable,
bundle-local identifiers; prerequisite entries refer to those IDs.

## Publish an immutable object

1. Produce UTF-8 JSON using the bundle contract. Keep the source and generated
   object outside the Shifter repository when it contains private content.
2. Compute the SHA-256 digest over the exact bytes that will be uploaded.
3. Put the digest in the object name and in the deployment reference.
4. Upload with the provider's create-only generation/version precondition.
   Never overwrite a referenced key.
5. Retain the provider object identity and digest in the deployment change
   record.

The portal workload needs read access only:

- GCP: set `ctf_content_bucket_name` on the `gcp-dev` Terraform root. The
  portal workload identity receives `roles/storage.objectViewer` on that
  bucket.
- AWS EC2: set `ctf_content_bucket_arn`, `ctf_content_prefix`, and optionally
  `ctf_content_max_bytes` in the portal environment overlay.
- AWS EKS: set `ctf_content_bucket_arn` and `ctf_content_prefix` in the EKS
  environment overlay and set the bucket, prefix, and maximum bytes in the
  portal workload's `runtime_env`.

The IAM grant is provider identity based. Do not create static storage keys.

## Bind a scenario

Store the complete reference catalog as the
`ctf_content_references` object in the existing application secret. The
entrypoint serializes that field into
`SHIFTER_CTF_CONTENT_REFERENCES_JSON` at process startup. A deployment may
instead inject that environment variable from a private secret, but it must
not put the catalog in a ConfigMap, Terraform output, command argument, or
checked-in file.

The non-secret runtime settings are:

| Setting | Purpose | Default |
|---|---|---|
| `SHIFTER_CTF_CONTENT_BUCKET` | Private object bucket | unset |
| `SHIFTER_CTF_CONTENT_PREFIX` | Contained object-key prefix | `ctf/content-bundles` |
| `SHIFTER_CTF_CONTENT_MAX_BYTES` | Maximum accepted object bytes | `8388608` |

Restart or redeploy the portal and its workers after changing runtime
configuration or the application secret. Startup fails if a reference catalog
is malformed or references are configured without a bucket.

## Create and verify an event

Create an event normally and select the bound scenario. Before the database
transaction starts, Shifter reads the object with a provider identity
precondition, checks its size and declared SHA-256 digest, and validates the
complete bundle. The event, native challenge graph, and hydration receipt then
commit atomically.

Verify:

1. The organizer challenge list has the expected challenge and hint counts.
2. Prerequisite-locked challenges have the expected graph.
3. The audit stream contains a `ctf_content_hydration` create record with the
   digest and bounded counts.
4. Opening registration and activating the event succeeds.

Creating an event for a scenario with no reference is unchanged. A referenced
scenario fails event creation rather than creating partial content when
storage, integrity, validation, native writes, or strict audit recording fails.

## Drift, retry, and rotation

An exact retry against a pristine receipt is a no-op. Any organizer mutation
of managed challenges, flags, hints, or prerequisites marks the receipt
drifted. Activation then fails closed. Shifter never silently merges,
overwrites, or restores drifted content.

To publish a revision:

1. Publish a new immutable object under a new digest key.
2. Update the deployment reference and restart the portal.
3. Create a new event for the revised content.

Do not repoint an event that has already been hydrated. Its scenario is
immutable, and a receipt whose digest no longer matches deployment
configuration is not activation-ready.

## Failure codes

The organizer/API error remains intentionally generic. Server-side reason
codes distinguish the main operator actions:

| Code | Operator action |
|---|---|
| `CTF_CONTENT_NOT_CONFIGURED` | Set the bucket/runtime configuration. |
| `CTF_CONTENT_RESOLUTION_FAILED` | Check object existence and portal read IAM. |
| `CTF_CONTENT_CHANGED` | Publish and reference one stable object generation. |
| `CTF_CONTENT_DIGEST_MISMATCH` | Recompute the digest over the uploaded bytes. |
| `CTF_CONTENT_INVALID` | Validate the bundle contract and native flag policy. |
| `CTF_CONTENT_SCENARIO_MISMATCH` | Align the reference, bundle, and scenario IDs. |
| `CTF_CONTENT_DRIFT` | Create a fresh event; do not overwrite managed state. |
| `CTF_CONTENT_NOT_READY` | Restore the configured reference or create a fresh event. |

Object keys, validator configuration, flag material, and provider exception
text are not included in public errors or audit records.
