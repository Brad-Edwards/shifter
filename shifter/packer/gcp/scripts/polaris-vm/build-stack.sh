#!/usr/bin/env bash
# POLARIS polaris-vm GDC image bake.
#
# Ports the AWS first-boot bring-up (scripts/polaris-aws-range/user_data.sh.tpl)
# into a packer build-time provisioner. The polaris-vm host is an Ubuntu docker
# host that runs the 17-service NORTHSTORM compose stack (a14-kali is a
# container, not the host OS). This script installs Docker + the compose v2
# plugin, fetches the private build tarball (the compose stack + per-asset
# Dockerfiles + baked flag content) from GCS, and `docker compose build`s every
# image so the golden qcow2 ships with all images present.
#
# What is intentionally NOT done here (it is per-range and belongs to the GDC
# range-bootstrap seam, replacing the AWS SSM PolarisRangeBootstrapPlan):
#   - writing docker-compose.override.yml (DC IP, kali authorized_key, splice
#     keypair) for the specific range
#   - `docker compose up -d`
#   - the polaris-splice-watcher host service
# Host sshd is left running (unlike the AWS host, which masks it and uses SSM):
# the GDC range setup-runner reaches the guest over host SSH.
set -euo pipefail
exec > >(tee /var/log/polaris-vm-bake.log) 2>&1
echo "=== polaris-vm bake starting $(date -u +%FT%TZ) ==="

export DEBIAN_FRONTEND=noninteractive

# The build tarball URI is injected by the packer build (var -> env). It is a
# private GCS object (contains CTF flag answers) mirrored from the AWS S3 bake
# tarball; keep it out of source control.
: "${POLARIS_BUILD_TARBALL_URI:?POLARIS_BUILD_TARBALL_URI must be set (gs://... to the polaris build tarball)}"
COMPOSE_VERSION="${DOCKER_COMPOSE_VERSION:-v2.29.7}"

# Let any on-boot unattended-upgrades finish before we grab the dpkg lock.
for _ in 1 2 3 4 5; do
  fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
  echo "dpkg lock held, waiting..."
  sleep 5
done

apt-get update
apt-get install -y docker.io jq unzip curl ca-certificates openssh-client sudo
systemctl enable --now docker

# The scenario models this host as an os_type=kali attacker, so the GDC range
# setup-runner SSHes in as `kali` and the kali.sh.j2 cloud-init seed installs
# the per-instance key into /home/kali/.ssh (owner kali:kali). The generic kali
# image ships that user; this Ubuntu docker host must provide it too. Give it:
#   - passwordless sudo (LinuxBootstrapPlan runs privileged host commands),
#   - docker group membership (the polaris range-bootstrap seam runs
#     `docker compose ...` over this SSH session, not via sudo),
#   - ownership of /opt/polaris so the seam can rewrite docker-compose.override.
# Match the generic kali image: cloud-init's default_user is kali and the
# account stays unlocked so a per-instance password can be set on first boot.
if ! id kali >/dev/null 2>&1; then
  useradd -m -s /bin/bash kali
fi
usermod -aG sudo,docker kali
install -d -m 0755 /etc/sudoers.d
echo 'kali ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-kali
chmod 0440 /etc/sudoers.d/90-kali
cat > /etc/cloud/cloud.cfg.d/90_shifter.cfg <<'EOF'
system_info:
  default_user:
    name: kali
    lock_passwd: false
EOF

# docker compose v2 plugin is not in the 22.04 apt repo; install the release
# binary directly (same version the AWS bake pins).
install -d /usr/libexec/docker/cli-plugins
curl -fsSL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/libexec/docker/cli-plugins/docker-compose
chmod +x /usr/libexec/docker/cli-plugins/docker-compose
docker compose version

# Fetch + unpack the private build tarball. The GCE builder runs with the
# cloud-platform scope, so gsutil (bundled in the Google guest image) can read
# the object with the builder service account.
install -d /opt/polaris
gsutil cp "${POLARIS_BUILD_TARBALL_URI}" /opt/polaris/polaris-build.tar.gz
tar xzf /opt/polaris/polaris-build.tar.gz -C /opt/polaris
rm -f /opt/polaris/polaris-build.tar.gz
# The range-bootstrap seam (run over SSH as kali) rewrites
# docker-compose.override.yml under this tree, so kali must own it.
chown -R kali:kali /opt/polaris

BUILD_DIR=/opt/polaris/scenario-dev/polaris/build
test -f "${BUILD_DIR}/docker-compose.yml" || {
  echo "ERROR: docker-compose.yml not found under ${BUILD_DIR}" >&2
  exit 1
}
cd "${BUILD_DIR}"

# Bake every image: `build:` services get built from their Dockerfile (which
# runs the flag-content generators), and registry-image services get pulled so
# the golden image has no first-run network dependency.
docker compose build
docker compose pull --ignore-buildable || true

echo "=== baked images ==="
docker image ls

# Leave the stack stopped; the range-bootstrap seam writes the per-range
# override and runs `docker compose up -d` at range start.
echo "=== polaris-vm bake complete $(date -u +%FT%TZ) ==="
