#!/bin/bash
# Runner-executed TechVault candidate checks. This script runs inside the
# disposable no-SA validation VM over IAP and fails closed on any incomplete
# participant-seat or running-stack invariant.
set -euo pipefail

fail() {
  echo "shifter-validate: FAIL techvault $*" >&2
  return 1
}

TECHVAULT_USER="${TECHVAULT_USER:-ubuntu}"
TECHVAULT_HOME="${TECHVAULT_HOME:-/home/ubuntu}"
COMPOSE_DIR="${TECHVAULT_HOME}/techvault"

systemctl is-active --quiet google-guest-agent || fail "google-guest-agent is not active"
[[ "$(id -u "${TECHVAULT_USER}" 2>/dev/null)" == "1000" ]] || fail "${TECHVAULT_USER} is not UID 1000"
[[ "$(stat -c '%U' "${TECHVAULT_HOME}" 2>/dev/null)" == "${TECHVAULT_USER}" ]] || fail "home ownership is invalid"
docker_members="$(getent group docker | cut -d: -f4)"
printf ',%s,' "${docker_members}" | grep -Fq ",${TECHVAULT_USER}," || fail "${TECHVAULT_USER} is not in the docker group"

for service in docker ssh xrdp; do
  systemctl is-active --quiet "${service}" || fail "${service} is not active"
done
# Capture ss once and match with a here-string. Piping ss into `grep -q` lets
# grep close the pipe on its first match, so ss takes SIGPIPE writing the next
# line and, under `set -o pipefail`, the check spuriously fails even though the
# port is listening (#1782).
listening="$(ss -tlnH)"
grep -q ':22' <<<"${listening}" || fail "participant SSH is not listening on port 22"
grep -q ':3389' <<<"${listening}" || fail "xrdp is not listening on port 3389"
code --version >/dev/null 2>&1 || fail "VS Code is unavailable"
claude --version >/dev/null 2>&1 || fail "Claude Code is unavailable"

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
[[ -f "${COMPOSE_DIR}/aptl.json" ]] || fail "aptl.json is missing"
[[ -f "${COMPOSE_DIR}/docker-compose.yml" ]] || fail "docker-compose.yml is missing"
cd "${COMPOSE_DIR}" || fail "cannot enter ${COMPOSE_DIR}"
docker compose config >/dev/null 2>&1 || fail "compose configuration is invalid"

missing_images=""
while IFS= read -r image; do
  [[ -z "${image}" ]] && continue
  docker image inspect "${image}" >/dev/null 2>&1 || missing_images="${missing_images} ${image}"
done < <(docker compose config --images)
[[ -z "${missing_images}" ]] || fail "baked images are missing:${missing_images}"

mapfile -t running < <(docker ps --filter name=aptl- --filter status=running --format '{{.Names}}')
[[ "${#running[@]}" -ge 30 ]] || fail "only ${#running[@]} aptl containers are running; expected at least 30"
# Match against a captured string, not `printf ... | grep -Fxq`: grep short-
# circuits and printf takes SIGPIPE writing the rest, which pipefail turns into a
# spurious "not running" for a container that IS present (#1782).
running_names="$(printf '%s\n' "${running[@]}")"
for required in aptl-wazuh-manager aptl-victim aptl-kali; do
  grep -Fxq "${required}" <<<"${running_names}" || fail "required container ${required} is not running"
done

mapfile -t unhealthy < <(docker ps -a --filter name=aptl- --filter health=unhealthy --format '{{.Names}}')
[[ "${#unhealthy[@]}" -eq 0 ]] || fail "unhealthy containers:${unhealthy[*]}"

mapfile -t exited < <(docker ps -a --filter name=aptl- --filter status=exited --format '{{.Names}}')
for container in "${exited[@]}"; do
  [[ "${container}" == "aptl-cortex-index-init" ]] || fail "unexpected exited container ${container}"
done
initializer_exit="$(docker inspect --format '{{.State.ExitCode}}' aptl-cortex-index-init 2>/dev/null)" \
  || fail "expected aptl-cortex-index-init container is missing"
[[ "${initializer_exit}" == "0" ]] || fail "aptl-cortex-index-init exited ${initializer_exit}"

echo "shifter-validate: TechVault participant seat and ${#running[@]}-container stack healthy"
