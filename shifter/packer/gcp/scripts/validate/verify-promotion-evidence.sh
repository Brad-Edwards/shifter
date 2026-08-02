#!/bin/bash
# Verify that a GCE promotion candidate is bound to a successful protected
# validation run and that run's exact evidence artifact (ADR-004-R23).
set -euo pipefail

for name in SRC_IMAGE SRC_PROJECT IMAGE_FAMILY IMAGE_TYPE VALIDATED_RUN VALIDATED_REVISION EVIDENCE_FILE RUN_FILE; do
  [[ -n "${!name:-}" ]] || { echo "::error::${name} is required" >&2; exit 1; }
done
[[ -f "${EVIDENCE_FILE}" ]] || { echo "::error::validation evidence artifact is missing" >&2; exit 1; }
[[ -f "${RUN_FILE}" ]] || { echo "::error::validation workflow run metadata is missing" >&2; exit 1; }

expect_json() {
  local file="$1" expression="$2" expected="$3" label="$4" actual
  actual="$(jq -er "${expression}" "${file}")" \
    || { echo "::error::${label} is missing or malformed" >&2; exit 1; }
  [[ "${actual}" == "${expected}" ]] \
    || { echo "::error::${label} mismatch" >&2; exit 1; }
}

expect_json "${RUN_FILE}" '.id | tostring' "${VALIDATED_RUN}" "validation run id"
expect_json "${RUN_FILE}" '.name' "Packer GCE Image Validate" "validation workflow name"
expect_json "${RUN_FILE}" '.path' ".github/workflows/packer-gcp-validate.yml" "validation workflow path"
expect_json "${RUN_FILE}" '.event' "workflow_dispatch" "validation event"
expect_json "${RUN_FILE}" '.conclusion' "success" "validation conclusion"
expect_json "${RUN_FILE}" '.head_sha' "${VALIDATED_REVISION}" "validation revision"
branch="$(jq -er '.head_branch' "${RUN_FILE}")"
case "${branch}" in
  dev|main) ;;
  *) echo "::error::validation run did not execute from a protected branch" >&2; exit 1 ;;
esac

expect_json "${EVIDENCE_FILE}" '.candidate_image' "${SRC_IMAGE}" "candidate image"
expect_json "${EVIDENCE_FILE}" '.project' "${SRC_PROJECT}" "candidate project"
expect_json "${EVIDENCE_FILE}" '.image_family' "${IMAGE_FAMILY}" "candidate family"
expect_json "${EVIDENCE_FILE}" '.image_type' "${IMAGE_TYPE}" "candidate image type"
expect_json "${EVIDENCE_FILE}" '.source_revision' "${VALIDATED_REVISION}" "evidence revision"
expect_json "${EVIDENCE_FILE}" '.validation_run | tostring' "${VALIDATED_RUN}" "evidence validation run"
expect_json "${EVIDENCE_FILE}" '.result' "passed" "validation result"

echo "Promotion evidence verified for ${SRC_IMAGE} (run ${VALIDATED_RUN})"
