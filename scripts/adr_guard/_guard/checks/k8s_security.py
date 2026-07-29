"""Kubernetes deployment security-context and network-policy coverage checks."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .._common import (
    Violation,
    _ADR_GUARD_PATH,
    _repo_relative,
)


K8S_BASE_DEPLOYMENT_DIR = "platform/k8s/gcp/base"
HELM_CHART_DIR = "platform/charts/shifter"
# Values files to render for ADR-006-R2 validation. Mirrors the helm-lint
# pre-commit hook's input set so the guard validates the same chart-rendered
# output devs already lint locally.
HELM_VALUES_FILES = (
    "platform/charts/shifter/values-aws-dev.yaml",
    "platform/charts/shifter/values-aws-proof.yaml",
    "platform/charts/shifter/values-aws-prod.yaml",
    "platform/charts/shifter/values-gcp-dev.yaml",
    "platform/charts/shifter/values-gcp-prod.yaml",
)


def _is_real_int(value: object) -> bool:
    """True when value is an int but not a bool (bool subclasses int in Python)."""
    return type(value) is int  # noqa: E721 - intentional exact type check


def _check_k8s_pod_security(pod_sc: dict, rel: str) -> list[Violation]:
    seccomp = pod_sc.get("seccompProfile") or {}
    if not isinstance(seccomp, dict):
        seccomp = {}
    seccomp_type = seccomp.get("type")
    if seccomp_type != "RuntimeDefault":
        return [
            Violation(
                "k8s-deployment-security-context",
                "ADR-006-R2",
                rel,
                f"pod-level securityContext.seccompProfile.type must be 'RuntimeDefault' (got {seccomp_type!r})",
            )
        ]
    return []


def _effective_field(container_sc: dict, pod_sc: dict, key: str) -> object:
    """Resolve a securityContext field that K8s lets the pod default cover.

    Per the Pod spec, container-level overrides take precedence; if the
    container does not set the field, the pod-level value applies. Used
    for runAsNonRoot, runAsUser, runAsGroup.
    """
    if key in container_sc:
        return container_sc.get(key)
    return pod_sc.get(key)


def _coerce_container_sc(raw_sc: object, label: str) -> tuple[dict, list[Violation]]:
    """Coerce a container's `securityContext` into a dict, surfacing structural problems.

    Non-mapping values (YAML aliases resolved to scalars, malformed shapes)
    produce a violation and the caller continues against an empty dict so
    individual field checks don't AttributeError.
    """
    if raw_sc is not None and not isinstance(raw_sc, dict):
        return (
            {},
            [
                Violation(
                    "k8s-deployment-security-context",
                    "ADR-006-R2",
                    "",  # rel filled in by caller
                    f"{label} securityContext must be a mapping "
                    "(YAML aliases or non-mapping values are not supported by this guard)",
                )
            ],
        )
    return (raw_sc or {}, [])


def _check_container_basic_fields(sc: dict, label: str) -> list[str]:
    """Per-container fields that don't inherit from the pod (ADR-006-R2)."""
    msgs: list[str] = []
    if sc.get("privileged") is True:
        msgs.append(f"{label} must not set securityContext.privileged: true")
    if sc.get("allowPrivilegeEscalation") is not False:
        msgs.append(f"{label} must set allowPrivilegeEscalation: false")
    if sc.get("readOnlyRootFilesystem") is not True:
        msgs.append(f"{label} must set readOnlyRootFilesystem: true")
    return msgs


def _check_container_capabilities(sc: dict, label: str) -> list[str]:
    """capabilities.drop == [ALL] and no capabilities.add key (ADR-006-R2)."""
    msgs: list[str] = []
    capabilities = sc.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        msgs.append(f"{label} securityContext.capabilities must be a mapping if set")
        capabilities = {}
    if capabilities is None:
        capabilities = {}
    drop = capabilities.get("drop")
    if drop != ["ALL"]:
        msgs.append(f"{label} must drop ALL capabilities (got {drop!r})")
    if "add" in capabilities:
        msgs.append(
            f"{label} must not set capabilities.add (would re-grant after drop ALL); got {capabilities['add']!r}"
        )
    return msgs


def _check_container_seccomp(sc: dict, label: str) -> list[str]:
    """Container-level seccompProfile.type must be RuntimeDefault when set."""
    block = sc.get("seccompProfile")
    if block is not None and not isinstance(block, dict):
        return [f"{label} securityContext.seccompProfile must be a mapping if set"]
    seccomp_type = (block or {}).get("type")
    if seccomp_type is not None and seccomp_type != "RuntimeDefault":
        return [f"{label} container-level seccompProfile.type must be 'RuntimeDefault' if set (got {seccomp_type!r})"]
    return []


