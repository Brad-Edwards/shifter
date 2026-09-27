#!/usr/bin/env bash
# Post-deploy live range smoke (issue #218).
#
# Runs ``python manage.py run_post_deploy_smoke`` inside the deployed portal pod
# on EKS via ``kubectl exec``. The AWS portal runtime is EKS (the portal runs as
# the ``portal-web`` Deployment in the platform namespace), so the smoke execs
# into a live portal pod rather than SSM-ing into an ASG/EC2 instance. Requires
# AWS credentials with EKS access, kubectl, and the env vars documented in
# scripts/post_deploy_smoke/README.md.
set -euo pipefail

VARIANT="linux"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --variant)
      VARIANT="${2:?--variant requires a value}"
      shift 2
      ;;
    -h | --help)
      echo "Usage: $0 [--variant linux|windows]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ENVIRONMENT="${ENV:-dev}"
AWS_REGION="${AWS_REGION:-us-east-2}"
CLUSTER_NAME="${SHIFTER_EKS_CLUSTER_NAME:-shifter-${ENVIRONMENT}-eks}"
NAMESPACE="${SHIFTER_PLATFORM_NAMESPACE:-shifter-platform}"
PORTAL_DEPLOYMENT="${SHIFTER_PORTAL_DEPLOYMENT:-deploy/portal-web}"

if [[ -z "${SMOKE_TEST_USER_EMAIL:-}" ]]; then
  echo "::error::SMOKE_TEST_USER_EMAIL is required" >&2
  exit 1
fi

# Point kubectl at the target EKS cluster (idempotent).
aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region "${AWS_REGION}"

# The manage command runs inside the portal pod via `kubectl exec`, which does
# not inherit this job's environment. Forward the smoke user identity explicitly
# with `env`. The smoke provisions ranges from base AMIs (no XDR agent), so there
# are no per-variant agent IDs to forward.
kubectl exec -n "${NAMESPACE}" "${PORTAL_DEPLOYMENT}" -- \
  env "SMOKE_TEST_USER_EMAIL=${SMOKE_TEST_USER_EMAIL}" \
  python manage.py run_post_deploy_smoke --variant "${VARIANT}"
