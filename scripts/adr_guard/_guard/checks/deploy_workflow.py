"""Deploy/workflow gating checks and CI action-pinning / OIDC integrity."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from .._common import (
    Violation,
    is_guard_source_path,
)
from .._workflow_model import (
    _CORE_WORKFLOW_PATH,
    _DW_REUSABLE_WORKFLOW_PATHS,
    _DwShapeError,
    _PLATFORM_WORKFLOW_PATH,
    _RANGE_WORKFLOW_PATH,
    _dw_is_self_hosted,
    _dw_job_denied_on_pull_request,
    _dw_job_if,
    _dw_jobs,
    _dw_load_workflow,
)


_DEPLOY_WORKFLOW_PATH = ".github/workflows/deploy.yml"
_PLAN_SCOPE_CHECK = "deploy-workflow-plan-scope"
_PLAN_SCOPE_RULE = "ADR-003-R2"
_TERRAFORM_PLAN_FILE = "tfplan"
_QUALITY_RELEVANT_OUTPUT = (
    "quality_relevant: ${{ steps.quality_non_docs.outputs.non_docs == 'true' || "
    "steps.quality_guardrails.outputs.guardrail_docs == 'true' }}"
)
_QUALITY_RELEVANT_CONDITION = "needs.changes.outputs.quality_relevant == 'true'"
_QUALITY_PREDICATE = "predicate-quantifier: every"
_QUALITY_NON_DOCS_REQUIRED_GLOBS = (
    "**",
    "!docs/**",
    "!**/*.md",
)
_QUALITY_GUARDRAIL_DOCS_REQUIRED_GLOBS = (
    ".github/pull_request_template.md",
    ".github/copilot-instructions.md",
    "docs/adr/**",
    "docs/technical/dev/adr-enforcement.md",
)
_PR_GATE_SKIPPED_QUALITY_GUARD = (
    '[ "$quality_result" = "skipped" ] && [ "$quality_relevant" != "false" ]'
)
_QUALITY_WORKFLOW_PATH = ".github/workflows/_quality.yml"
_SKIP_TESTS_LITERAL = "skip_tests: false"
_SKIP_TESTS_FORBIDDEN_MARKERS = (
    "[skip tests]",
    "[skip quality]",
    "Check for skip flags",
)
# Lint / architecture / security jobs in _quality.yml that must never be gated
# on inputs.skip_tests (ADR-003-R2 / issue #760).
_QUALITY_SKIP_TESTS_IMMUNE_JOB_SUFFIXES = ("-lint", "-lint-js", "-sast", "-arch")
_QUALITY_SKIP_TESTS_IMMUNE_JOB_NAMES = frozenset(
    {
        "adr-conformance",
        "workflow-lint",
        "terraform-lint",
        "security-iac",
        "security-k8s",
        "secrets-gitleaks",
        "k8s-lint",
        "k8s-schema",
        "mcp-lint",
    }
)
_QUALITY_ONLY_OUTPUT = "quality_only: ${{ steps.filter.outputs.quality_only }}"
_QUALITY_ONLY_REQUIRED_GLOBS = (
    "scripts/polaris-aws-range/**",
    "scenario-dev/polaris/tests/**",
)
_PORTAL_IMAGE_OUTPUT = "portal_image: ${{ steps.filter.outputs.portal_image }}"
_PORTAL_IMAGE_DEPLOY_CONDITION = "needs.changes.outputs.portal_image == 'true'"
_PORTAL_IMAGE_REQUIRED_GLOB = "shifter/shifter_platform/**"
_PORTAL_IMAGE_BUILD_INPUT = "inputs.portal_image_changes"
_PORTAL_DEPLOY_MODE_CHECK = "portal-deploy-mode-source-of-truth"
_PORTAL_DEPLOY_MODE_RULE = "ADR-003-R4"
_PORTAL_DEPLOY_HELPER_PATH = "scripts/portal_deploy/portal_deploy.py"
_PORTAL_DEV_OUTPUTS_PATH = "platform/terraform/environments/dev/portal/outputs.tf"
_PORTAL_PROD_OUTPUTS_PATH = "platform/terraform/environments/prod/portal/outputs.tf"


def _deploy_plan_scope_relevant(files: list[str] | None) -> bool:
    if files is None:
        return True
    relevant = {
        _DEPLOY_WORKFLOW_PATH,
        _CORE_WORKFLOW_PATH,
        _RANGE_WORKFLOW_PATH,
        _PLATFORM_WORKFLOW_PATH,
        _QUALITY_WORKFLOW_PATH,
    }
    return any(path in relevant or is_guard_source_path(path) for path in files)


def _should_check_plan_scope_file(files: list[str] | None, path: str) -> bool:
    return files is None or path in files or any(is_guard_source_path(f) for f in files)


def _paths_filter_block(deploy_text: str, filter_name: str) -> list[str]:
    block: list[str] = []
    in_block = False
    block_indent: int | None = None
    for raw_line in deploy_text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped == f"{filter_name}:":
            in_block = True
            block_indent = indent
            continue
        if not in_block:
            continue
        if stripped and block_indent is not None and indent <= block_indent:
            break
        block.append(stripped)
    return block


def _workflow_job_block(workflow_text: str, job_name: str) -> list[str]:
    block: list[str] = []
    in_block = False
    for raw_line in workflow_text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 2 and stripped == f"{job_name}:":
            in_block = True
            continue
        if in_block and stripped and indent == 2 and not stripped.startswith("- "):
            break
        if in_block:
            block.append(stripped)
    return block


def _block_contains_glob(block: list[str], glob: str) -> bool:
    return glob in _filter_globs(block)


def _filter_globs(block: list[str]) -> list[str]:
    globs: list[str] = []
    for line in block:
        if not line.startswith("- "):
            continue
        glob = line[2:].strip()
        if len(glob) >= 2 and glob[0] == glob[-1] and glob[0] in {"'", '"'}:
            glob = glob[1:-1]
        if glob:
            globs.append(glob)
    return globs


def _active_line_contains(block: list[str], needle: str) -> bool:
    return any(needle in line for line in block if not line.lstrip().startswith("#"))


def _extract_job_if(block: list[str]) -> str:
    """Return the ``if:`` expression for a stripped workflow job block."""
    active = [line for line in block if not line.lstrip().startswith("#")]
    for idx, line in enumerate(active):
        if not line.startswith("if:"):
            continue
        rest = line[3:].strip()
        if rest == "|":
            body: list[str] = []
            for follow in active[idx + 1 :]:
                if re.match(r"^[A-Za-z0-9_-]+:", follow):
                    break
                body.append(follow)
            return " ".join(body)
        return rest
    return ""


def _quality_job_is_skip_tests_immune(job_name: str) -> bool:
    if job_name in _QUALITY_SKIP_TESTS_IMMUNE_JOB_NAMES:
        return True
    return any(job_name.endswith(suffix) for suffix in _QUALITY_SKIP_TESTS_IMMUNE_JOB_SUFFIXES)


def _quality_workflow_job_names(quality_text: str) -> list[str]:
    names: list[str] = []
    in_jobs = False
    for raw_line in quality_text.splitlines():
        if raw_line.strip() == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if raw_line and not raw_line.startswith(" "):
            break
        match = re.match(r"^  ([a-z0-9_-]+):\s*$", raw_line)
        if match:
            names.append(match.group(1))
    return names


def _check_deploy_workflow_skip_tests_policy(deploy_text: str) -> list[Violation]:
    """ADR-003-R2: commit-message / dynamic test skips are not accepted."""
    violations: list[Violation] = []
    lowered = deploy_text.lower()
    for marker in _SKIP_TESTS_FORBIDDEN_MARKERS:
        if marker.lower() in lowered:
            violations.append(
                _plan_scope_violation(
                    _DEPLOY_WORKFLOW_PATH,
                    f"Commit-message or label-based test skips are not accepted; "
                    f"remove `{marker}` handling from the deploy workflow",
                )
            )
            break

    quality_block = _workflow_job_block(deploy_text, "quality")
    if not quality_block:
        return violations
    if not (
        _active_line_contains(quality_block, "_quality.yml")
        or _active_line_contains(quality_block, "./.github/workflows/_quality.yml")
    ):
        return violations
    if not _active_line_contains(quality_block, _SKIP_TESTS_LITERAL):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The Quality reusable-workflow call must pass "
                f"`{_SKIP_TESTS_LITERAL}` literally so protected-branch CI "
                "cannot bypass unit tests through commit-message flags",
            )
        )
    if _active_line_contains(quality_block, "skip_tests: ${{") or _active_line_contains(
        quality_block, "skip_tests:${{"
    ):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The Quality reusable-workflow call must not derive "
                "`skip_tests` from step outputs or commit-message parsing",
            )
        )
    return violations


def _check_quality_workflow_skip_tests_contract(quality_text: str) -> list[Violation]:
    """Architecture, lint, and security jobs must not honor ``inputs.skip_tests``."""
    violations: list[Violation] = []
    for job_name in _quality_workflow_job_names(quality_text):
        if not _quality_job_is_skip_tests_immune(job_name):
            continue
        block = _workflow_job_block(quality_text, job_name)
        if not block:
            continue
        if_expr = _extract_job_if(block)
        if "skip_tests" in if_expr:
            violations.append(
                _plan_scope_violation(
                    _QUALITY_WORKFLOW_PATH,
                    f"Job `{job_name}` must not be gated on `inputs.skip_tests`; "
                    "lint, architecture, and security checks run even when unit "
                    "tests are skipped",
                )
            )
    return violations


def _terraform_plan_has_lock_timeout(stripped_line: str) -> bool:
    if stripped_line.startswith("- run:"):
        command = stripped_line.split(":", 1)[1].strip()
    else:
        command = stripped_line
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens[:-1]):
        if token != "terraform" or tokens[index + 1] != "plan":
            continue
        plan_tokens: list[str] = []
        for plan_token in tokens[index + 2 :]:
            if plan_token in {"&&", "||", ";", "|"}:
                break
            plan_tokens.append(plan_token)
        return "-lock-timeout=5m" in plan_tokens
    return False


def _terraform_plan_writes_saved_plan(stripped_line: str) -> bool:
    if stripped_line.startswith("- run:"):
        command = stripped_line.split(":", 1)[1].strip()
    else:
        command = stripped_line
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens[:-1]):
        if token != "terraform" or tokens[index + 1] != "plan":
            continue
        plan_tokens: list[str] = []
        for plan_token in tokens[index + 2 :]:
            if plan_token in {"&&", "||", ";", "|"}:
                break
            plan_tokens.append(plan_token)
        if f"-out={_TERRAFORM_PLAN_FILE}" in plan_tokens:
            return True
        return any(
            plan_token == "-out"
            and next_index + 1 < len(plan_tokens)
            and plan_tokens[next_index + 1] == _TERRAFORM_PLAN_FILE
            for next_index, plan_token in enumerate(plan_tokens)
        )
    return False


def _terraform_apply_uses_saved_plan(stripped_line: str) -> bool:
    if stripped_line.startswith("- run:"):
        command = stripped_line.split(":", 1)[1].strip()
    else:
        command = stripped_line
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens[:-1]):
        if token != "terraform" or tokens[index + 1] != "apply":
            continue
        apply_tokens = tokens[index + 2 :]
        return (
            "-lock-timeout=5m" in apply_tokens
            and _TERRAFORM_PLAN_FILE in apply_tokens
            and "-auto-approve" not in apply_tokens
        )
    return False


def _line_removes_tfplan(stripped_line: str) -> bool:
    if _TERRAFORM_PLAN_FILE not in stripped_line:
        return False
    if stripped_line.startswith("- run:"):
        command = stripped_line.split(":", 1)[1].strip()
    else:
        command = stripped_line
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    return "rm" in tokens and _TERRAFORM_PLAN_FILE in tokens


def _plan_scope_violation(path: str, message: str) -> Violation:
    return Violation(_PLAN_SCOPE_CHECK, _PLAN_SCOPE_RULE, path, message)


def _platform_app_source_globs(deploy_text: str) -> list[str]:
    platform_block = _paths_filter_block(deploy_text, "shifter_platform")
    return [
        glob
        for glob in _filter_globs(platform_block)
        if glob == "shifter/**" or glob.startswith("shifter/")
    ]


def _check_deploy_workflow_plan_routing(deploy_text: str) -> list[Violation]:
    violations: list[Violation] = []
    app_source_globs = _platform_app_source_globs(deploy_text)
    if app_source_globs:
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "`shifter_platform` must not include app-source globs under `shifter/`; "
                f"found {', '.join(app_source_globs)}",
            )
        )

    changes_block = _workflow_job_block(deploy_text, "changes")
    quality_block = _workflow_job_block(deploy_text, "quality")
    pr_gate_block = _workflow_job_block(deploy_text, "pr-gate")
    non_docs_block = _paths_filter_block(deploy_text, "non_docs")
    guardrail_docs_block = _paths_filter_block(deploy_text, "guardrail_docs")

    if not _active_line_contains(changes_block, _QUALITY_RELEVANT_OUTPUT):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Quality routing must retain a `quality_relevant` changes-job output "
                "that combines the non-docs and guardrail-docs classifiers",
            )
        )
    elif not non_docs_block:
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Quality routing must retain a `non_docs` filter so ordinary docs-only "
                "diffs are the only general Quality skip path",
            )
        )
    elif not _active_line_contains(changes_block, _QUALITY_PREDICATE):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The `non_docs` Quality classifier must use "
                f"`{_QUALITY_PREDICATE}` so exclusion globs are honored together",
            )
        )
    elif missing_non_doc_globs := [
        glob
        for glob in _QUALITY_NON_DOCS_REQUIRED_GLOBS
        if not _block_contains_glob(non_docs_block, glob)
    ]:
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The `non_docs` Quality classifier is missing required docs-only "
                f"exclusion globs: {', '.join(missing_non_doc_globs)}",
            )
        )
    elif not guardrail_docs_block:
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Quality routing must retain a `guardrail_docs` filter so ADR and "
                "enforcement-doc changes still run Quality",
            )
        )
    elif missing_guardrail_globs := [
        glob
        for glob in _QUALITY_GUARDRAIL_DOCS_REQUIRED_GLOBS
        if not _block_contains_glob(guardrail_docs_block, glob)
    ]:
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The `guardrail_docs` Quality classifier is missing required "
                f"guardrail paths: {', '.join(missing_guardrail_globs)}",
            )
        )
    elif not _active_line_contains(quality_block, _QUALITY_RELEVANT_CONDITION):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The Quality job must include "
                f"`{_QUALITY_RELEVANT_CONDITION}` so non-docs and guardrail-docs "
                "changes run Quality",
            )
        )
    elif not pr_gate_block or not _active_line_contains(
        pr_gate_block, _PR_GATE_SKIPPED_QUALITY_GUARD
    ):
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "PR Gate must reject skipped Quality unless `quality_relevant` is false, "
                "so skipped Quality is accepted only for ordinary docs-only changes",
            )
        )
    return violations


def _check_deploy_workflow_quality_only_routing(deploy_text: str) -> list[Violation]:
    """Require non-deploy test-support paths to remain categorized."""
    quality_only_block = _paths_filter_block(deploy_text, "quality_only")
    changes_block = _workflow_job_block(deploy_text, "changes")
    if not quality_only_block or not _active_line_contains(changes_block, _QUALITY_ONLY_OUTPUT):
        return [
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Non-deploy test-support changes must retain a `quality_only` "
                "filter/output; missing the filter or changes-job output",
            )
        ]

    missing_globs = [
        glob
        for glob in _QUALITY_ONLY_REQUIRED_GLOBS
        if not _block_contains_glob(quality_only_block, glob)
    ]
    if missing_globs:
        return [
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "`quality_only` must include "
                f"{', '.join(missing_globs)} so orphaned support test suites stay "
                "categorized without triggering deploy jobs",
            )
        ]
    return []


def _check_deploy_workflow_portal_image_routing(deploy_text: str) -> list[Violation]:
    """Require the portal-image deploy trigger restored by #913.

    Application-code changes must reach the portal build/deploy path through
    a dedicated `portal_image` filter, without widening the Terraform-scoped
    `shifter_platform` plan trigger.
    """
    portal_block = _paths_filter_block(deploy_text, "portal_image")
    changes_block = _workflow_job_block(deploy_text, "changes")
    platform_job_block = _workflow_job_block(deploy_text, "shifter_platform")
    if not portal_block or not _active_line_contains(changes_block, _PORTAL_IMAGE_OUTPUT):
        return [
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Portal application changes must retain a `portal_image` filter/output "
                "so app-only pushes still build and deploy the portal image (#913); "
                "missing the filter or changes-job output",
            )
        ]
    if not _block_contains_glob(portal_block, _PORTAL_IMAGE_REQUIRED_GLOB):
        return [
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                f"`portal_image` must include `{_PORTAL_IMAGE_REQUIRED_GLOB}` so portal "
                "application changes trigger the image build/deploy path",
            )
        ]
    if not _active_line_contains(platform_job_block, _PORTAL_IMAGE_DEPLOY_CONDITION):
        return [
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "The `shifter_platform` job must include "
                f"`{_PORTAL_IMAGE_DEPLOY_CONDITION}` so application-code pushes still "
                "invoke the portal build/deploy workflow",
            )
        ]
    return []


def _check_platform_build_portal_image_gate(platform_text: str) -> list[Violation]:
    """Require the platform build job to gate on the portal-image input (#913)."""
    build_block = _workflow_job_block(platform_text, "build")
    if not build_block or not _active_line_contains(build_block, _PORTAL_IMAGE_BUILD_INPUT):
        return [
            _plan_scope_violation(
                _PLATFORM_WORKFLOW_PATH,
                f"The `build` job must gate on `{_PORTAL_IMAGE_BUILD_INPUT}` so app-only "
                "changes build and deploy the portal image without running Terraform",
            )
        ]
    return []


def _check_deploy_concurrency_queues_apply_runs(deploy_text: str) -> list[Violation]:
    """Require deploy runs that can apply infrastructure to queue, not cancel.

    PR cancellation is still allowed because PR runs do not execute environment
    branch applies. A global `true` cancellation policy can kill Terraform
    mid-apply on `aws-dev` / `gcp-dev` pushes.
    """
    cancel_value: str | None = None
    for line in deploy_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("cancel-in-progress:"):
            continue
        cancel_value = stripped.split(":", 1)[1].strip()
        break

    if cancel_value is None:
        return []

    normalized = cancel_value.strip()
    if normalized in {"false", "${{ false }}"}:
        return []
    if (
        "github.event_name == 'pull_request'" in normalized
        or 'github.event_name == "pull_request"' in normalized
    ):
        return []

    return [
        _plan_scope_violation(
            _DEPLOY_WORKFLOW_PATH,
            "Deploy workflow concurrency must queue env-branch apply runs instead "
            "of cancelling an in-flight Terraform apply; restrict cancellation to "
            "pull_request runs or set `cancel-in-progress: false`",
        )
    ]


def _check_terraform_plan_lock_timeout(workflow_text: str, path: str) -> list[Violation]:
    violations: list[Violation] = []
    for lineno, line in enumerate(workflow_text.splitlines(), start=1):
        stripped = line.strip()
        if "terraform plan" not in stripped:
            continue
        if stripped.startswith(("#", "echo ")):
            continue
        if _terraform_plan_has_lock_timeout(stripped):
            continue
        violations.append(
            _plan_scope_violation(
                f"{path}:{lineno}",
                "AWS Terraform plan commands must include `-lock-timeout=5m` "
                "so legitimate concurrent plans wait for the state lock instead of failing",
            )
        )
    return violations


def _check_saved_plan_apply_contract(workflow_text: str, path: str) -> list[Violation]:
    """Require the apply job to create and consume a local saved Terraform plan."""
    violations: list[Violation] = []
    plan_block = _workflow_job_block(workflow_text, "plan")
    apply_block = _workflow_job_block(workflow_text, "apply")

    if not plan_block:
        return [
            _plan_scope_violation(
                path,
                "Terraform workflow is missing a `plan` job; ADR-003-R2 cannot verify "
                "saved-plan apply integrity",
            )
        ]
    if not apply_block:
        return [
            _plan_scope_violation(
                path,
                "Terraform workflow is missing an `apply` job; ADR-003-R2 cannot verify "
                "saved-plan apply integrity",
            )
        ]

    apply_plan_idx: int | None = None
    apply_command_idx: int | None = None
    for index, line in enumerate(apply_block):
        stripped = line.strip()
        if stripped.startswith(("#", "echo ")):
            continue
        if apply_plan_idx is None and "terraform plan" in stripped:
            apply_plan_idx = index
        if apply_command_idx is None and "terraform apply" in stripped:
            apply_command_idx = index

    if apply_plan_idx is None:
        violations.append(
            _plan_scope_violation(
                path,
                "The Terraform `apply` job must create a local saved Terraform plan "
                "(`terraform plan -lock-timeout=5m -out=tfplan`) immediately before "
                "applying, avoiding raw binary plan artifacts while ensuring apply "
                "executes a reviewed saved plan",
            )
        )
    elif not _terraform_plan_writes_saved_plan(apply_block[apply_plan_idx]):
        violations.append(
            _plan_scope_violation(
                path,
                "The Terraform `apply` job's local plan command must write `-out=tfplan` "
                "so the subsequent apply consumes a saved plan file",
            )
        )

    if apply_command_idx is None:
        violations.append(
            _plan_scope_violation(
                path,
                "The Terraform `apply` job must run `terraform apply -lock-timeout=5m "
                "tfplan` after creating the saved plan",
            )
        )
    elif apply_plan_idx is not None and apply_plan_idx > apply_command_idx:
        violations.append(
            _plan_scope_violation(
                path,
                "The Terraform `apply` job must create the saved `tfplan` before "
                "running `terraform apply`",
            )
        )

    for index, line in enumerate(apply_block):
        stripped = line.strip()
        if stripped.startswith(("#", "echo ")):
            continue
        if (
            apply_plan_idx is not None
            and apply_command_idx is not None
            and apply_plan_idx < index < apply_command_idx
            and _line_removes_tfplan(stripped)
        ):
            violations.append(
                _plan_scope_violation(
                    path,
                    "The Terraform `apply` job must not remove `tfplan` before applying; "
                    "Service Discovery checks and Terraform apply must consume the same "
                    "saved plan file",
                )
            )
        if "terraform apply" not in stripped:
            continue
        if _terraform_apply_uses_saved_plan(stripped):
            continue
        violations.append(
            _plan_scope_violation(
                path,
                "Terraform apply commands must apply the saved Terraform plan with "
                "`terraform apply -lock-timeout=5m tfplan`, not run a fresh "
                "`terraform apply -auto-approve`",
            )
        )
    return violations


def _check_terraform_workflow_integrity(workflow_text: str, path: str) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(_check_terraform_plan_lock_timeout(workflow_text, path))
    violations.extend(_check_saved_plan_apply_contract(workflow_text, path))
    return violations


def check_deploy_workflow_plan_scope(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Keep AWS platform PR planning scoped to Terraform inputs."""
    if not _deploy_plan_scope_relevant(files):
        return []

    violations: list[Violation] = []
    deploy_path = repo_root / _DEPLOY_WORKFLOW_PATH
    core_path = repo_root / _CORE_WORKFLOW_PATH
    range_path = repo_root / _RANGE_WORKFLOW_PATH
    platform_path = repo_root / _PLATFORM_WORKFLOW_PATH

    check_deploy_and_platform = files is None or any(
        path in {_DEPLOY_WORKFLOW_PATH, _PLATFORM_WORKFLOW_PATH} or is_guard_source_path(path)
        for path in files
    )

    if check_deploy_and_platform and not deploy_path.exists():
        violations.append(
            _plan_scope_violation(
                _DEPLOY_WORKFLOW_PATH,
                "Required workflow is missing; ADR-003-R2 cannot verify platform plan routing",
            )
        )
    elif check_deploy_and_platform:
        deploy_text = deploy_path.read_text(encoding="utf-8")
        violations.extend(_check_deploy_concurrency_queues_apply_runs(deploy_text))
        violations.extend(_check_deploy_workflow_plan_routing(deploy_text))
        violations.extend(_check_deploy_workflow_quality_only_routing(deploy_text))
        violations.extend(_check_deploy_workflow_portal_image_routing(deploy_text))
        violations.extend(_check_deploy_workflow_skip_tests_policy(deploy_text))

    quality_path = repo_root / _QUALITY_WORKFLOW_PATH
    if files is None or _QUALITY_WORKFLOW_PATH in files or any(is_guard_source_path(f) for f in files):
        if not quality_path.exists():
            violations.append(
                _plan_scope_violation(
                    _QUALITY_WORKFLOW_PATH,
                    "Required workflow is missing; ADR-003-R2 cannot verify "
                    "architecture/security independence from skip_tests",
                )
            )
        else:
            violations.extend(
                _check_quality_workflow_skip_tests_contract(
                    quality_path.read_text(encoding="utf-8")
                )
            )

    for path, workflow_path in (
        (_CORE_WORKFLOW_PATH, core_path),
        (_RANGE_WORKFLOW_PATH, range_path),
    ):
        if not _should_check_plan_scope_file(files, path):
            continue
        if not workflow_path.exists():
            violations.append(
                _plan_scope_violation(
                    path,
                    "Required workflow is missing; ADR-003-R2 cannot verify Terraform "
                    "lock-timeout and saved-plan apply integrity",
                )
            )
            continue
        violations.extend(
            _check_terraform_workflow_integrity(workflow_path.read_text(encoding="utf-8"), path)
        )

    if check_deploy_and_platform and not platform_path.exists():
        violations.append(
            _plan_scope_violation(
                _PLATFORM_WORKFLOW_PATH,
                "Required workflow is missing; ADR-003-R2 cannot verify platform Terraform plan commands",
            )
        )
    elif check_deploy_and_platform:
        platform_text = platform_path.read_text(encoding="utf-8")
        violations.extend(_check_terraform_workflow_integrity(platform_text, _PLATFORM_WORKFLOW_PATH))
        violations.extend(_check_platform_build_portal_image_gate(platform_text))

    return violations


def _portal_deploy_mode_relevant(files: list[str] | None) -> bool:
    if files is None:
        return True
    relevant = {
        _PLATFORM_WORKFLOW_PATH,
        _PORTAL_DEPLOY_HELPER_PATH,
        _PORTAL_DEV_OUTPUTS_PATH,
        _PORTAL_PROD_OUTPUTS_PATH,
    }
    return any(path in relevant or is_guard_source_path(path) for path in files)


def _portal_deploy_mode_violation(path: str, message: str) -> Violation:
    return Violation(_PORTAL_DEPLOY_MODE_CHECK, _PORTAL_DEPLOY_MODE_RULE, path, message)


def _check_portal_deploy_mode_workflow(platform_text: str) -> list[Violation]:
    violations: list[Violation] = []
    deploy_block = _workflow_job_block(platform_text, "deploy")
    if not deploy_block:
        return [
            _portal_deploy_mode_violation(
                _PLATFORM_WORKFLOW_PATH,
                "The platform deploy job is missing; ADR-003-R4 cannot verify portal "
                "deployment-mode source-of-truth handling",
            )
        ]
    if "AWS_PORTAL_ENABLE_AUTOSCALING" in platform_text:
        violations.append(
            _portal_deploy_mode_violation(
                _PLATFORM_WORKFLOW_PATH,
                "`AWS_PORTAL_ENABLE_AUTOSCALING` must not drive the AWS portal deploy "
                "path; derive deployment mode from Terraform outputs instead",
            )
        )
    if not (
        _active_line_contains(deploy_block, _PORTAL_DEPLOY_HELPER_PATH)
        and _active_line_contains(deploy_block, "resolve-topology")
    ):
        violations.append(
            _portal_deploy_mode_violation(
                _PLATFORM_WORKFLOW_PATH,
                "The deploy job must call `scripts/portal_deploy/portal_deploy.py "
                "resolve-topology` so the deploy path is derived from Terraform state",
            )
        )
    if not (
        _active_line_contains(deploy_block, "verify-asg-image")
        and _active_line_contains(deploy_block, "--image-digest")
    ):
        violations.append(
            _portal_deploy_mode_violation(
                _PLATFORM_WORKFLOW_PATH,
                "The ASG deploy path must call `verify-asg-image` after instance refresh "
                "with `--image-digest` so every in-service instance is checked for the "
                "new portal image digest",
            )
        )
    return violations


def _check_portal_deploy_mode_outputs(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for outputs_path in (_PORTAL_DEV_OUTPUTS_PATH, _PORTAL_PROD_OUTPUTS_PATH):
        path = repo_root / outputs_path
        if not path.exists():
            violations.append(
                _portal_deploy_mode_violation(
                    outputs_path,
                    "Portal Terraform outputs are missing; ADR-003-R4 requires "
                    '`output "enable_autoscaling"` in each AWS portal environment',
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        if 'output "enable_autoscaling"' not in text:
            violations.append(
                _portal_deploy_mode_violation(
                    outputs_path,
                    'Portal Terraform outputs must expose `output "enable_autoscaling"` '
                    "so the deploy workflow reads the same mode Terraform applied",
                )
            )
    return violations


def _check_portal_deploy_helper(helper_text: str) -> list[Violation]:
    checks = (
        (
            "terraform output -json",
            "The portal deploy helper must read Terraform outputs, not a GitHub variable",
        ),
        (
            "len(running_instance_ids) != 1",
            "The portal deploy helper must fail unless single-instance mode finds exactly one "
            "running tagged instance",
        ),
        (
            "Reservations[].Instances[].InstanceId",
            "The portal deploy helper must query all matching running instances and must not "
            "pick `Reservations[0].Instances[0]`",
        ),
        (
            "describe-auto-scaling-groups",
            "The portal deploy helper must verify the Terraform ASG exists before choosing "
            "the ASG deploy path",
        ),
        (
            "send-command",
            "The portal deploy helper must use SSM to verify the running portal image digest "
            "on ASG instances",
        ),
        (
            "docker inspect",
            "The portal deploy helper must inspect the running portal container image during "
            "ASG verification",
        ),
        (
            "get-command-invocation",
            "The portal deploy helper must check each ASG instance's SSM verification result",
        ),
    )
    violations: list[Violation] = []
    for needle, message in checks:
        if needle not in helper_text:
            violations.append(
                _portal_deploy_mode_violation(_PORTAL_DEPLOY_HELPER_PATH, message)
            )
            break
    return violations


def check_portal_deploy_mode_source_of_truth(
    repo_root: Path, files: list[str] | None
) -> list[Violation]:
    """Ensure the AWS portal deploy path is derived from Terraform state."""
    if not _portal_deploy_mode_relevant(files):
        return []

    violations: list[Violation] = []
    platform_path = repo_root / _PLATFORM_WORKFLOW_PATH
    helper_path = repo_root / _PORTAL_DEPLOY_HELPER_PATH

    if not platform_path.exists():
        violations.append(
            _portal_deploy_mode_violation(
                _PLATFORM_WORKFLOW_PATH,
                "Required workflow is missing; ADR-003-R4 cannot verify portal "
                "deployment-mode source-of-truth handling",
            )
        )
    else:
        violations.extend(
            _check_portal_deploy_mode_workflow(platform_path.read_text(encoding="utf-8"))
        )

    violations.extend(_check_portal_deploy_mode_outputs(repo_root))

    if not helper_path.exists():
        violations.append(
            _portal_deploy_mode_violation(
                _PORTAL_DEPLOY_HELPER_PATH,
                "Portal deploy helper is missing; ADR-003-R4 requires a tested helper "
                "for Terraform-derived mode resolution and ASG image verification",
            )
        )
    else:
        violations.extend(_check_portal_deploy_helper(helper_path.read_text(encoding="utf-8")))

    return violations


_TFVARS_RENDER_CHECK = "aws-platform-renders-deploy-tfvars"
_TFVARS_RENDER_RULE = "ADR-011-R7"
# Jobs in `_shifter-platform.yml` that run Terraform against the portal root
# and therefore must render the deployment-owned override first.
_TFVARS_RENDER_JOBS = ("plan", "apply")
_LOCAL_AUTO_TFVARS = "local.auto.tfvars"
# `terraform` subcommands that consume variable values. `fmt`, `show`, and
# `output` do not, so the render step may legitimately sit after a `fmt` check.
_TF_CONSUMING_SUBCOMMANDS = ("init", "validate", "plan", "apply")


def _tfvars_render_violation(path: str, message: str) -> Violation:
    """Build an ADR-011-R7 violation for the deploy-tfvars-render check."""
    return Violation(_TFVARS_RENDER_CHECK, _TFVARS_RENDER_RULE, path, message)


def _is_terraform_consuming_command(stripped_line: str) -> bool:
    """True when the line runs a terraform subcommand that consumes variables."""
    if stripped_line.lstrip().startswith("#"):
        return False
    return any(f"terraform {sub}" in stripped_line for sub in _TF_CONSUMING_SUBCOMMANDS)


def _writes_local_auto_tfvars(stripped_line: str) -> bool:
    """True when the line redirects output *into* local.auto.tfvars.

    A line that merely names the file (e.g. the step's `name:`) is not
    proof of a render — only a write redirection (`> local.auto.tfvars`,
    including a path-prefixed `> dir/local.auto.tfvars`) counts, so the
    guard verifies executable behavior rather than a label.
    """
    if stripped_line.lstrip().startswith("#"):
        return False
    marker_pos = stripped_line.find(_LOCAL_AUTO_TFVARS)
    if marker_pos == -1:
        return False
    redirect_pos = stripped_line.find(">")
    return 0 <= redirect_pos < marker_pos


def _tfvars_render_violations_for_workflow(workflow_path: str, text: str) -> list[Violation]:
    """Return ADR-011-R7 violations for one reusable workflow file."""
    violations: list[Violation] = []
    for job in _TFVARS_RENDER_JOBS:
        block = _workflow_job_block(text, job)
        if not block:
            violations.append(
                _tfvars_render_violation(
                    workflow_path,
                    f"`{job}` job is missing; ADR-011-R7 expects it to render "
                    f"`{_LOCAL_AUTO_TFVARS}` before Terraform consumes variables",
                )
            )
            continue
        render_idx = next(
            (i for i, line in enumerate(block) if _writes_local_auto_tfvars(line)),
            None,
        )
        tf_idx = next(
            (i for i, line in enumerate(block) if _is_terraform_consuming_command(line)),
            None,
        )
        if render_idx is None:
            violations.append(
                _tfvars_render_violation(
                    workflow_path,
                    f"`{job}` job must render `{_LOCAL_AUTO_TFVARS}` from the deployment "
                    "secret (a step that writes the file, not merely names it) before "
                    "`terraform init/validate/plan/apply`, so the deploy never applies "
                    "the committed example.com baseline",
                )
            )
        elif tf_idx is not None and render_idx > tf_idx:
            violations.append(
                _tfvars_render_violation(
                    workflow_path,
                    f"`{job}` job renders `{_LOCAL_AUTO_TFVARS}` after a Terraform "
                    "command; the render must precede `terraform init/validate/plan/apply`",
                )
            )
    return violations


def check_platform_renders_deploy_tfvars(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Require AWS Terraform deploy jobs to render local.auto.tfvars first.

    The committed `terraform.tfvars` under `platform/terraform/environments/*`
    is an intentionally-broken `example.com` baseline. Each Terraform-running
    job in the AWS reusable workflows must render the deployment-owned override
    into a gitignored `local.auto.tfvars` before `terraform init/validate/plan/apply`
    consumes variables, so deploys never apply the baseline (ADR-011-R7).
    """
    workflow_paths = (_PLATFORM_WORKFLOW_PATH, _CORE_WORKFLOW_PATH, _RANGE_WORKFLOW_PATH)
    if files is not None and not any(
        path in {*workflow_paths} or is_guard_source_path(path) for path in files
    ):
        return []

    violations: list[Violation] = []
    for workflow_path in workflow_paths:
        workflow_file = repo_root / workflow_path
        if not workflow_file.exists():
            continue
        violations.extend(
            _tfvars_render_violations_for_workflow(
                workflow_path,
                workflow_file.read_text(encoding="utf-8"),
            )
        )
    return violations


_FAIL_LOUD_CHECK = "deploy-verification-fail-loud"
_FAIL_LOUD_RULE = "ADR-003-R3"
_ENGINE_WORKFLOW_PATH = ".github/workflows/_shifter-engine.yml"
_GUAC_STABILIZE_STEP = "Wait for Guacamole ECS services to stabilize"
_ENGINE_TASKDEF_STEP = "Update ECS task definition"
# The engine ECS task-family skip is only acceptable behind this explicit
# bootstrap input (mirrors gcp_require_active_certificate); its presence in the
# step proves the skip is gated rather than unconditional.
_ENGINE_BOOTSTRAP_INPUT = "first_deploy"


def _fail_loud_relevant(files: list[str] | None) -> bool:
    if files is None:
        return True
    relevant = {
        _PLATFORM_WORKFLOW_PATH,
        _ENGINE_WORKFLOW_PATH,
        _DEPLOY_WORKFLOW_PATH,
    }
    return any(path in relevant or is_guard_source_path(path) for path in files)


def _fail_loud_violation(path: str, message: str) -> Violation:
    return Violation(_FAIL_LOUD_CHECK, _FAIL_LOUD_RULE, path, message)


def _workflow_step_block(workflow_text: str, step_name: str) -> list[str]:
    """Return the raw lines of the named step, including its `run:` script.

    A step is the `- name: <step_name>` list item and every more-indented line
    beneath it, up to the next list item at the same indent or a dedent out of
    the step list. Returns [] when the step is not found.
    """
    block: list[str] = []
    in_block = False
    step_indent: int | None = None
    target = f"- name: {step_name}"
    for raw_line in workflow_text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if not in_block:
            if stripped == target:
                in_block = True
                step_indent = indent
            continue
        # End the step at the next sibling list item or any dedent to/under it.
        if stripped and step_indent is not None and indent <= step_indent:
            break
        block.append(raw_line)
    return block


def _noncomment_contains(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines if not line.lstrip().startswith("#"))


def _check_guacamole_timeout_fails(platform_text: str) -> list[Violation]:
    block = _workflow_step_block(platform_text, _GUAC_STABILIZE_STEP)
    if not block:
        return [
            _fail_loud_violation(
                _PLATFORM_WORKFLOW_PATH,
                f"`{_GUAC_STABILIZE_STEP}` step is missing; ADR-003-R3 cannot verify "
                "the Guacamole stabilization timeout fails the deploy",
            )
        ]
    # The stabilization poll is the last `while ... done` loop in the step; its
    # closing `done` separates the loop body from the timeout handler tail.
    done_idx = max(
        (i for i, line in enumerate(block) if line.strip() == "done"),
        default=None,
    )
    if done_idx is None:
        return [
            _fail_loud_violation(
                _PLATFORM_WORKFLOW_PATH,
                f"`{_GUAC_STABILIZE_STEP}` step has no polling loop; ADR-003-R3 expects "
                "a stabilization wait whose timeout fails the deploy",
            )
        ]
    tail = block[done_idx + 1 :]
    if not _noncomment_contains(tail, "exit 1") or _noncomment_contains(tail, "exit 0"):
        return [
            _fail_loud_violation(
                _PLATFORM_WORKFLOW_PATH,
                f"`{_GUAC_STABILIZE_STEP}` step must fail the deploy on stabilization "
                "timeout: the handler after the polling loop must `exit 1` (not warn and "
                "exit 0). Raise the timeout if first boot needs longer, but do not "
                "downgrade a timeout to a warning",
            )
        ]
    return []


def _check_engine_task_family_fails(engine_text: str) -> list[Violation]:
    block = _workflow_step_block(engine_text, _ENGINE_TASKDEF_STEP)
    if not block:
        return [
            _fail_loud_violation(
                _ENGINE_WORKFLOW_PATH,
                f"`{_ENGINE_TASKDEF_STEP}` step is missing; ADR-003-R3 cannot verify "
                "a missing engine task family fails the deploy",
            )
        ]
    violations: list[Violation] = []
    if not _noncomment_contains(block, "exit 1"):
        violations.append(
            _fail_loud_violation(
                _ENGINE_WORKFLOW_PATH,
                f"`{_ENGINE_TASKDEF_STEP}` step must `exit 1` when the ECS task "
                "definition family cannot be described, so a missing/typo'd family "
                "fails the deploy instead of skipping silently",
            )
        )
    if not _noncomment_contains(block, _ENGINE_BOOTSTRAP_INPUT):
        violations.append(
            _fail_loud_violation(
                _ENGINE_WORKFLOW_PATH,
                f"`{_ENGINE_TASKDEF_STEP}` step must gate any missing-family skip on the "
                f"explicit `{_ENGINE_BOOTSTRAP_INPUT}` bootstrap input; an unconditional "
                "`exit 0` skip lets a typo'd family skip every deploy forever",
            )
        )
    return violations


def check_deploy_verification_fail_loud(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Require deploy-verification steps to fail loud (ADR-003-R3).

    Two deploy steps must fail the run when the thing they verify did not
    happen, rather than warning and exiting 0:

    - `_shifter-platform.yml`'s Guacamole stabilization wait must `exit 1` on
      timeout (the FAILED circuit-breaker branch already does).
    - `_shifter-engine.yml`'s task-definition update must `exit 1` when the ECS
      task family cannot be described, with the only skip gated behind the
      explicit `first_deploy` bootstrap input.
    """
    if not _fail_loud_relevant(files):
        return []

    violations: list[Violation] = []
    platform_path = repo_root / _PLATFORM_WORKFLOW_PATH
    engine_path = repo_root / _ENGINE_WORKFLOW_PATH

    if not platform_path.exists():
        violations.append(
            _fail_loud_violation(
                _PLATFORM_WORKFLOW_PATH,
                "Required workflow is missing; ADR-003-R3 cannot verify the Guacamole "
                "stabilization timeout fails the deploy",
            )
        )
    else:
        violations.extend(_check_guacamole_timeout_fails(platform_path.read_text(encoding="utf-8")))

    if not engine_path.exists():
        violations.append(
            _fail_loud_violation(
                _ENGINE_WORKFLOW_PATH,
                "Required workflow is missing; ADR-003-R3 cannot verify a missing engine "
                "task family fails the deploy",
            )
        )
    else:
        violations.extend(_check_engine_task_family_fails(engine_path.read_text(encoding="utf-8")))

    return violations


# --- ADR-003-R5 hard check: no pull_request reaches a self-hosted deploy job #
_RUNNER_EXPOSURE_CHECK = "deploy-workflow-runner-exposure"
_RUNNER_EXPOSURE_RULE = "ADR-003-R5"


def _runner_exposure_violation(path: str, message: str) -> Violation:
    return Violation(_RUNNER_EXPOSURE_CHECK, _RUNNER_EXPOSURE_RULE, path, message)


def _deploy_runner_exposure_relevant(files: list[str] | None) -> bool:
    if files is None:
        return True
    relevant = set(_DW_REUSABLE_WORKFLOW_PATHS) | {
        _DEPLOY_WORKFLOW_PATH,
    }
    return any(path in relevant or is_guard_source_path(path) for path in files)


def check_deploy_runner_exposure(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """No pull_request event may reach a self-hosted deploy job (ADR-003-R5).

    Evaluates each reusable deploy workflow's self-hosted job ``if:`` for a
    pull_request event and requires it to fail closed. Semantic evaluation, not
    substring matching: a guard broadened with ``|| always()`` is still caught.
    """
    if not _deploy_runner_exposure_relevant(files):
        return []

    violations: list[Violation] = []
    for rel in _DW_REUSABLE_WORKFLOW_PATHS:
        if not (repo_root / rel).exists():
            violations.append(
                _runner_exposure_violation(
                    rel,
                    "Required reusable deploy workflow is missing; ADR-003-R5 "
                    "cannot verify self-hosted runner exposure",
                )
            )
            continue
        try:
            wf = _dw_load_workflow(repo_root, rel)
            job_map = _dw_jobs(wf, rel)
        except _DwShapeError as exc:
            violations.append(
                _runner_exposure_violation(
                    rel, f"workflow could not be parsed for ADR-003-R5: {exc}"
                )
            )
            continue
        for jid, job in job_map.items():
            if not _dw_is_self_hosted(job):
                continue
            expr = _dw_job_if(job)
            try:
                denied = _dw_job_denied_on_pull_request(expr)
            except _DwShapeError as exc:
                violations.append(
                    _runner_exposure_violation(
                        rel,
                        f"self-hosted job '{jid}' has an if-expression "
                        f"ADR-003-R5 cannot evaluate: {exc}",
                    )
                )
                continue
            if not denied:
                violations.append(
                    _runner_exposure_violation(
                        rel,
                        f"self-hosted job '{jid}' is reachable from a "
                        "pull_request event; ADR-003-R5 requires it gate on "
                        "github.event_name != 'pull_request'",
                    )
                )
    return violations


# --- ADR-037-R1 hard check: cloud-credentialed workflows pin action SHAs ----
# Every non-local `uses:` action in a cloud-credentialed workflow is an
# executable dependency that runs with cloud credentials; a mutable tag can be
# moved by a compromised or careless maintainer, so it must resolve to a full
# 40-hex commit SHA (supply-chain provenance, issue #1519). This mirrors the
# `_dw_*` workflow-as-data model rather than string-matching workflow text, and
# fails closed: a workflow that cannot be parsed cannot be classified.
_ACTION_PIN_CHECK = "workflow-action-sha-pinning"
_ACTION_PIN_RULE = "ADR-037-R1"
_ACTION_PIN_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_ACTION_PIN_OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CLOUD_AUTH_ACTIONS = (
    "aws-actions/configure-aws-credentials",
    "google-github-actions/auth",
)


def _action_pin_violation(path: str, message: str) -> Violation:
    return Violation(_ACTION_PIN_CHECK, _ACTION_PIN_RULE, path, message)


def _dw_iter_workflow_files(repo_root: Path) -> list[str]:
    """Repo-relative paths of every GitHub Actions workflow file, sorted."""
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        f".github/workflows/{p.name}"
        for p in wf_dir.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def _dw_permissions_grant_id_token(perms) -> bool:
    if isinstance(perms, str):
        return perms.strip().lower() == "write-all"
    if isinstance(perms, dict):
        return str(perms.get("id-token", "")).strip().lower() == "write"
    return False


def _dw_job_steps(job: dict) -> list:
    steps = job.get("steps")
    return steps if isinstance(steps, list) else []


def _dw_step_uses(step) -> str | None:
    if isinstance(step, dict):
        uses = step.get("uses")
        if isinstance(uses, str):
            return uses.strip()
    return None


def _dw_step_uses_cloud_auth(step) -> bool:
    uses = _dw_step_uses(step)
    if uses and any(
        uses == a or uses.startswith(a + "@") for a in _CLOUD_AUTH_ACTIONS
    ):
        return True
    if isinstance(step, dict):
        with_block = step.get("with")
        if isinstance(with_block, dict) and "workload_identity_provider" in with_block:
            return True
    return False


# A hijacked mutable-tag action can exfiltrate any secret the job holds, not
# only OIDC/self-hosted cloud identity, so a static or inherited secret makes a
# workflow credential-bearing for ADR-037-R1 (issue #998 codex review). Match
# `${{ secrets.X }}` references; GITHUB_TOKEN is excluded because it is present
# by default in nearly every workflow and its elevated (write / id-token) uses
# are already covered by the permission and OIDC markers above, so counting it
# would flag effectively every workflow rather than the genuinely credentialed
# ones this rule targets.
_DW_NAMED_SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_-]*)")


def _dw_iter_strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _dw_iter_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _dw_iter_strings(item)
    elif isinstance(node, str):
        yield node


def _dw_references_named_secret(node) -> bool:
    """True if the YAML subtree references any ``secrets.X`` except GITHUB_TOKEN."""
    return any(
        name != "GITHUB_TOKEN"
        for text in _dw_iter_strings(node)
        for name in _DW_NAMED_SECRET_RE.findall(text)
    )


def _dw_job_forwards_secrets(job: dict) -> bool:
    """True if a reusable-workflow-call job forwards secrets to the callee.

    Covers both ``secrets: inherit`` (all secrets forwarded) and an explicit
    ``secrets:`` mapping that passes a named secret.
    """
    secrets = job.get("secrets")
    if isinstance(secrets, str):
        return secrets.strip().lower() == "inherit"
    if isinstance(secrets, dict):
        return _dw_references_named_secret(secrets)
    return False


def _dw_job_is_cloud_credentialed(job: dict) -> bool:
    if _dw_permissions_grant_id_token(job.get("permissions")):
        return True
    if _dw_is_self_hosted(job):
        return True
    if _dw_job_forwards_secrets(job):
        return True
    if _dw_references_named_secret(job):
        return True
    return any(_dw_step_uses_cloud_auth(step) for step in _dw_job_steps(job))


def _dw_workflow_is_cloud_credentialed(wf: dict) -> bool:
    """True when a workflow hands a job real credentials in any form.

    Markers: top-level or job-level ``id-token: write`` (or ``write-all``), a
    self-hosted runner, a cloud-auth action, a ``workload_identity_provider``
    input, a job that references or forwards a named secret (static ``env`` /
    ``with`` secrets, ``secrets:`` mappings, or ``secrets: inherit``), or a
    workflow-level ``env`` that injects a named secret into every job. Any one is
    sufficient; the classifier fails toward "credentialed" so an unpinned action
    is never silently exempted. GITHUB_TOKEN alone does not qualify (see
    ``_DW_NAMED_SECRET_RE``).
    """
    if _dw_permissions_grant_id_token(wf.get("permissions")):
        return True
    jobs = wf.get("jobs")
    if not isinstance(jobs, dict):
        return False
    if any(
        _dw_job_is_cloud_credentialed(job)
        for job in jobs.values()
        if isinstance(job, dict)
    ):
        return True
    return _dw_references_named_secret(wf.get("env"))


def _dw_iter_uses_refs(wf: dict):
    """Yield ``(job_id, uses_ref)`` for every job- and step-level ``uses:``."""
    jobs = wf.get("jobs")
    if not isinstance(jobs, dict):
        return
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_uses = job.get("uses")
        if isinstance(job_uses, str):
            yield jid, job_uses.strip()
        for step in _dw_job_steps(job):
            uses = _dw_step_uses(step)
            if uses:
                yield jid, uses


def _dw_uses_is_sha_pinned(ref: str) -> bool:
    parts = ref.rsplit("@", 1)
    if len(parts) != 2:
        return False
    after = parts[1]
    # Repository actions pin a 40-hex git commit SHA; container (`docker://`)
    # actions pin an OCI `sha256:<64 hex>` digest. Both are immutable.
    return bool(_ACTION_PIN_SHA40.match(after) or _ACTION_PIN_OCI_DIGEST.match(after))


def _workflow_action_pin_relevant(files: list[str] | None) -> bool:
    if files is None:
        return True
    return any(
        f.startswith(".github/workflows/") or is_guard_source_path(f)
        for f in files
    )


def check_workflow_action_sha_pinning(
    repo_root: Path, files: list[str] | None
) -> list[Violation]:
    """Cloud-credentialed workflows pin every action to a full SHA (ADR-037-R1).

    Enumerates every ``.github/workflows/*.yml`` as data, classifies each as
    cloud-credentialed, and requires every non-local ``uses:`` reference in a
    credentialed workflow to be a full 40-hex commit SHA. Fails closed: a
    workflow that cannot be parsed cannot be classified, so it is reported.
    ``actions/*`` is included - GitHub-owned actions are executable dependencies
    too, as are ``docker://`` container actions, which must pin an OCI
    ``sha256:<64 hex>`` digest. Only local reusable-workflow refs (``./...``) are
    exempt.
    """
    import yaml  # local import: keeps PyYAML optional for non-workflow checks

    if not _workflow_action_pin_relevant(files):
        return []

    violations: list[Violation] = []
    for rel in _dw_iter_workflow_files(repo_root):
        try:
            wf = _dw_load_workflow(repo_root, rel)
        except (_DwShapeError, yaml.YAMLError) as exc:
            violations.append(
                _action_pin_violation(
                    rel,
                    "workflow could not be parsed for ADR-037-R1, so its "
                    f"cloud-credential status cannot be verified: {exc}",
                )
            )
            continue
        if not _dw_workflow_is_cloud_credentialed(wf):
            continue
        for jid, ref in _dw_iter_uses_refs(wf):
            # Local reusable-workflow refs (`./...`) are first-party and exempt.
            # `docker://` container actions are NOT exempt: they are remote
            # executable dependencies too, so they must pin an OCI digest.
            if ref.startswith("./"):
                continue
            if not _dw_uses_is_sha_pinned(ref):
                hint = (
                    "an OCI 'sha256:<64 hex>' digest"
                    if ref.startswith("docker://")
                    else "a full 40-hex commit SHA (keep a '# <version>' comment for Dependabot)"
                )
                violations.append(
                    _action_pin_violation(
                        rel,
                        f"job '{jid}' uses '{ref}' with a mutable ref; "
                        f"ADR-037-R1 requires {hint} in cloud-credentialed workflows",
                    )
                )
    return violations


_GITHUB_OIDC_TF_PATH = "platform/terraform/global/iam/github-oidc.tf"


def check_github_oidc_no_admin_access(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Forbid AdministratorAccess attachment on the GitHub Actions OIDC role (ADR-004-R15)."""
    if files is not None and _GITHUB_OIDC_TF_PATH not in files and not any(
        path.endswith("adr_guard.py") for path in files
    ):
        return []

    path = repo_root / _GITHUB_OIDC_TF_PATH
    if not path.is_file():
        return []

    text = path.read_text(encoding="utf-8")
    if re.search(
        r'policy_arn\s*=\s*"arn:aws:iam::aws:policy/AdministratorAccess"',
        text,
    ):
        return [
            Violation(
                "github-oidc-no-admin-access",
                "ADR-004-R15",
                _GITHUB_OIDC_TF_PATH,
                "GitHub Actions OIDC role must not attach managed AdministratorAccess; "
                "use scoped inline or managed policies required by CI workflows.",
            )
        ]
    return []
