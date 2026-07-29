"""Layer-import boundaries, INSTALLED_APPS classification, cloud-factory seam."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .._common import (
    LAYERS,
    Violation,
    _repo_relative,
)


IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+"
    r"((?:shared|engine|cms|management|mission_control|ctf|config|workspaces)(?:\.\w+)*)",
    re.MULTILINE,
)
CYBERSCRIPT_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+(cyberscript(?:\.\w+)*)",
    re.MULTILINE,
)
CYBERSCRIPT_ALLOWED_LAYER = "shared"


def _iter_yaml_section(config_path: Path, section: str) -> "list[tuple[str, list[str]]]":
    """Parse one top-level ``section:`` of the layer-policy YAML.

    Minimal, dependency-free reader for the two-level shape used by
    layer_imports.yaml (``section:`` -> ``key:`` -> ``- item`` list). Only the
    requested top-level section is parsed; other sections are ignored, so the
    ``classification`` and ``allowed`` blocks never bleed into each other.
    """
    result: dict[str, list[str]] = {}
    current_section: str | None = None
    current_key: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1]
            current_key = None
            continue
        if current_section != section:
            continue
        if indent == 2 and stripped.endswith(":"):
            current_key = stripped[:-1]
            result[current_key] = []
            continue
        if current_key is not None and indent >= 4 and stripped.startswith("- "):
            result[current_key].append(stripped[2:].strip())

    return list(result.items())


def load_allowed_imports(config_path: Path) -> dict[str, list[str]]:
    """Load the simple layer import policy without external YAML dependencies."""
    return dict(_iter_yaml_section(config_path, "allowed"))


def load_allowed_symbols(config_path: Path) -> dict[str, dict[str, list[str]]]:
    """Parse the 3-level ``allowed_symbols:`` block (layer -> facade -> [symbols]).

    Dependency-free reader mirroring ``_iter_yaml_section`` one level deeper, for
    the per-symbol facade allowlists (ADR-001-R4). Only the ``allowed_symbols``
    top-level section is parsed; other sections are ignored.
    """
    result: dict[str, dict[str, list[str]]] = {}
    in_section = False
    current_layer: str | None = None
    current_facade: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if indent == 0 and stripped.endswith(":"):
            in_section = stripped[:-1] == "allowed_symbols"
            current_layer = None
            current_facade = None
            continue
        if not in_section:
            continue
        if indent == 2 and stripped.endswith(":"):
            current_layer = stripped[:-1]
            result[current_layer] = {}
            current_facade = None
            continue
        if indent == 4 and stripped.endswith(":") and current_layer is not None:
            current_facade = stripped[:-1]
            result[current_layer][current_facade] = []
            continue
        if current_layer is not None and current_facade is not None and indent >= 6 and stripped.startswith("- "):
            result[current_layer][current_facade].append(stripped[2:].strip())

    return result


def load_classification(config_path: Path) -> dict[str, list[str]]:
    """Load the canonical package classification without external YAML deps."""
    return dict(_iter_yaml_section(config_path, "classification"))


def is_import_allowed(from_layer: str, module_path: str, allowed: dict[str, list[str]]) -> bool:
    """Check whether an import is allowed by the layer policy.

    A dotted entry (e.g. ``cms.services``) is the public facade: the exact
    facade and its public submodules are allowed, but a private split-package
    submodule — any path component after the facade that starts with ``_``
    (``cms.services._range_pause``) — is not a cross-layer seam (ADR-001-R1).
    ``shared`` is the contracts layer and stays freely importable.
    """
    for entry in allowed.get(from_layer, []):
        if entry == "shared":
            if module_path == "shared" or module_path.startswith("shared."):
                return True
        elif "." in entry:
            if module_path == entry:
                return True
            if module_path.startswith(entry + "."):
                remainder = module_path[len(entry) + 1 :]
                if not any(part.startswith("_") for part in remainder.split(".")):
                    return True
        elif module_path == entry:
            return True
    return False


def iter_layer_files(repo_root: Path, files: list[str] | None) -> list[tuple[str, str]]:
    """Return repo-relative Python files grouped by originating layer."""
    candidates: list[Path]
    if files is None:
        candidates = list((repo_root / "shifter" / "shifter_platform").rglob("*.py"))
    else:
        candidates = [repo_root / rel for rel in files if rel.endswith(".py")]

    layer_files: list[tuple[str, str]] = []
    for path in candidates:
        if not path.exists():
            continue
        rel = _repo_relative(path, repo_root)
        parts = Path(rel).parts
        if len(parts) < 4:
            continue
        if parts[0:2] != ("shifter", "shifter_platform"):
            continue
        layer = parts[2]
        if layer in LAYERS:
            layer_files.append((rel, layer))

    return sorted(set(layer_files))


def iter_private_facade_imports(text: str) -> set[str]:
    """Return ``layer.path._private`` targets imported via ``from ... import``.

    The ``IMPORT_PATTERN`` regex only captures the module path, so
    ``from cms.services import _range_pause`` looks like an allowed
    ``cms.services`` facade import. This AST pass recovers the imported name and,
    when it is private (starts with ``_``) and the module belongs to one of our
    layers, reconstructs the effective dotted target (``cms.services._range_pause``).
    Relative imports (``from ._x import y``) and public names are ignored.
    """
    targets: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return targets

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
            continue
        if node.module.split(".")[0] not in LAYERS:
            continue
        targets.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name.startswith("_"))
    return targets


def _is_public_facade_descendant(module: str, facade: str) -> bool:
    """True when ``module`` is a PUBLIC descendant module of ``facade``.

    ``engine.services.runtime`` is a descendant of ``engine.services``. A private
    component anywhere in the remainder (``engine.services._x``) is not, because
    private split-package modules are already rejected by ADR-001-R1; this keeps
    the two rules from double-reporting the same import.
    """
    if not module.startswith(facade + "."):
        return False
    remainder = module[len(facade) + 1 :]
    return not any(part.startswith("_") for part in remainder.split("."))


def iter_facade_symbol_imports(text: str, restricted_facades: set[str]) -> "tuple[dict[str, set[str]], set[str]]":
    """Return (public symbols imported per restricted facade, non-facade bypasses).

    The only sanctioned shape for a symbol-restricted facade (ADR-001-R4) is an
    absolute ``from <facade> import <name>``; its public names feed the allowlist
    check. Every other shape that reaches the facade or one of its public
    descendants is a bypass: a relative ``from ..<facade> import name``, a
    descendant ``from <facade>.sub import name``, a bare ``import <facade>``, or
    ``import <facade>.sub``. Private ``_``-prefixed targets are left to the
    public-facade rule (ADR-001-R1) so the two rules do not double-report.
    """
    from_symbols: dict[str, set[str]] = {}
    module_bypass: set[str] = set()
    if not restricted_facades:
        return from_symbols, module_bypass
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return from_symbols, module_bypass

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module
            if not module:
                continue
            for facade in restricted_facades:
                if node.level == 0 and module == facade:
                    for alias in node.names:
                        if not alias.name.startswith("_"):
                            from_symbols.setdefault(facade, set()).add(alias.name)
                elif module == facade or _is_public_facade_descendant(module, facade):
                    # Relative facade import (level > 0) or a facade descendant.
                    module_bypass.add(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for facade in restricted_facades:
                    if alias.name == facade or _is_public_facade_descendant(alias.name, facade):
                        module_bypass.add(alias.name)
    return from_symbols, module_bypass


def _symbol_facade_violations(
    rel: str,
    from_layer: str,
    text: str,
    allowed_symbols: dict[str, dict[str, list[str]]],
) -> list[Violation]:
    """Return ADR-001-R4 per-symbol facade-allowlist violations for one file."""
    restrictions = allowed_symbols.get(from_layer, {})
    if not restrictions:
        return []

    violations: list[Violation] = []
    from_symbols, module_bypass = iter_facade_symbol_imports(text, set(restrictions))
    for facade, names in sorted(from_symbols.items()):
        allowed_names = set(restrictions.get(facade, []))
        for name in sorted(names):
            if name not in allowed_names:
                violations.append(
                    Violation(
                        "layer-imports",
                        "ADR-001-R4",
                        rel,
                        f"{from_layer} may import only sanctioned symbols from {facade}; "
                        f"'{name}' is not allowed (front control-plane operations through cms.services)",
                    )
                )
    for module in sorted(module_bypass):
        violations.append(
            Violation(
                "layer-imports",
                "ADR-001-R4",
                rel,
                f"{from_layer} may not reach {module} except via 'from <facade> import <sanctioned symbol>' "
                "(no relative, descendant, or bare-module imports of the restricted facade)",
            )
        )
    return violations


def check_layer_imports(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Check the layer import policy against selected files."""
    violations: list[Violation] = []
    config_path = repo_root / "scripts" / "check_layer_imports" / "layer_imports.yaml"
    allowed = load_allowed_imports(config_path)
    allowed_symbols = load_allowed_symbols(config_path)

    for rel, from_layer in iter_layer_files(repo_root, files):
        text = (repo_root / rel).read_text(encoding="utf-8")
        regex_modules = set(IMPORT_PATTERN.findall(text))
        # AST recovers `from facade import _private`, which the regex sees only
        # as the allowed facade module path.
        private_modules = iter_private_facade_imports(text)
        for module in sorted(regex_modules | private_modules):
            to_layer = module.split(".", 1)[0]
            if to_layer == from_layer:
                continue
            if not is_import_allowed(from_layer, module, allowed):
                violations.append(
                    Violation(
                        "layer-imports",
                        "ADR-001-R1",
                        rel,
                        f"{from_layer} may not import {module}",
                    )
                )
        # ADR-001-R4: symbol-restricted facade seams (e.g. mission_control ->
        # engine.services) permit only the enumerated data-plane symbols.
        violations.extend(_symbol_facade_violations(rel, from_layer, text, allowed_symbols))
        if from_layer != CYBERSCRIPT_ALLOWED_LAYER:
            for module in sorted(set(CYBERSCRIPT_IMPORT_PATTERN.findall(text))):
                violations.append(
                    Violation(
                        "layer-imports",
                        "ADR-001-R1",
                        rel,
                        f"{from_layer} may not import {module}; use shared shims",
                    )
                )

    return violations