def _check_container_identity(sc: dict, pod_sc: dict, label: str) -> list[str]:
    """runAsNonRoot, runAsUser, runAsGroup with pod-level inheritance."""
    msgs: list[str] = []
    if _effective_field(sc, pod_sc, "runAsNonRoot") is not True:
        msgs.append(f"{label} must set runAsNonRoot: true (directly or via pod-level securityContext)")
    run_as_user = _effective_field(sc, pod_sc, "runAsUser")
    if not _is_real_int(run_as_user) or run_as_user <= 0:
        msgs.append(
            f"{label} runAsUser must be a positive integer "
            f"(directly or via pod-level securityContext); got {run_as_user!r}"
        )
    run_as_group = _effective_field(sc, pod_sc, "runAsGroup")
    if not _is_real_int(run_as_group) or run_as_group <= 0:
        msgs.append(
            f"{label} runAsGroup must be a positive integer "
            f"(directly or via pod-level securityContext); got {run_as_group!r}"
        )
    return msgs


def _check_k8s_container_security(container: dict, pod_sc: dict, rel: str, role: str) -> list[Violation]:
    """Validate a single container or init container's securityContext.

    Honors pod-level inheritance for runAsNonRoot/runAsUser/runAsGroup
    (Kubernetes lets these be set on the pod and inherited by containers
    unless overridden). Container-only fields (allowPrivilegeEscalation,
    capabilities, readOnlyRootFilesystem, privileged) must be set on the
    container itself.
    """
    name = container.get("name", "<unnamed>")
    label = f"{role} {name!r}"
    sc, structural_violations = _coerce_container_sc(container.get("securityContext"), label)

    field_msgs: list[str] = []
    field_msgs += _check_container_basic_fields(sc, label)
    field_msgs += _check_container_capabilities(sc, label)
    field_msgs += _check_container_seccomp(sc, label)
    field_msgs += _check_container_identity(sc, pod_sc, label)

    violations = [Violation("k8s-deployment-security-context", "ADR-006-R2", rel, msg) for msg in field_msgs]
    # Re-stamp rel onto any structural violations from the coercion step.
    for v in structural_violations:
        violations.append(Violation(v.check, v.rule_id, rel, v.message))
    return violations


def _iter_yaml_documents(text: str, rel: str) -> tuple[list[object], list[Violation]]:
    """Parse a (possibly multi-document) YAML file and return docs + parse violations."""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return (
            [],
            [
                Violation(
                    "k8s-deployment-security-context",
                    "ADR-006-R2",
                    _ADR_GUARD_PATH,
                    "PyYAML is required to validate K8s deployment security contexts; "
                    "install pyyaml in the runtime environment",
                )
            ],
        )

    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        return (
            [],
            [
                Violation(
                    "k8s-deployment-security-context",
                    "ADR-006-R2",
                    rel,
                    f"YAML parse error: {exc}",
                )
            ],
        )
    return ([d for d in docs if d is not None], [])


def _v(rel: str, msg: str) -> Violation:
    """Shorthand: ADR-006-R2 violation builder for the K8s deployment check."""
    return Violation("k8s-deployment-security-context", "ADR-006-R2", rel, msg)


def _resolve_pod_spec(doc: dict, rel: str) -> tuple[dict | None, list[Violation]]:
    """Walk doc.spec.template.spec, validating each level is a mapping.

    Returns (pod_spec_or_None, violations). When any level is non-mapping,
    pod_spec is None and the caller skips the per-document checks.
    """
    spec = doc.get("spec")
    if spec is not None and not isinstance(spec, dict):
        return None, [_v(rel, f"spec must be a mapping (got {type(spec).__name__})")]
    spec = spec or {}

    template = spec.get("template")
    if template is not None and not isinstance(template, dict):
        return None, [_v(rel, f"spec.template must be a mapping (got {type(template).__name__})")]
    template = template or {}

    pod_spec = template.get("spec")
    if pod_spec is not None and not isinstance(pod_spec, dict):
        return None, [
            _v(
                rel,
                f"spec.template.spec must be a mapping (got {type(pod_spec).__name__})",
            )
        ]
    return pod_spec or {}, []


