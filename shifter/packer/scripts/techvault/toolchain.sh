#!/bin/bash
# TechVault bake — phase 1: host toolchain (runs as root via Packer sudo).
# Faithful port of the retired techvault-scenario-bake.yml "toolchain" phase.
set -euo pipefail

readonly CLAUDE_CODE_VERSION="2.1.215"
readonly CLAUDE_CODE_TARBALL_SHA256="1a5cf8e491689154264c0b2f28371bf645cdee2903b45c497915868308502d7b"
readonly CLAUDE_CODE_LINUX_X64_TARBALL_SHA256="d160d3ae2c90cb54a7ebc9a1d5c280e6da37ee6cd2624e5701dc5e4dabfbd289"

export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait || true

# Use the signed Ubuntu archive rather than executing remote bootstrap scripts
# as root. Noble supplies Docker Engine, Compose v2, Node.js, npm, pipx, and jq.
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl docker.io docker-compose-v2 jq nodejs npm python3-pip python3-venv
systemctl enable --now docker

# The stack is baked as the ubuntu user (uid 1000, see stack.sh), and aptl's
# very first docker op (the Suricata named-volume seed) runs as ubuntu.
# Package installation does not add ubuntu to the docker group when installed
# as root, so grant it explicitly or the seed fails with docker.sock permission
# denied (surfaced as BackendSeedError).
usermod -aG docker ubuntu

# @anthropic-ai/claude-code has no transitive npm dependencies at this version.
# Fetch its immutable tarball directly, verify the repository-reviewed digest,
# then install offline so npm cannot resolve or substitute registry content.
claude_tgz="$(mktemp --suffix=.tgz)"
claude_native_tgz="$(mktemp --suffix=.tgz)"
cleanup() {
  rm -f "${claude_tgz}" "${claude_native_tgz}"
}
trap cleanup EXIT
curl -fsSL --proto '=https' --proto-redir '=https' \
  -o "${claude_tgz}" \
  "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-${CLAUDE_CODE_VERSION}.tgz"
curl -fsSL --proto '=https' --proto-redir '=https' \
  -o "${claude_native_tgz}" \
  "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-${CLAUDE_CODE_VERSION}.tgz"
printf '%s  %s\n' "${CLAUDE_CODE_TARBALL_SHA256}" "${claude_tgz}" | sha256sum --check --status
printf '%s  %s\n' "${CLAUDE_CODE_LINUX_X64_TARBALL_SHA256}" "${claude_native_tgz}" | sha256sum --check --status
npm install -g --offline "${claude_tgz}" "${claude_native_tgz}"
