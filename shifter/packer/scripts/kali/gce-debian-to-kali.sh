#!/bin/bash
# Convert a Google debian-12 GCE base image into Kali Rolling, in place.
#
# GCP publishes no Kali image, and the official Kali genericcloud disk is not
# GCE-bootable: it lacks Google's guest environment (no google-guest-agent, so
# no metadata-based SSH-key injection, and no GCE network setup), which means
# packer can never connect. Building on Google's debian-12 base keeps that guest
# environment intact and layers Kali on top via Kali's official apt repository,
# so the IAP/SSH build path behaves exactly like the ubuntu builder.
#
# The Kali repos do NOT carry google-guest-agent, so a naive full-upgrade would
# strip it and reproduce the no-SSH failure in the published image. This script
# re-asserts Google's guest-environment apt repo and reinstalls the agents after
# the conversion so the captured image stays GCE-native.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== Prerequisites ==="
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg

echo "=== Adding Kali official apt repository + keyring ==="
curl -fsSL --proto-redir =https https://archive.kali.org/archive-keyring.gpg \
  -o /usr/share/keyrings/kali-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/kali-archive-keyring.gpg] http://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware" \
  > /etc/apt/sources.list.d/kali.list
# Track Kali Rolling as the system distro: drop the Debian suite lists so the
# full-upgrade below moves the whole base onto Kali.
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources

echo "=== Pinning the Google guest environment so the conversion keeps it ==="
curl -fsSL --proto-redir =https https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt google-compute-engine-bookworm-stable main" \
  > /etc/apt/sources.list.d/google-compute-engine.list

echo "=== Upgrading the base into Kali Rolling ==="
# The debian-12 (pre-t64) -> kali-rolling (post-t64) jump crosses the 64-bit
# time_t library transition, where the new tNN library packages ship files that
# still belong to the old pre-transition packages ("trying to overwrite
# .../libcurl-gnutls.so.4.8.0, which is also in package libcurl3-gnutls").
# --force-overwrite is the documented way through that transition: run the
# upgrade forcing the overwrites, repair any partial dpkg state, settle
# dependencies, then re-run the upgrade which must now complete cleanly.
apt-get update
apt-get -y \
  -o Dpkg::Options::="--force-confnew" \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-overwrite" \
  full-upgrade || true
dpkg --configure -a --force-overwrite || true
apt-get -y -o Dpkg::Options::="--force-overwrite" --fix-broken install
apt-get -y \
  -o Dpkg::Options::="--force-confnew" \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-overwrite" \
  full-upgrade

echo "=== Reinstalling the Google guest environment (Kali repos omit it) ==="
apt-get install -y google-guest-agent google-osconfig-agent
systemctl enable google-guest-agent.service || true
systemctl enable google-osconfig-agent.service || true

echo "=== Creating the kali user (Kali scripts and xrdp expect it) ==="
if ! id kali >/dev/null 2>&1; then
  useradd -m -s /bin/bash kali
  echo 'kali:kali' | chpasswd
  usermod -aG sudo kali
fi

echo "=== Debian -> Kali Rolling conversion complete ==="