def _resolve_pod_sc(pod_spec: dict, rel: str) -> tuple[dict, list[Violation]]:
    """Coerce pod_spec.securityContext to a dict, surfacing structural problems."""
    pod_sc = pod_spec.get("securityContext") or {}
    if not isinstance(pod_sc, dict):
        return {}, [
            _v(
                rel,
                "spec.template.spec.securityContext must be a mapping "
                "(YAML aliases or non-mapping values are not supported by this guard)",
            )
        ]
    return pod_sc, []


def _validate_containers_list(
    pod_spec: dict, pod_sc: dict, rel: str, key: str, role: str, *, required: bool
) -> list[Violation]:
    """Validate every container entry in pod_spec[key]. `required=True` rejects empty/missing."""
    raw = pod_spec.get(key)
    if raw is None and not required:
        return []
    if not isinstance(raw, list) or (required and len(raw) == 0):
        if required:
            return [
                _v(
                    rel,
                    f"spec.template.spec.{key} must be a non-empty list (got {type(raw).__name__})",
                )
            ]
        return []

    violations: list[Violation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            violations.append(_v(rel, f"{role} entry must be a mapping (got {type(entry).__name__})"))
            continue
        violations.extend(_check_k8s_container_security(entry, pod_sc, rel, role))
    return violations


def _validate_deployment_documents(docs: list[object], rel: str) -> list[Violation]:
    """Apply the ADR-006-R2 security-context rule to every Deployment in a parsed document set.

    rel is the file-or-source label included in any Violation produced.
    """
    violations: list[Violation] = []
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue

        pod_spec, structural = _resolve_pod_spec(doc, rel)
        violations.extend(structural)
        if pod_spec is None:
            continue

        pod_sc, sc_violations = _resolve_pod_sc(pod_spec, rel)
        violations.extend(sc_violations)
        violations.extend(_check_k8s_pod_security(pod_sc, rel))
        violations.extend(_validate_containers_list(pod_spec, pod_sc, rel, "containers", "container", required=True))
        violations.extend(
            _validate_containers_list(pod_spec, pod_sc, rel, "initContainers", "initContainer", required=False)
        )
    return violations


def _render_chart_for_validation(
    repo_root: Path, values_files: tuple[str, ...]
) -> tuple[list[tuple[list[object], str]], list[Violation]]:
    """Run `helm template` for each values file and return (parsed docs, label) pairs.

    Returns (rendered_docs_per_values_file, violations). When helm is not
    available the call returns a single Violation pointing at adr_guard.py
    so CI surfaces the missing prerequisite rather than passing silently.
    """
    chart_dir = repo_root / HELM_CHART_DIR
    if not chart_dir.exists():
        return [], [
            Violation(
                "k8s-deployment-security-context",
                "ADR-006-R2",
                HELM_CHART_DIR,
                "configured Helm chart directory is missing; cannot validate "
                "the authoritative deployment contract per ADR-007",
            )
        ]

    import shutil

    helm = shutil.which("helm")
    if helm is None:
        return [], [
            Violation(
                "k8s-deployment-security-context",
                "ADR-006-R2",
                _ADR_GUARD_PATH,
                "helm CLI is required to render the chart for ADR-006-R2 validation; "
                "install helm in the runtime environment "
                "(CI installs it in the adr-conformance and adr-guard-tests jobs)",
            )
        ]

    rendered: list[tuple[list[object], str]] = []
    violations: list[Violation] = []
    for vf in values_files:
        values_path = repo_root / vf
        if not values_path.exists():
            violations.append(
                Violation(
                    "k8s-deployment-security-context",
                    "ADR-006-R2",
                    vf,
                    "configured Helm values file is missing; cannot validate this environment's chart-rendered output",
                )
            )
            continue
        result = subprocess.run(
            [helm, "template", str(chart_dir), "-f", str(values_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            violations.append(
                Violation(
                    "k8s-deployment-security-context",
                    "ADR-006-R2",
                    vf,
                    f"helm template failed: {result.stderr.strip() or result.stdout.strip()}",
                )
            )
            continue
        # Violation.path stays repo-relative (the values file) so existing
        # exception globs in docs/adr/exceptions.yaml can match. The render
        # context goes in messages via _validate_deployment_documents, which
        # callers extend with their own context if needed.
        docs, parse_violations = _iter_yaml_documents(result.stdout, vf)
        violations.extend(parse_violations)
        rendered.append((docs, vf))
    return rendered, violations


def _scan_targets(repo_root: Path, files: list[str] | None) -> tuple[bool, bool, list[Path]]:
    """Decide whether to scan base manifests, chart, and which base files to read.

    --all/CI mode (`files is None`) always exercises the chart branch so a
    missing chart directory surfaces as a violation. files-mode (pre-commit)
    triggers each branch only when the changed file set actually overlaps.
    """
    base_dir = repo_root / K8S_BASE_DEPLOYMENT_DIR
    if files is None:
        scan_base = base_dir.exists()
        base_files = sorted(list(base_dir.rglob("*.yaml")) + list(base_dir.rglob("*.yml"))) if scan_base else []
        return scan_base, True, base_files

    scan_base = False
    scan_chart = False
    base_files: list[Path] = []
    for f in files:
        if f.startswith(K8S_BASE_DEPLOYMENT_DIR + "/") and f.endswith((".yaml", ".yml")):
            scan_base = True
            full = repo_root / f
            if full.exists():
                base_files.append(full)
        if f.startswith(HELM_CHART_DIR + "/"):
            scan_chart = True
    return scan_base, scan_chart, base_files


def _validate_base_files(repo_root: Path, base_files: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in base_files:
        rel = _repo_relative(path, repo_root)
        docs, parse_violations = _iter_yaml_documents(path.read_text(encoding="utf-8"), rel)
        violations.extend(parse_violations)
        violations.extend(_validate_deployment_documents(docs, rel))
    return violations


def _validate_chart_renders(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    rendered, render_violations = _render_chart_for_validation(repo_root, HELM_VALUES_FILES)
    violations.extend(render_violations)
    for docs, label in rendered:
        violations.extend(_validate_deployment_documents(docs, label))
    return violations


def _network_policy_violation(path: str, message: str) -> Violation:
    return Violation(
        "k8s-network-policy-coverage",
        "ADR-006-R3",
        path,
        message,
    )


def _as_network_policy_violations(violations: list[Violation]) -> list[Violation]:
    return [_network_policy_violation(violation.path, violation.message) for violation in violations]


def _is_shifter_namespace(name: object) -> bool:
    return isinstance(name, str) and name.startswith("shifter-")


def _document_namespace(doc: object) -> str | None:
    if not isinstance(doc, dict):
        return None
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return None
    namespace = metadata.get("namespace")
    if isinstance(namespace, str):
        return namespace
    return None


def _collect_shifter_namespaces(docs: list[object]) -> set[str]:
    namespaces: set[str] = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        metadata = doc.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if doc.get("kind") == "Namespace":
            name = metadata.get("name")
            if _is_shifter_namespace(name):
                namespaces.add(name)
        namespace = metadata.get("namespace")
        if _is_shifter_namespace(namespace):
            namespaces.add(namespace)
    return namespaces


def _is_default_deny_network_policy(doc: dict) -> bool:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return False
    policy_types = spec.get("policyTypes")
    if not isinstance(policy_types, list):
        return False
    if not {"Ingress", "Egress"}.issubset(set(policy_types)):
        return False
    if spec.get("podSelector") != {}:
        return False
    ingress = spec.get("ingress", [])
    egress = spec.get("egress", [])
    return ingress == [] and egress == []


def _network_policy_name(doc: dict) -> str:
    metadata = doc.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return metadata["name"]
    return "<unnamed>"


def _shifter_network_policy_docs(docs: list[object]) -> list[tuple[dict, str]]:
    policies: list[tuple[dict, str]] = []
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
            continue
        namespace = _document_namespace(doc)
        if not _is_shifter_namespace(namespace):
            continue
        policies.append((doc, namespace))
    return policies


def _default_deny_network_policy_namespaces(
    policies: list[tuple[dict, str]],
) -> set[str]:
    return {namespace for doc, namespace in policies if _is_default_deny_network_policy(doc)}


def _iter_egress_destinations(doc: dict) -> list[tuple[int, object]]:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return []
    egress_rules = spec.get("egress", [])
    if not isinstance(egress_rules, list):
        return []

    destinations: list[tuple[int, object]] = []
    for rule_index, rule in enumerate(egress_rules):
        if not isinstance(rule, dict) or not isinstance(rule.get("to"), list):
            continue
        destinations.extend((rule_index, destination) for destination in rule["to"])
    return destinations


def _destination_ip_block_cidr(destination: object) -> object:
    if not isinstance(destination, dict):
        return None
    ip_block = destination.get("ipBlock")
    if not isinstance(ip_block, dict):
        return None
    return ip_block.get("cidr")


def _broad_egress_network_policy_violations(policies: list[tuple[dict, str]], rel: str) -> list[Violation]:
    violations: list[Violation] = []
    broad_cidrs = {"0.0.0.0/0", "::/0"}
    for doc, namespace in policies:
        for rule_index, destination in _iter_egress_destinations(doc):
            cidr = _destination_ip_block_cidr(destination)
            if cidr not in broad_cidrs:
                continue
            violations.append(
                _network_policy_violation(
                    rel,
                    f"NetworkPolicy {namespace}/{_network_policy_name(doc)} "
                    f"egress rule {rule_index} allows broad CIDR {cidr}; "
                    "ADR-006-R3 requires explicit service ranges",
                )
            )
    return violations


def _missing_default_deny_network_policy_violations(
    namespaces: set[str], default_deny_namespaces: set[str], rel: str
) -> list[Violation]:
    return [
        _network_policy_violation(
            rel,
            f"namespace {namespace} lacks a default-deny NetworkPolicy covering both ingress and egress",
        )
        for namespace in sorted(namespaces - default_deny_namespaces)
    ]


def _validate_network_policy_documents(docs: list[object], rel: str) -> list[Violation]:
    namespaces = _collect_shifter_namespaces(docs)
    policies = _shifter_network_policy_docs(docs)
    default_deny_namespaces = _default_deny_network_policy_namespaces(policies)
    return [
        *_broad_egress_network_policy_violations(policies, rel),
        *_missing_default_deny_network_policy_violations(namespaces, default_deny_namespaces, rel),
    ]


def _validate_network_policy_base_files(repo_root: Path, base_files: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    docs: list[object] = []
    for path in base_files:
        rel = _repo_relative(path, repo_root)
        parsed, parse_violations = _iter_yaml_documents(path.read_text(encoding="utf-8"), rel)
        docs.extend(parsed)
        violations.extend(_as_network_policy_violations(parse_violations))
    if base_files:
        violations.extend(_validate_network_policy_documents(docs, K8S_BASE_DEPLOYMENT_DIR))
    return violations


def _validate_network_policy_chart_renders(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    rendered, render_violations = _render_chart_for_validation(repo_root, HELM_VALUES_FILES)
    violations.extend(_as_network_policy_violations(render_violations))
    for docs, label in rendered:
        violations.extend(_validate_network_policy_documents(docs, label))
    return violations


def check_k8s_deployment_security_context(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Verify pod, container, and init-container securityContext on Deployments (ADR-006-R2).

    Two enforcement sources are scanned per ADR-006-R2 and ADR-007:

    1. **Base manifest snapshots** under `platform/k8s/gcp/base/` (recursive):
       every YAML document with `kind: Deployment` is validated regardless of
       filename or extension.
    2. **Helm chart rendered output**: the chart at
       `platform/charts/shifter` is rendered via `helm template` for each
       supported values file in `HELM_VALUES_FILES`, and every Deployment
       document in the rendered output is validated. Per ADR-007 the chart is
       the authoritative deployment contract; this catches regressions where
       a chart template or values file removes a required securityContext
       field even if the base snapshots remain compliant.

    Honors pod-level securityContext inheritance for runAsNonRoot, runAsUser,
    and runAsGroup (Kubernetes lets these be set on the pod and inherited by
    containers unless overridden).

    Per Deployment:
    - pod-level seccompProfile.type == 'RuntimeDefault'
    - every container AND initContainer (effective context after pod-level
      inheritance):
      - allowPrivilegeEscalation: false (container-only)
      - capabilities.drop: ['ALL'] AND no capabilities.add (container-only)
      - readOnlyRootFilesystem: true (container-only)
      - privileged: not true (container-only)
      - container-level seccompProfile.type, when set, equals 'RuntimeDefault'
      - runAsNonRoot: true (effective)
      - runAsUser, runAsGroup are positive integers (effective; booleans rejected)
    """
    scan_base, scan_chart, base_files = _scan_targets(repo_root, files)
    if not (scan_base or scan_chart):
        return []

    violations: list[Violation] = []
    if scan_base:
        violations.extend(_validate_base_files(repo_root, base_files))
    if scan_chart:
        violations.extend(_validate_chart_renders(repo_root))
    return violations


def check_k8s_network_policy_coverage(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Verify Shifter namespaces are isolated by default-deny NetworkPolicies."""
    scan_base, scan_chart, base_files = _scan_targets(repo_root, files)
    if not (scan_base or scan_chart):
        return []

    violations: list[Violation] = []
    if scan_base:
        violations.extend(_validate_network_policy_base_files(repo_root, base_files))
    if scan_chart:
        violations.extend(_validate_network_policy_chart_renders(repo_root))
    return violations
