"""Static extraction of settings environment-variable bindings (#948).

Scans ``config/settings.py`` and ``config/_*.py`` for ``os.environ.get`` calls
so the generated manifest stays single-sourced with the code that reads each var.
Bindings resolved outside ``os.environ.get`` (e.g. ``require_environment()``)
are listed in ``_EXPLICIT_BINDINGS``.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = CONFIG_DIR / "env-manifest.json"


@dataclass(frozen=True, slots=True)
class EnvBinding:
    name: str
    default: str | None
    source_file: str


_EXPLICIT_BINDINGS = (EnvBinding(name="ENVIRONMENT", default=None, source_file="config/settings.py"),)


def _default_repr(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return None
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.IfExp):
        return f"{_default_repr(node.body)} if ... else {_default_repr(node.orelse)}"
    try:
        return ast.unparse(node)
    except Exception:
        return "<dynamic>"


def _extract_from_file(path: Path) -> list[EnvBinding]:
    tree = ast.parse(path.read_text(), filename=str(path))
    bindings: list[EnvBinding] = []
    rel = path.relative_to(CONFIG_DIR.parent).as_posix()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        name = node.args[0].value
        default = _default_repr(node.args[1]) if len(node.args) > 1 else None
        bindings.append(EnvBinding(name=name, default=default, source_file=rel))
    return bindings


def collect_env_bindings(config_dir: Path | None = None) -> list[EnvBinding]:
    root = config_dir or CONFIG_DIR
    files = sorted(root.glob("settings.py")) + sorted(root.glob("_*.py"))
    merged: dict[str, EnvBinding] = {binding.name: binding for binding in _EXPLICIT_BINDINGS}
    for path in files:
        if path.name == "_env_manifest.py":
            continue
        for binding in _extract_from_file(path):
            merged.setdefault(binding.name, binding)
    return sorted(merged.values(), key=lambda item: item.name)


def render_manifest_json(bindings: list[EnvBinding]) -> str:
    payload = {
        "schema_version": 1,
        "generated_from": "config/_env_manifest.py",
        "variables": [asdict(binding) for binding in bindings],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_manifest(path: Path | None = None) -> Path:
    target = path or MANIFEST_PATH
    bindings = collect_env_bindings()
    target.write_text(render_manifest_json(bindings))
    return target


def manifest_is_current(path: Path | None = None) -> bool:
    target = path or MANIFEST_PATH
    if not target.exists():
        return False
    expected = render_manifest_json(collect_env_bindings())
    return target.read_text() == expected
