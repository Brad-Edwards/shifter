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

## NGFW

There is no GCE-native NGFW (Palo Alto VM-Series) path; that path exists only
under the GDC backend. A GCP range that requests an NGFW is not supported while
the backend is `gce`.
