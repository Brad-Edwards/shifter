#!/bin/bash
# Polaris compose-stack fetch + verify + build for the polaris-vm image bake.
#
# Split out of host-setup.sh so the fail-closed contract can be exercised by a
# behavioral test (#1343 test-quality review): a missing stack, a checksum
# mismatch, an invalid compose config, a failed build/pull, a missing image, or
# failure to create/start every declared service must FAIL the build (non-zero
# exit) when the stack is required, so a
# non-promotable polaris-vm image can never be captured. Runs as a packer
# provisioner AFTER host-setup.sh has installed Docker + the Cloud SDK.
#
# Inputs (env):
#   POLARIS_STACK_BUCKET      GCS bucket holding the compose-stack tarball
#   POLARIS_STACK_KEY         GCS object key (default polaris/stack/...)
#   POLARIS_STACK_GENERATION  optional immutable object generation to pin
#   POLARIS_STACK_SHA256      required tarball digest (when the stack is required)
#   POLARIS_REQUIRE_STACK     1 (default) = fail-closed; 0 = warn + succeed
#   HOST_MGMT_SSH_PORT        for the completion message only
#   POLARIS_STACK_START_TIMEOUT_SECONDS  bounded service-start wait (default 300)
set -euo pipefail

HOST_MGMT_SSH_PORT="${HOST_MGMT_SSH_PORT:-2222}"
POLARIS_STACK_BUCKET="${POLARIS_STACK_BUCKET:-}"
POLARIS_STACK_KEY="${POLARIS_STACK_KEY:-polaris/stack/polaris-stack.tar.gz}"
POLARIS_STACK_GENERATION="${POLARIS_STACK_GENERATION:-}"
POLARIS_STACK_SHA256="${POLARIS_STACK_SHA256:-}"
POLARIS_REQUIRE_STACK="${POLARIS_REQUIRE_STACK:-1}"
POLARIS_STACK_START_TIMEOUT_SECONDS="${POLARIS_STACK_START_TIMEOUT_SECONDS:-300}"
POLARIS_ROOT="${POLARIS_ROOT:-/opt/polaris/scenario-dev/polaris}"
COMPOSE_DIR="${COMPOSE_DIR:-${POLARIS_ROOT}/build}"

