"""Production-path quality-ownership reconciliation (ADR-004-R24)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .._common import (
    Violation,
    _git_tracked_all,
    _walk_all_files,
)
from .._workflow_model import (
    _DwExprError,
    _DwParser,
    _DwShapeError,
    _dw_job_if,
    _dw_jobs,
    _dw_load_workflow,
    _dw_normalize_expr,
    _dw_tokenize,
    _dw_truthy,
)
from .layer_imports import (
    _classified_packages,
)


# --------------------------------------------------------------------------- #
# Production-path quality-ownership conformance (#1530, GEN-002, ADR-004-R24)
#
# Reconciles .github/quality-path-filters.yaml (the single versioned
# quality-ownership contract) against the repository. Three invariants:
#   1. estate completeness  - every tracked path is a production owner or a
#      typed exclusion (unknown fails closed);
#   2. ownership completeness - every production PATH is covered, across the
#      union of its matching units, by a blocking lint AND security AND test
#      job (advisory / continue-on-error / missing jobs do not count); genuine
#      gaps are recorded as time-bounded docs/adr/exceptions.yaml entries;
#   3. routing reachability  - a representative change to each unit makes its
#      declared jobs (and matrix members) run in the real _quality.yml, while a
#      docs-only change does not select production jobs.
# The schema itself is parsed once by scripts/quality_ownership/contract.py -
# the same module the _quality.yml `paths` job uses - so there is no second
# implementation of the contract.
# --------------------------------------------------------------------------- #
_QUALITY_CONTRACT_REL = ".github/quality-path-filters.yaml"
_QUALITY_WORKFLOW_REL = ".github/workflows/_quality.yml"
_QUALITY_RULE = "ADR-004-R24"
_QUALITY_CHECK = "quality-path-ownership"
_QUALITY_RESPONSIBILITIES = ("lint", "security", "test")
# Evidence-only jobs: soft-fail, always-run, or advisory scanners that cannot
# by themselves own a required responsibility (per the #1530 preflight).
_QUALITY_ADVISORY_JOBS = frozenset(
    {
        "security-trivy-advisory",
        "security-osv-advisory",
        "secrets-gitleaks",
        "sonarcloud",
        "security-k8s",
    }
)


def _load_quality_module(repo_root: Path):
    """Load scripts/quality_ownership/contract.py as a module (the single
    contract implementation), without mutating sys.path."""
    import importlib.util

    path = repo_root / "scripts" / "quality_ownership" / "contract.py"
    spec = importlib.util.spec_from_file_location("_quality_ownership_contract", path)
    if spec is None or spec.loader is None:
        raise _DwShapeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's dataclasses (with `from __future__
    # import annotations`) can resolve their own namespace during processing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _quality_probe_path(pattern: str) -> str:
    if pattern.endswith("/**"):
        return pattern[:-3].rstrip("/") + "/__probe__"
    return pattern


def _quality_strip_if(if_expr) -> str:
    expr = _dw_normalize_expr(if_expr)
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return expr


def _quality_eval_if(if_expr, outputs: dict) -> bool:
    """Evaluate a job ``if:`` against a controlled paths-output map, so routing
    is proven semantically (not by substring). ``needs.paths.outputs.<key>``
    resolves from ``outputs``; other operands are permissive; ``skip_tests`` is
    false and ``run_stack_smoke`` true so test/smoke jobs are eligible."""
    expr = _quality_strip_if(if_expr)
    if not expr:
        return True

    def resolve(pathstr):
        parts = pathstr.split(".")
        if parts[:3] == ["needs", "paths", "outputs"] and len(parts) >= 4:
            return outputs.get(parts[3], "false")
        if parts[0] == "needs":
            if len(parts) >= 3 and parts[2] == "result":
                return "success"
            return "true"
        if parts[0] == "inputs":
            field = parts[1] if len(parts) > 1 else ""
            return {
                "skip_tests": False,
                "run_full_matrix": False,
                "run_stack_smoke": True,
            }.get(field, False)
        if parts[0] == "github":
            return ""
        raise _DwExprError(f"unresolvable operand: {pathstr}")

    return _dw_truthy(_DwParser(_dw_tokenize(expr), resolve).evaluate())


def _quality_job_reachable(jobs: dict, job_id: str, outputs: dict, seen=None) -> bool:
    """A job runs iff its own ``if:`` is true AND every job it ``needs`` runs
    (transitively) - the real GitHub gating semantics, so a matrix generator or
    other upstream gate is honoured."""
    seen = seen or set()
    job = jobs.get(job_id)
    if job is None or job_id in seen:
        return job is not None
    if not _quality_eval_if(_dw_job_if(job), outputs):
        return False
    needs = job.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    return all(
        _quality_job_reachable(jobs, need, outputs, seen | {job_id}) for need in needs
    )


def _quality_ownership_completeness(contract, module, tracked, jobs, viol):
    violations = []

    def job_blocking(jobref):
        if jobref.job not in jobs:
            return (False, "missing")
        if jobref.job in _QUALITY_ADVISORY_JOBS:
            return (False, "advisory")
        if _dw_truthy(jobs[jobref.job].get("continue-on-error")):
            return (False, "continue-on-error")
        return (True, "")

    for unit in contract.units:
        for resp, refs in unit.responsibilities.items():
            for ref in refs:
                ok, why = job_blocking(ref)
                if not ok:
                    violations.append(
                        viol(
                            f"{_QUALITY_CONTRACT_REL}#{unit.id}:{resp}",
                            f"quality unit {unit.id!r} {resp} references {why} job "
                            f"{ref.job!r}; an advisory / continue-on-error / missing "
                            "job cannot own a responsibility",
                        )
                    )

    satisfies = {
        unit.id: {
            resp
            for resp, refs in unit.responsibilities.items()
            if any(job_blocking(ref)[0] for ref in refs)
        }
        for unit in contract.units
    }

    gaps: dict = {}
    for path in tracked:
        units_here = module.matching_units(contract, path)
        if not units_here:
            continue
        covered: set = set()
        for unit_id in units_here:
            covered |= satisfies.get(unit_id, set())
        missing = set(_QUALITY_RESPONSIBILITIES) - covered
        if missing:
            owner = module.most_specific_unit(contract, path) or units_here[0]
            for resp in missing:
                gaps.setdefault((owner, resp), path)

    for (unit_id, resp), path in sorted(gaps.items()):
        violations.append(
            viol(
                f"{_QUALITY_CONTRACT_REL}#{unit_id}:{resp}",
                f"no blocking {resp} owner for quality unit {unit_id!r} "
                f"(e.g. {path}); add a blocking {resp} job or record a time-bounded "
                "docs/adr/exceptions.yaml entry for this gap",
            )
        )
    return violations


_QUALITY_MATRIX_OUTPUT = {"mcp-lint": "mcp_lint_packages", "mcp-tests": "mcp_test_packages"}


def _quality_matrix_reachable(ref, resp, unit, outputs, jobs, viol):
    matrix = dict(ref.matrix)
    package = matrix.get("package")
    key = _QUALITY_MATRIX_OUTPUT.get(ref.job)
    if key is None:
        return []
    violations = []
    try:
        selected = json.loads(outputs.get(key, "[]"))
    except json.JSONDecodeError:
        selected = []
    if package not in selected:
        violations.append(
            viol(
                _QUALITY_WORKFLOW_REL,
                f"quality unit {unit.id!r} {resp} matrix member package={package!r} "
                f"is not selected in {key} when {unit.id!r} changes",
            )
        )
    # Verify the real job actually consumes that output as its matrix source, so
    # a job wired to the wrong JSON output (or a hard-coded matrix) is caught
    # rather than trusting the classifier value alone.
    matrix_key = next(iter(matrix), None)
    job = jobs.get(ref.job) or {}
    strategy = job.get("strategy") or {}
    matrix_spec = strategy.get("matrix") or {}
    matrix_source = str(matrix_spec.get(matrix_key, "")) if matrix_key else ""
    if "fromjson" not in matrix_source.lower() or f"needs.paths.outputs.{key}" not in matrix_source:
        violations.append(
            viol(
                _QUALITY_WORKFLOW_REL,
                f"job {ref.job!r} strategy.matrix.{matrix_key} must consume "
                f"fromJSON(needs.paths.outputs.{key}) (found {matrix_source!r}); "
                "the matrix must be driven by the declared output, not a fixed list",
            )
        )
    return violations


def _quality_output_wiring_violations(contract, module, jobs, viol):
    """Verify the real paths-job output export edges, not just the classifier's
    values: every classifier-emitted key is exported as
    ``steps.detect.outputs.<key>`` (a mis-wired key silently breaks routing),
    and no classifier-sourced output exists that the contract does not emit."""
    violations = []
    paths_job = jobs.get("paths")
    if not isinstance(paths_job, dict):
        return [viol(_QUALITY_WORKFLOW_REL, "paths job is missing from the workflow")]
    declared = paths_job.get("outputs") or {}
    emitted = set(module.compute_outputs(contract, None, run_full_matrix=True).keys())
    for key in sorted(emitted):
        if key not in declared:
            violations.append(
                viol(
                    _QUALITY_WORKFLOW_REL,
                    f"paths job does not export classifier output {key!r}",
                )
            )
        elif f"steps.detect.outputs.{key}" not in str(declared[key]):
            violations.append(
                viol(
                    _QUALITY_WORKFLOW_REL,
                    f"paths output {key!r} is not wired to steps.detect.outputs.{key} "
                    f"(found {declared[key]!r}); a mis-wired output silently breaks routing",
                )
            )
    for key, value in declared.items():
        if "steps.detect.outputs." in str(value) and key not in emitted:
            violations.append(
                viol(
                    _QUALITY_WORKFLOW_REL,
                    f"paths output {key!r} is sourced from the classifier but is not an "
                    "emitted contract output",
                )
            )
    return violations


def _quality_routing_reachability(contract, module, jobs, viol):
    violations = []
    for unit in contract.units:
        probe = _quality_probe_path(unit.paths[0])
        try:
            outputs = module.compute_outputs(contract, [probe])
        except Exception as exc:  # UnknownPathError / ContractError
            violations.append(
                viol(
                    _QUALITY_CONTRACT_REL,
                    f"cannot classify probe {probe!r} for unit {unit.id!r}: {exc}",
                )
            )
            continue
        for resp, refs in unit.responsibilities.items():
            for ref in refs:
                if ref.job not in jobs:
                    continue  # missing-job already reported by completeness
                try:
                    reachable = _quality_job_reachable(jobs, ref.job, outputs)
                except _DwShapeError as exc:
                    violations.append(
                        viol(
                            _QUALITY_WORKFLOW_REL,
                            f"quality unit {unit.id!r} {resp} job {ref.job!r} has an "
                            f"if-expression the routing model cannot evaluate: {exc}",
                        )
                    )
                    continue
                if not reachable:
                    violations.append(
                        viol(
                            _QUALITY_WORKFLOW_REL,
                            f"quality unit {unit.id!r} {resp} job {ref.job!r} does not "
                            f"run when {probe!r} changes (routing unreachable)",
                        )
                    )
                    continue
                if ref.matrix:
                    violations += _quality_matrix_reachable(
                        ref, resp, unit, outputs, jobs, viol
                    )

    docs_probe = "docs/__probe__.md"
    try:
        neg = module.compute_outputs(contract, [docs_probe])
    except Exception:
        neg = None
    if neg is not None:
        # A docs-only change must not select any declared production job.
        declared_jobs = {
            ref.job
            for unit in contract.units
            for refs in unit.responsibilities.values()
            for ref in refs
            if ref.job in jobs
        }
        for job_id in sorted(declared_jobs):
            try:
                if _quality_job_reachable(jobs, job_id, neg):
                    violations.append(
                        viol(
                            _QUALITY_WORKFLOW_REL,
                            f"production job {job_id!r} is selected by a docs-only "
                            f"change ({docs_probe!r}); an exclusion must not route "
                            "production jobs",
                        )
                    )
            except _DwShapeError:
                continue  # unevaluatable if already reported above
    return violations


def _quality_package_reconciliation(contract, repo_root, viol):
    try:
        classified = _classified_packages(repo_root)
    except Exception as exc:
        return [
            viol(
                _QUALITY_CONTRACT_REL,
                f"cannot load the #1523 package classification: {exc}",
            )
        ]
    declared: set = set()
    for unit in contract.units:
        declared |= set(unit.packages)
    violations = []
    for pkg in sorted(declared - classified):
        violations.append(
            viol(
                _QUALITY_CONTRACT_REL,
                f"quality unit references package {pkg!r} that is not in the #1523 "
                "classification (scripts/check_layer_imports/layer_imports.yaml)",
            )
        )
    for pkg in sorted(classified - declared):
        violations.append(
            viol(
                _QUALITY_CONTRACT_REL,
                f"#1523 first-party package {pkg!r} has no quality-ownership unit",
            )
        )
    return violations


def check_quality_path_ownership(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Reconcile the quality-ownership contract (whole-tree invariant)."""
    del files  # whole-tree invariant

    def viol(path: str, message: str) -> Violation:
        return Violation(_QUALITY_CHECK, _QUALITY_RULE, path, message)

    try:
        module = _load_quality_module(repo_root)
    except Exception as exc:
        return [viol(_QUALITY_CONTRACT_REL, f"cannot load quality-ownership module: {exc}")]
    try:
        contract = module.load_contract(repo_root / _QUALITY_CONTRACT_REL)
    except Exception as exc:  # ContractError / OSError
        return [viol(_QUALITY_CONTRACT_REL, f"contract invalid: {exc}")]

    tracked = _git_tracked_all(repo_root)
    if tracked is None:
        tracked = _walk_all_files(repo_root)

    violations: list[Violation] = [
        viol(_QUALITY_CONTRACT_REL, err)
        for err in module.estate_violations(contract, tracked)
    ]

    try:
        workflow = _dw_load_workflow(repo_root, _QUALITY_WORKFLOW_REL)
        jobs = _dw_jobs(workflow, _QUALITY_WORKFLOW_REL)
    except _DwShapeError as exc:
        return violations + [viol(_QUALITY_WORKFLOW_REL, str(exc))]

    violations += _quality_ownership_completeness(contract, module, tracked, jobs, viol)
    violations += _quality_output_wiring_violations(contract, module, jobs, viol)
    violations += _quality_routing_reachability(contract, module, jobs, viol)
    violations += _quality_package_reconciliation(contract, repo_root, viol)
    return violations
