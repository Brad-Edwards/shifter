#!/bin/bash
# Verify that a GCE promotion candidate is bound to a successful protected
# validation run and that run's exact evidence artifact (ADR-004-R23).
set -euo pipefail

for name in SRC_IMAGE SRC_IMAGE_ID SRC_PROJECT IMAGE_FAMILY IMAGE_TYPE VALIDATED_RUN \
  VALIDATED_RUN_ATTEMPT VALIDATED_ARTIFACT_ID VALIDATED_REVISION \
  EXPECTED_REPOSITORY EVIDENCE_FILE RUN_FILE ARTIFACT_FILE; do
  [[ -n "${!name:-}" ]] || { echo "::error::${name} is required" >&2; exit 1; }
done
[[ -f "${EVIDENCE_FILE}" ]] || { echo "::error::validation evidence artifact is missing" >&2; exit 1; }
[[ -f "${RUN_FILE}" ]] || { echo "::error::validation workflow run metadata is missing" >&2; exit 1; }
[[ -f "${ARTIFACT_FILE}" ]] || { echo "::error::validation artifact metadata is missing" >&2; exit 1; }

for value_name in SRC_IMAGE_ID VALIDATED_RUN VALIDATED_RUN_ATTEMPT VALIDATED_ARTIFACT_ID; do
  [[ "${!value_name}" =~ ^[0-9]+$ ]] \
    || { echo "::error::${value_name} must be a numeric identifier" >&2; exit 1; }
done
[[ "${VALIDATED_REVISION}" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "::error::VALIDATED_REVISION must be a full commit SHA" >&2; exit 1; }

expect_json() {
  local file="$1" expression="$2" expected="$3" label="$4" actual
  actual="$(jq -er "${expression}" "${file}")" \
    || { echo "::error::${label} is missing or malformed" >&2; exit 1; }
  [[ "${actual}" == "${expected}" ]] \
    || { echo "::error::${label} mismatch" >&2; exit 1; }
}

expect_json "${RUN_FILE}" '.id | tostring' "${VALIDATED_RUN}" "validation run id"
expect_json "${RUN_FILE}" '.run_attempt | tostring' "${VALIDATED_RUN_ATTEMPT}" "validation run attempt"
expect_json "${RUN_FILE}" '.name' "Packer GCE Image Validate" "validation workflow name"
expect_json "${RUN_FILE}" '.path' ".github/workflows/packer-gcp-validate.yml" "validation workflow path"
expect_json "${RUN_FILE}" '.event' "workflow_dispatch" "validation event"
expect_json "${RUN_FILE}" '.conclusion' "success" "validation conclusion"
expect_json "${RUN_FILE}" '.head_sha' "${VALIDATED_REVISION}" "validation revision"
expect_json "${RUN_FILE}" '.repository.full_name' "${EXPECTED_REPOSITORY}" "validation repository"
branch="$(jq -er '.head_branch' "${RUN_FILE}")"
case "${branch}" in
  dev|main) ;;
  *) echo "::error::validation run did not execute from a protected branch" >&2; exit 1 ;;
esac

expect_json "${ARTIFACT_FILE}" '.id | tostring' "${VALIDATED_ARTIFACT_ID}" "validation artifact id"
expect_json "${ARTIFACT_FILE}" '.name' "${IMAGE_TYPE}-gce-validation-evidence" "validation artifact name"
expect_json "${ARTIFACT_FILE}" '.expired | tostring' "false" "validation artifact expiry"
expect_json "${ARTIFACT_FILE}" '.workflow_run.id | tostring' "${VALIDATED_RUN}" "validation artifact run"

expect_json "${EVIDENCE_FILE}" '.schema_version | tostring' "1" "evidence schema version"
expect_json "${EVIDENCE_FILE}" '.repository' "${EXPECTED_REPOSITORY}" "evidence repository"
expect_json "${EVIDENCE_FILE}" '.workflow' ".github/workflows/packer-gcp-validate.yml" "evidence workflow"
expect_json "${EVIDENCE_FILE}" '.source_ref' "refs/heads/${branch}" "evidence source ref"
expect_json "${EVIDENCE_FILE}" '.candidate_image' "${SRC_IMAGE}" "candidate image"
expect_json "${EVIDENCE_FILE}" '.candidate_image_id | tostring' "${SRC_IMAGE_ID}" "candidate image id"
expect_json "${EVIDENCE_FILE}" '.project' "${SRC_PROJECT}" "candidate project"
expect_json "${EVIDENCE_FILE}" '.environment' "dev" "promotion source environment"
expect_json "${EVIDENCE_FILE}" '.image_family' "${IMAGE_FAMILY}" "candidate family"
expect_json "${EVIDENCE_FILE}" '.image_type' "${IMAGE_TYPE}" "candidate image type"
expect_json "${EVIDENCE_FILE}" '.source_revision' "${VALIDATED_REVISION}" "evidence revision"
expect_json "${EVIDENCE_FILE}" '.validation_run | tostring' "${VALIDATED_RUN}" "evidence validation run"
expect_json "${EVIDENCE_FILE}" '.validation_run_attempt | tostring' "${VALIDATED_RUN_ATTEMPT}" "evidence run attempt"
expect_json "${EVIDENCE_FILE}" '(.phases == ["first_boot_health", "reboot_health"]) | tostring' "true" "evidence phases"
expect_json "${EVIDENCE_FILE}" '.result' "passed" "validation result"
validated_at="$(jq -er '.validated_at_utc' "${EVIDENCE_FILE}")" \
  || { echo "::error::validation timestamp is missing or malformed" >&2; exit 1; }
[[ "${validated_at}" =~ ^[0-9]{8}T[0-9]{6}$ ]] \
  || { echo "::error::validation timestamp is malformed" >&2; exit 1; }

echo "Promotion evidence verified for ${SRC_IMAGE} (${SRC_IMAGE_ID}, run ${VALIDATED_RUN}/${VALIDATED_RUN_ATTEMPT}, artifact ${VALIDATED_ARTIFACT_ID})"