if [[ ! "${POLARIS_STACK_START_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "polaris verify-stack: ERROR POLARIS_STACK_START_TIMEOUT_SECONDS must be a non-negative integer." >&2
  exit 1
fi

# fail_stack: emit an error and exit non-zero when the stack is required, or warn
# and succeed when it is not (POLARIS_REQUIRE_STACK=0).
fail_stack() {
  if [[ "${POLARIS_REQUIRE_STACK}" == "1" ]]; then
    echo "polaris verify-stack: ERROR $*" >&2
    exit 1
  fi
  echo "polaris verify-stack: WARNING $* (POLARIS_REQUIRE_STACK=0, leaving host range-ready without the stack)" >&2
  echo "polaris verify-stack: complete (mgmt sshd port ${HOST_MGMT_SSH_PORT}, no stack)"
  exit 0
}

if [[ -z "${POLARIS_STACK_BUCKET}" ]]; then
  fail_stack "POLARIS_STACK_BUCKET is not set; a promotable polaris-vm image requires the compose stack."
fi
if [[ "${POLARIS_REQUIRE_STACK}" == "1" && -z "${POLARIS_STACK_SHA256}" ]]; then
  echo "polaris verify-stack: ERROR POLARIS_STACK_SHA256 is required to verify the compose-stack tarball." >&2
  exit 1
fi

SOURCE_URI="gs://${POLARIS_STACK_BUCKET}/${POLARIS_STACK_KEY}"
if [[ -n "${POLARIS_STACK_GENERATION}" ]]; then
  SOURCE_URI="${SOURCE_URI}#${POLARIS_STACK_GENERATION}"
fi
echo "polaris verify-stack: fetching compose stack from ${SOURCE_URI}"
mkdir -p "${POLARIS_ROOT}"
STACK_TARBALL="$(mktemp)"
gcloud storage cp "${SOURCE_URI}" "${STACK_TARBALL}"

# Verify the tarball digest before it is unpacked or built. A mutable GCS key
# must not be able to change what gets baked without changing the declared hash.
if [[ -n "${POLARIS_STACK_SHA256}" ]]; then
  echo "${POLARIS_STACK_SHA256}  ${STACK_TARBALL}" | sha256sum -c - \
    || { rm -f "${STACK_TARBALL}"; echo "polaris verify-stack: ERROR compose-stack tarball sha256 mismatch." >&2; exit 1; }
fi

rm -rf "${COMPOSE_DIR}"
mkdir -p "${COMPOSE_DIR}"
tar xzf "${STACK_TARBALL}" -C "${COMPOSE_DIR}"
rm -f "${STACK_TARBALL}"

if [[ ! -f "${COMPOSE_DIR}/docker-compose.yml" ]]; then
  fail_stack "no docker-compose.yml at ${COMPOSE_DIR} after extracting the stack tarball."
fi

echo "polaris verify-stack: validating and building compose stack in ${COMPOSE_DIR}"
cd "${COMPOSE_DIR}"
# Fail-closed: an invalid compose file, a failed build, or a failed pull is not
# a promotable image (no `|| true`). --ignore-buildable skips images this stack
# builds locally, so a pull failure is a real registry/auth error.
docker compose config >/dev/null
compose_json="$(docker compose config --format json)"
if ! jq -e '
  (.services | type == "object") and
  ([.services | to_entries[] | select(
    (.value.privileged // false) or
    ((.value.network_mode // "") == "host") or
    ((.value.pid // "") == "host") or
    ((.value.ipc // "") == "host") or
    ([.value.cap_add[]?] | any(. == "ALL" or . == "NET_ADMIN" or . == "SYS_ADMIN")) or
    ([.value.volumes[]? | select(type == "object" and .type == "bind") | .source] |
      any(. == "/" or . == "/run" or . == "/var/run" or startswith("/etc/") or
          startswith("/proc/") or startswith("/sys/") or startswith("/root/") or
          startswith("/home/packer/") or startswith("/var/lib/docker/")))
  )] | length == 0)
' <<<"${compose_json}" >/dev/null; then
  fail_stack "compose stack requests a privileged/host namespace, dangerous capability, or sensitive host bind."
fi
if ! jq -e '
  all(.services | to_entries[];
    (.value.build != null) or
    ((.value.image // "") | test("@sha256:[0-9a-f]{64}$")))
' <<<"${compose_json}" >/dev/null; then
  fail_stack "every pulled compose image must use an immutable sha256 digest."
fi
docker compose build
docker compose pull --ignore-buildable

# Every image the stack references must be present after build+pull; a missing
# image means the baked host cannot start the stack on the range.
missing_images=""
while IFS= read -r img; do
  [[ -z "${img}" ]] && continue
  if ! docker image inspect "${img}" >/dev/null 2>&1; then
    missing_images="${missing_images} ${img}"
  fi
done < <(docker compose config --images)
if [[ -n "${missing_images}" ]]; then
  echo "polaris verify-stack: ERROR compose images missing after build/pull:${missing_images}" >&2
  exit 1
fi

# The builder service account is still attached while Packer provisions the VM.
# Block both host/host-network OUTPUT and Docker-forwarded traffic to the GCE
# metadata IP before any external workload entrypoint executes. Privileged and
# host-namespace services were rejected above so a compromised image cannot
# remove or bypass these rules (#1763 security review).
METADATA_IP="169.254.169.254/32"
iptables -I OUTPUT 1 -d "${METADATA_IP}" -j DROP
iptables -I DOCKER-USER 1 -d "${METADATA_IP}" -j DROP
iptables -C OUTPUT -d "${METADATA_IP}" -j DROP >/dev/null 2>&1 \
  || fail_stack "host metadata isolation rule was not installed."
iptables -C DOCKER-USER -d "${METADATA_IP}" -j DROP >/dev/null 2>&1 \
  || fail_stack "container metadata isolation rule was not installed."

# Create and start every declared container before Packer captures the disk.
# `restart: unless-stopped` can only restore containers that already exist;
# baking images alone leaves a range VM with no target environment (#1763).
echo "polaris verify-stack: creating full compose stack before image capture"
docker compose up -d
mapfile -t services < <(docker compose config --services)
[[ "${#services[@]}" -gt 0 ]] || fail_stack "compose declares no services."
deadline=$(( SECONDS + POLARIS_STACK_START_TIMEOUT_SECONDS ))
while :; do
  notready=""
  for svc in "${services[@]}"; do
    state="$(docker compose ps -a --format '{{.Service}} {{.State}}' \
      | awk -v s="${svc}" '$1==s {print $2; f=1} END{if(!f) print "absent"}')"
    [[ "${state}" == "running" ]] || notready="${notready} ${svc}(${state})"
  done
  [[ -z "${notready}" ]] && break
  if (( SECONDS >= deadline )); then
    fail_stack "compose services not running before image capture:${notready}"
  fi
  echo "polaris verify-stack: waiting for services to reach running:${notready}"
  sleep 15
done

echo "polaris verify-stack: complete (mgmt sshd port ${HOST_MGMT_SSH_PORT}, ${#services[@]} services created and running)"
