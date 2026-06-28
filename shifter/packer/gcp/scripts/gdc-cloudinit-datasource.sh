#!/usr/bin/env bash
# Configure cloud-init for GDC VM Runtime (GCP guest images only).
#
# GDC VM Runtime delivers each guest's cloud-init userData via the NoCloud
# datasource (a "cidata" seed built from the VirtualMachine
# cloudInit.noCloud.secretRef userData). The GCE base images
# (ubuntu-os-cloud / GCE Debian -> Kali) ship cloud-init locked to the GCE
# datasource, whose metadata server (169.254.169.254) does not exist on the
# isolated range L2 segment. The guest therefore logs "No instance datasource
# found!" and applies none of the range userData (the Ed25519 host key,
# authorized_keys, or the first-boot setup script), which breaks guest setup.
#
# Force NoCloud first so GDC guests consume the range userData. This is
# GCP-only and is intentionally NOT added to the shared cleanup script, because
# AWS images must keep their Ec2 datasource.
set -euo pipefail

cat > /etc/cloud/cloud.cfg.d/99-shifter-gdc-datasource.cfg <<'CFG'
# Shifter GDC VM Runtime guests boot from a NoCloud seed (cidata) built from
# the VirtualMachine cloudInit.noCloud.secretRef userData. Probe NoCloud first;
# keep GCE/None as fallbacks so the image still boots cleanly off GDC.
datasource_list: [ NoCloud, ConfigDrive, GCE, None ]
CFG

# Re-arm cloud-init so the range VM performs a full first-boot run (datasource
# detection + module application, including the injected ssh_keys host key)
# instead of treating the baked image's cached instance-id as already
# provisioned. Run last so no cloud-init state is recreated before capture.
cloud-init clean --logs --seed
