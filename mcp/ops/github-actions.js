// GitHub Actions workflow-dispatch helpers for the shifter-ops MCP
// server (AMI build/promote — issue #411; GCE image build/promote —
// issue #505, PLAT-001.10).
//
// User-controlled values land as literal argv elements via
// buildGhWorkflowRunArgs; no shell interpolation (ADR-010). The GitHub
// token stays child-environment-only (resolved inside ghExec).

import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_GITHUB_REPO,
  buildGhWorkflowRunArgs,
  ghExec,
  resolveGitRef,
} from "./lib.js";

const _HERE = path.dirname(fileURLToPath(import.meta.url));
const _REPO_ROOT = path.resolve(_HERE, "..", "..");

export function triggerAmiWorkflow({ workflow, ami_type, ref, actionsPath }) {
  const branch = ref ?? resolveGitRef(_REPO_ROOT);
  ghExec(
    buildGhWorkflowRunArgs({
      workflow,
      repo: DEFAULT_GITHUB_REPO,
      ref: branch,
      inputs: { ami_type },
    }),
  );
  return (
    `Triggered ${workflow} for ${ami_type} on ref ${branch}. ` +
    `View at: https://github.com/${DEFAULT_GITHUB_REPO}/actions/workflows/${actionsPath}`
  );
}

// GCE image build/promote (issue #505, PLAT-001.10). Parallel to
// triggerAmiWorkflow but passes the GCP-scoped `image_type` workflow input
// (the GCE workflows never use the AWS `ami_type` input name). The dispatch
// `--ref` is the single source of truth for the built branch: the GCE
// workflows check out the dispatched ref (github.sha) rather than a separate
// free-form input, so the branch reported here is the branch actually built.
export function triggerGceImageWorkflow({
  workflow,
  image_type,
  ref,
  actionsPath,
}) {
  const branch = ref ?? resolveGitRef(_REPO_ROOT);
  ghExec(
    buildGhWorkflowRunArgs({
      workflow,
      repo: DEFAULT_GITHUB_REPO,
      ref: branch,
      inputs: { image_type },
    }),
  );
  return (
    `Triggered ${workflow} for ${image_type} on ref ${branch}. ` +
    `View at: https://github.com/${DEFAULT_GITHUB_REPO}/actions/workflows/${actionsPath}`
  );
}