def check_cross_layer_model_imports(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Find direct cross-layer model imports in selected runtime files."""
    violations: list[Violation] = []

    for rel, from_layer in iter_layer_files(repo_root, files):
        text = (repo_root / rel).read_text(encoding="utf-8")
        for module in sorted(set(IMPORT_PATTERN.findall(text))):
            parts = module.split(".")
            to_layer = parts[0]
            if to_layer == from_layer:
                continue
            if len(parts) >= 2 and parts[1] == "models":
                violations.append(
                    Violation(
                        "cross-layer-model-imports",
                        "ADR-001-R2",
                        rel,
                        f"{from_layer} imports {module}; prefer a service seam or shared contract",
                    )
                )

    return violations


_PLATFORM_REL = "shifter/shifter_platform"
_SETTINGS_REL = "shifter/shifter_platform/config/settings.py"
_LAYER_POLICY_REL = "scripts/check_layer_imports/layer_imports.yaml"


def _classified_packages(repo_root: Path) -> set[str]:
    """Return the union of every canonically classified first-party package."""
    classification = load_classification(repo_root / _LAYER_POLICY_REL)
    return {pkg for packages in classification.values() for pkg in packages}


def _local_appconfig_packages(repo_root: Path) -> set[str]:
    """Return local packages under shifter_platform whose apps.py defines an AppConfig.

    A tracked local Django app is a top-level package with an ``apps.py`` that
    subclasses ``AppConfig``. Directories without an AppConfig (e.g. the retired
    ``documentation`` package, ADR-038) are not tracked apps and are excluded.
    """
    platform = repo_root / _PLATFORM_REL
    found: set[str] = set()
    for apps_py in platform.glob("*/apps.py"):
        try:
            text = apps_py.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"class\s+\w+\s*\(\s*[\w.]*AppConfig\b", text):
            found.add(apps_py.parent.name)
    return found


def _parse_installed_apps(settings_text: str) -> tuple[list[str], list[str]]:
    """Return (resolved app strings, unresolved dynamic reprs) from INSTALLED_APPS.

    Parses the ``INSTALLED_APPS = [...]`` literal plus ``INSTALLED_APPS.append(...)``
    calls. Any entry that is not a string constant — a dynamic expression, or an
    ``extend``/``insert`` mutation — is returned as unresolved so the check fails
    closed rather than silently skipping it.
    """
    resolved: list[str] = []
    unresolved: list[str] = []

    def _collect_sequence(value: ast.expr, dynamic_detail: str) -> None:
        """Add string-literal elements of a list/tuple; flag anything else."""
        if isinstance(value, (ast.List, ast.Tuple)):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    resolved.append(elt.value)
                else:
                    unresolved.append("non-literal INSTALLED_APPS entry")
        else:
            unresolved.append(dynamic_detail)

    tree = ast.parse(settings_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "INSTALLED_APPS":
                    _collect_sequence(node.value, "INSTALLED_APPS not assigned a list/tuple literal")
        elif isinstance(node, ast.AugAssign):
            # INSTALLED_APPS += [...] / += SOME_APPS
            target = node.target
            if isinstance(target, ast.Name) and target.id == "INSTALLED_APPS" and isinstance(node.op, ast.Add):
                _collect_sequence(node.value, "unresolvable INSTALLED_APPS += mutation")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if isinstance(func.value, ast.Name) and func.value.id == "INSTALLED_APPS":
                if func.attr == "append":
                    arg = node.args[0] if node.args else None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        resolved.append(arg.value)
                    else:
                        unresolved.append("non-literal INSTALLED_APPS.append() argument")
                elif func.attr in {"extend", "insert", "__iadd__", "__add__"}:
                    unresolved.append(f"unresolvable INSTALLED_APPS.{func.attr}() mutation")
    return resolved, unresolved


def check_installed_apps_classified(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Fail closed when a first-party INSTALLED_APPS app is unclassified (#1523).

    Enforces set-equality between the canonical classification
    (layer_imports.yaml), the tracked local AppConfig packages, and the
    first-party apps actually installed. Adding a local app to INSTALLED_APPS
    without classifying it, leaving a stale classification entry, or introducing
    a dynamic INSTALLED_APPS entry the checker cannot resolve, all fail closed.
    """
    del files  # whole-tree invariant; not file-scoped
    settings_path = repo_root / _SETTINGS_REL
    policy_path = repo_root / _LAYER_POLICY_REL
    if not settings_path.exists() or not policy_path.exists():
        return []

    violations: list[Violation] = []
    classified = _classified_packages(repo_root)
    local_apps = _local_appconfig_packages(repo_root)
    installed, unresolved = _parse_installed_apps(settings_path.read_text(encoding="utf-8"))

    for detail in unresolved:
        violations.append(
            Violation(
                "installed-apps-classified",
                "ADR-001-R3",
                _SETTINGS_REL,
                f"INSTALLED_APPS has an entry the classifier cannot resolve ({detail}); "
                "use a string literal so first-party apps stay classifiable",
            )
        )

    installed_first_party = {entry.split(".")[0] for entry in installed} & local_apps

    for pkg in sorted(installed_first_party - classified):
        violations.append(
            Violation(
                "installed-apps-classified",
                "ADR-001-R3",
                _SETTINGS_REL,
                f"first-party app '{pkg}' is in INSTALLED_APPS but not classified in {_LAYER_POLICY_REL}",
            )
        )
    for pkg in sorted(local_apps - classified):
        violations.append(
            Violation(
                "installed-apps-classified",
                "ADR-001-R3",
                _LAYER_POLICY_REL,
                f"local app '{pkg}' has an AppConfig but is not classified in {_LAYER_POLICY_REL}",
            )
        )
    for pkg in sorted(classified - local_apps):
        violations.append(
            Violation(
                "installed-apps-classified",
                "ADR-001-R3",
                _LAYER_POLICY_REL,
                f"classified package '{pkg}' has no local AppConfig under {_PLATFORM_REL} (stale classification)",
            )
        )

    return violations


CLOUD_ROOTS = (
    "shifter/shifter_platform/shared/cloud",
    "shifter/engine/provisioner/cloud",
)
CLOUD_SKIP_FILES = {"__init__.py", "base.py"}


def check_cloud_factory_seam(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Ensure cloud adapter parity between AWS and GCP (ADR-005-R1).

    Every adapter module in cloud/aws/ must have a counterpart in cloud/gcp/
    and vice versa.  Modules named __init__.py and base.py are excluded since
    they serve structural rather than adapter roles.
    """
    if files is not None:
        cloud_touched = any(any(f.startswith(root + "/") for root in CLOUD_ROOTS) for f in files)
        if not cloud_touched:
            return []

    violations: list[Violation] = []
    for root in CLOUD_ROOTS:
        aws_dir = repo_root / root / "aws"
        gcp_dir = repo_root / root / "gcp"
        if not aws_dir.exists() or not gcp_dir.exists():
            continue
        aws_modules = {f.name for f in aws_dir.glob("*.py")} - CLOUD_SKIP_FILES
        gcp_modules = {f.name for f in gcp_dir.glob("*.py")} - CLOUD_SKIP_FILES
        for missing in sorted(aws_modules - gcp_modules):
            violations.append(
                Violation(
                    "cloud-factory-seam",
                    "ADR-005-R1",
                    f"{root}/gcp/{missing}",
                    f"AWS adapter {missing} has no GCP counterpart",
                )
            )
        for missing in sorted(gcp_modules - aws_modules):
            violations.append(
                Violation(
                    "cloud-factory-seam",
                    "ADR-005-R1",
                    f"{root}/aws/{missing}",
                    f"GCP adapter {missing} has no AWS counterpart",
                )
            )
    return violations
