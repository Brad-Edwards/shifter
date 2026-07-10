# REV1 Security Review

## Overall posture

Shifter is materially stronger than a typical pre-OSS platform. The DRF API is
authenticated by default, opaque API tokens are scope checked and compared in
constant time, malformed bearer credentials fail closed rather than falling
back to a browser session, range reads are owner-first, containers and network
policies are hardened, and the provisioner admission policy is unusually
thoughtful. The reviewed production paths generally use ORM queries,
serializers/Pydantic, `yaml.safe_load`, path-containment checks, and configured
network endpoints. No credible generic SQL injection, unsafe YAML load, generic
path traversal, or user-controlled SSRF was found.

The principal risk is inconsistent trust boundaries rather than generally
unsafe coding.

## S1: Self-asserted user type grants organizer authority

**Severity: critical**

[`user_type_sync.py`](../../../shifter/shifter_platform/config/user_type_sync.py)
documents `custom:user_type` as self-mutable and maps `ctf_organizer` to the
global CTF Organizer group. Organizer authorization in
[`ctf/api/_base.py`](../../../shifter/shifter_platform/ctf/api/_base.py) is based
on that global membership. Organizer views can create events and reach
provisioning workflows.

Closed issue #937 explicitly accepted the invariant that self-mutation could
not grant organizer or admin authority. The current implementation violates
that invariant.

**Required remediation:** remove organizer from the self-asserted mapping; bind
organizer authority to an administrator-controlled provider claim or local
assignment; audit and migrate current group members; and add negative tests that
mutating participant profile claims cannot acquire organizer, staff, or
provisioning authority.

## S2: GCP workload identities are project-wide

**Severity: high**

[`platform/terraform/gcp/modules/portal/iam/main.tf`](../../../platform/terraform/gcp/modules/portal/iam/main.tf)
grants portal/worker/scheduler access to all project secrets and gives portal or
provisioner identities broad project storage roles.

**Impact:** a web or worker compromise can read unrelated secrets or mutate and
delete unrelated project objects; a provisioner compromise has project-wide
secret administration impact.

**Required remediation:** use per-secret and per-bucket IAM or tightly scoped
custom roles/conditions; isolate provisioner secret lifecycle by naming
condition or project; add Terraform guards against project-level
`secretAccessor`, `secretAdmin`, and object-admin grants for application service
accounts.

## S3: Kubernetes Secret integrity is not separated by workload

**Severity: high**

[`rbac-job-launcher.yaml`](../../../platform/charts/shifter/templates/rbac-job-launcher.yaml)
allows portal, scheduler, and workers to list Jobs and create, patch, and delete
Secrets. Provisioner Job Secrets carry high-value database and encryption
material. The admission policy validates Job creation but does not protect the
associated Secret mutation path.

**Impact:** a compromised portal or scheduler service account can discover a
provisioner Secret name and race-patch or delete its credentials, crossing the
intended worker boundary.

**Required remediation:** only a dedicated worker/launcher identity should
create provisioner Jobs and manage their Secrets; split Roles and service
accounts, use `resourceNames` where possible, deny portal/scheduler Secret
mutation, and add adversarial authorization tests.

## S4: Build and deployment provenance is not verifiable

**Severity: high**

Credentialed workflows include mutable action tags, including Packer setup, and
several image build paths use floating container bases. The provisioner image
downloads Terraform and Pulumi without consistently verifying checksums or
signatures and installs a separate unhashed requirement set.

**Impact:** compromise of a mutable action, tag, base image, or downloaded tool
can gain cloud OIDC credentials or ship privileged provisioning code. Runtime
hardening cannot compensate for a compromised build.

**Required remediation:** SHA-pin every action in credentialed workflows;
digest-pin runtime bases; checksum/signature verify downloaded CLIs; install
Python dependencies from the reviewed lock/export; generate SBOMs and signed
provenance attestations; and enforce image identity at deployment. Issue #1498
covers known vulnerability alerts, not provenance.

## S5: Browser security policy lacks a global baseline

**Severity: medium**

Production enables HSTS, no-sniff, frame denial, and secure cookies, but there is
no global CSP, Referrer-Policy, or Permissions-Policy. Only a narrow invite
surface sets a no-referrer response.

**Required remediation:** inventory inline scripts/styles; deploy CSP in
Report-Only with reporting; remove or nonce/hash unsafe inline behavior before
enforcement; set a global referrer and permissions policy; and add response
tests. This is defense in depth and does not replace XSS remediation.

## S6: OIDC administrator bootstrap does not require verified email

**Severity: medium**

[`config/oidc.py`](../../../shifter/shifter_platform/config/oidc.py) applies
staff/superuser flags from an email-derived bootstrap rule without checking the
OIDC `email_verified` claim. The Identity Platform path does perform a verified
email check, and current Cognito configuration normally verifies email, but the
application privilege boundary is weaker than the identity-provider assumption.

**Required remediation:** reject missing or false `email_verified` before user
lookup or elevation, bind existing accounts to issuer and subject, and test
provider drift/federation cases.

## Planned security work to preserve

Do not duplicate these existing issues:

- #1206: participant login design without magic-link account takeover.
- #322: launch backpressure and abuse controls.
- #1171: zero-egress posture for commercial self-service ranges.
- #1377: AWS range credential exposure through instance metadata.
- #1295: mesh and range escape containment.
- #201: blocked-request WAF logging and retention.

## Validation limits

This was a static review of the pinned baseline. It did not include live IAM
simulation, CloudTrail/audit-log analysis, DAST, an authenticated browser test,
dependency or image scanning, SBOM verification, or an adversarial race against
Kubernetes admission/RBAC. These should be treated as verification work, not as
evidence that the reviewed controls are ineffective.
