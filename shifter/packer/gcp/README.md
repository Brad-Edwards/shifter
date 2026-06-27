# Packer GCE Image Builds

Reproducible Google Compute Engine guest-image builds for Shifter ranges
(issue #505, PLAT-001.10). These `googlecompute` templates are the GCP parallel
to the AWS `amazon-ebs` templates in the parent directory.

> **Provider separation.** This directory is a SEPARATE Packer configuration
> from `shifter/packer/`. `packer` reads only the directory it is invoked in
> (it does not recurse), so the AWS `packer build .` / `-only='*.<type>'` flow
> never sees a `googlecompute` source, and the AWS path is unaffected.

## The image-family contract

There is no GCP equivalent of the AWS `/shifter/ami/*` SSM parameter. Instead:

- Each build publishes image `shifter-<type>-<timestamp>` into **image family**
  `shifter-<type>` with labels (`project`, `managed-by`, `image-type`).
- Consumers resolve the **newest non-deprecated image in the family**
  (`gcloud compute images describe-from-family shifter-<type>`).
- Promotion (`packer-gcp-promote.yml`) copies the newest dev-family image into
  the prod project's family and deprecates the previous head.

## Prerequisites

- [Packer](https://www.packer.io/downloads) 1.9+ (`packer init .` installs the
  `googlecompute` plugin)
- A GCP project with the Compute Engine API enabled and a build network/subnet
- A service account with image-build permissions (Compute Instance Admin,
  Service Account User, Storage)
- For `kali`: an **imported** Kali base image (see below)

## Quick start

```bash
packer init .
# Validate (uses the committed placeholder var-file)
packer validate -var-file=dev.pkrvars.hcl .
# Build one image type. Real builds override the placeholder project/network/SA.
packer build -only='googlecompute.ubuntu' \
  -var="project_id=my-proj" -var="zone=us-central1-a" \
  -var="network=default" -var="subnetwork=default" \
  -var="service_account_email=packer-builder@my-proj.iam.gserviceaccount.com" \
  -var="image_prefix=shifter" -var="machine_type=e2-standard-2" \
  -var="use_internal_ip=false" .
```

In CI, variables are passed as `PKR_VAR_*` environment variables (never `-var`
CLI flags) so secrets cannot appear in a process list. See
`.github/workflows/packer-gcp.yml`.

## Image types

| Type | Source | Notes |
|------|--------|-------|
| `ubuntu` | `ubuntu-2204-lts` (ubuntu-os-cloud) | Reuses `../scripts/ubuntu` |
| `brokenbk` | `ubuntu-2204-lts` (ubuntu-os-cloud) | Reuses `../scripts/brokenbk` |
| `kali` | **operator-imported** (`kali_source_image`) | No public Kali GCP image |
| `windows` | `windows-2022` (windows-cloud) | WinRM + GCESysprep |
| `dc` | `windows-2022` (windows-cloud) | AD DS via `PACKER_ROLE=dc` |

### Kali base image

GCP has no public Kali image (the AWS path uses an AWS Marketplace product
code). Import one first, then pass it as `kali_source_image`:

```bash
gcloud compute images import shifter-kali-base \
  --source-file=gs://<bucket>/kali-rolling.tar.gz --os=debian-11
packer build -only='googlecompute.kali' -var="kali_source_image=shifter-kali-base" ...
```

The build fails loud if `kali_source_image` is unset.

### Windows / DC

The `googlecompute` builder has no auto-generated Windows password (unlike the
AWS path's `build.Password`). A throwaway local admin is created on the builder
VM from a per-build `winrm_bootstrap_password`, injected by CI via
`PKR_VAR_winrm_bootstrap_password` — never committed. The VM is generalized with
`GCESysprep` (`scripts/windows/sysprep.ps1`) and discarded, so the credential
never reaches the published image.

Packer connects over **WinRM-over-TLS only** (port 5986): the shared bootstrap
in `locals.pkr.hcl` stands up an HTTPS listener bound to a self-signed cert,
disables HTTP Basic and unencrypted transport, and opens only 5986. The
bootstrap password therefore never crosses the wire in cleartext. `winrm_insecure`
is set only to skip validation of the ephemeral builder's self-signed cert; the
channel is still encrypted.

> **Live validation.** Until a GCP project is bootstrapped (network, build SA,
> and — for Windows — quota), these builds are validated statically
> (`packer validate`, the `tests/test_packer_gcp.py` suite) but have not been
> run end-to-end. Run a `workflow_dispatch` of `packer-gcp.yml` against a real
> project to close the live-build acceptance criteria.

## Guest specialization

The Linux builders reuse the shared `../scripts/**` provisioning. Those scripts
carry AWS-specific guest tuning (the SSM agent; Claude Code's Bedrock binding).
Replacing that with GCP-native equivalents (OS-management agent; Vertex) is a
guest-runtime / range-provisioning concern tracked separately from this
image-build pipeline (per the #505 architecture preflight non-goals).
