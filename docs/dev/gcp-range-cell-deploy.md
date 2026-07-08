# GCP GCE range-cell deploy runbook

The GCP range backend defaults to the GCE range-cell path. This runbook covers
enabling it in a real environment, mapping images, and rolling back to GDC.

For the backend design see
`docs/architecture/gcp-range-cell-backend-preflight-1341.md`; for the Polaris
port see `docs/dev/polaris-gcp-range-cell.md`. For the image build see
`docs/architecture/gcp-guest-images.md`.

## Backend selection

`GCP_RANGE_BACKEND` selects the GCP range backend:

- `gce` (default): provision each range as an isolated GCE range cell.
- `gdc`: the retained GDC VM Runtime path.

The default lives in `config.py` (`get_gcp_range_backend`) and the generated
runtime config (`scripts/gcp/render_runtime_env.py`). To roll back an
environment, set the `GCP_RANGE_BACKEND=gdc` repository/environment variable and
redeploy. The GDC configuration block is retained in the rendered contract and
is inert while the backend is `gce`.

## Required configuration

Set the GCE range-cell variables documented in
`docs/dev/deploy-secrets.md` ("GCE range-cell backend variables"). The
minimum for a live range is: `RANGE_NETWORK_ZONE`, `GCP_RANGE_LINUX_IMAGE`,
`GCP_RANGE_DC_IMAGE`, and `GCP_RANGE_HOST_SERVICE_ACCOUNT_EMAIL`. Polaris also
needs `GCP_RANGE_VERTEX_SERVICE_ACCOUNT_EMAIL`.

`GCP_RANGE_CELL_PROJECT_ID` defaults to the project parsed from the range VPC
self-link, so the range backend targets the real range project even when the
control-plane `GCP_PROJECT_ID` is a deploy-overlay placeholder.

## Network mode

`GCP_RANGE_CELL_NETWORK_MODE` selects how range guests are networked:

- `shared-vpc` (default) — each range gets its own subnet in the pre-existing,
  platform-peered range VPC (`RANGE_NETWORK_ID`/`RANGE_VPC_ID`). This mirrors the
  AWS shared-VPC + per-range-subnet model, so the provisioner reaches guests over
  the existing platform↔range peering. Isolation is by per-range subnet and
  target-tag firewall rules (see `docs/architecture/range-isolation-model.md`).
  Requires `RANGE_NETWORK_ID` (or `RANGE_VPC_ID`); both are rendered from the
  `range_network_id` Terraform output.
- `vpc-per-range` — each range mints its own isolated VPC. This gives VPC-hard
  isolation but currently has **no provisioner reachability path** (no peering or
  IAP is created), so guests are unreachable and ranges cannot reach READY. It is
  retained as a selectable mode for a future peering/IAP implementation; do not
  use it for live deployments yet.

## Image mapping

A range instance resolves to one of four image profiles by role and OS
(`GCERangeCellConfig.get_profile`):

| Instance | Profile | Variable |
|---|---|---|
| Domain controller (`role=dc`) | dc | `GCP_RANGE_DC_IMAGE` |
| Kali / attacker | kali | `GCP_RANGE_KALI_IMAGE` |
| Windows guest | windows | `GCP_RANGE_WINDOWS_IMAGE` |
| Everything else (Linux host) | linux | `GCP_RANGE_LINUX_IMAGE` |

For a Polaris deployment the Docker host maps to the linux profile and the
`BOREAS.LOCAL` DC to the dc profile, so set `GCP_RANGE_LINUX_IMAGE` to the
`shifter-polaris-vm` family and `GCP_RANGE_DC_IMAGE` to `shifter-polaris-dc`.
Because there is one image per profile per deployment, a single environment
serves either Polaris hosts or generic Linux guests, not both; run generic
scenarios in a separate deployment or environment.

Images are native GCE images referenced by family:
`projects/<project>/global/images/family/shifter-<type>`. Unlike the GDC path
there is no qcow2 export or CDI import.

## Service accounts

The GCE range-cell backend uses two service accounts:

- **Host SA** (`GCP_RANGE_HOST_SERVICE_ACCOUNT_EMAIL`): attached to every range
  guest. Grant only logging and monitoring write.
- **Vertex SA** (`GCP_RANGE_VERTEX_SERVICE_ACCOUNT_EMAIL`): the identity whose
  short-lived, per-range key the a14-kali agent uses for Vertex AI. Grant only
  `roles/aiplatform.user`. The participant container is blocked from the
  metadata server and reads the key from Secret Manager (see
  `shifter/engine/provisioner/gcp_range_vertex_creds.py`).

These are independent inputs. In an account that requires it, both variables may
name the same service account; nothing in the render or provisioner assumes they
differ.

## Baking a new pre-promoted DC image

Domain controllers are **pre-promoted at bake time** so a range boots an
already-promoted DC with no per-range promotion (promotion takes ~15-20 minutes
and would dominate time-to-serve). One parameterized Packer template,
`shifter/packer/gcp/dc-prebaked.pkr.hcl`, bakes many DC images. At range setup
the DC is only **verified** (that it is already the expected promoted domain),
never promoted.

Runtime DC promotion is **disabled and unreachable**. The runtime-promotion code
path (`DCSetupPlan(runtime_promotion=True)` and its templates) is retained for a
future, explicitly-authorized decision, but nothing can select it:
`_should_promote_dc_at_runtime` always returns `False` (no provider default, no
`DC_RUNTIME_PROMOTION` env escape hatch). A DC image that is not actually
pre-promoted therefore fails verification at setup rather than silently
promoting at runtime.

Each DC image is a **profile** var-file in `shifter/packer/gcp/dc-profiles/`. The
profile sets the domain, NetBIOS name, AD-content seed, and purpose (which names
the image family `shifter-<purpose>-dc`). To add a DC for a new domain:

1. Copy `dc-profiles/example.pkrvars.hcl` to `dc-profiles/<name>.pkrvars.hcl` and
   set `dc_image_purpose`, `dc_domain_name`, `dc_netbios_name`, and
   `dc_content_script`.
2. Add the AD-content seed script the profile points at (path relative to
   `shifter/packer/gcp`). It creates the scenario's OUs/users/groups/SPNs and
   accepts a `-DnsForwarder` parameter; the Polaris one is
   `scripts/polaris-aws-range/a2_setup.ps1`.
3. Run the **Packer GCE Image Build** workflow with `image_type=dc-prebaked` and
   `dc_profile=<name>`. It publishes image family `shifter-<purpose>-dc`.
4. Point the consuming scenario at that family. The scenario's `dc_config`
   `domain_name` must match the baked domain, because setup verifies the
   already-promoted DC against it (it never promotes).

The `polaris` profile reproduces the Polaris `shifter-polaris-dc`
(`BOREAS.LOCAL`) image and is the default.

## NGFW

There is no GCE-native NGFW (Palo Alto VM-Series) path; that path exists only
under the GDC backend. A GCP range that requests an NGFW is not supported while
the backend is `gce`.
