"""``shifter-config`` — inspect, validate, and render the root Shifter installation config.

``validate`` checks the *shape* of ``shifter.yaml`` — the backend selector, deployment
identity, secret references, and that backend-specific ``settings`` is a mapping — so CI,
deploy scripts, and operators catch a malformed root config before Terraform, Helm,
Django, workers, or deployment scripts run. The *contents* of ``settings`` (and which
settings a backend requires) are validated by the selected backend bundle's contract
(#1113); the backend-aware setup/doctor UX is #1115.

``render`` (#958) turns the validated, normalized ``settings.range_egress`` policy into
the provider-specific Terraform bridge ``.tfvars`` for the config's backend, so the
deployed firewall rules are generated from the single authoritative source rather than
hand-copied into a second gitignored allowlist (ADR-017-R4).

This command deliberately stays small: parse a path, read a file, print sanitized results.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import InstallationConfigError
from .loader import load_root_config
from .render import render_tfvars

DEFAULT_CONFIG_FILENAME = "shifter.yaml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shifter-config",
        description="Inspect and validate the root Shifter installation config.",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")
    validate = subcommands.add_parser(
        "validate",
        help=f"Validate the shape of a root installation config (default: ./{DEFAULT_CONFIG_FILENAME}).",
        description=(
            f"Validate the shape of a root installation config (default: ./{DEFAULT_CONFIG_FILENAME}): "
            "the backend selector, deployment identity, secret references, and that backend-specific "
            "settings is a mapping. The contents of settings are validated by the selected backend bundle."
        ),
    )
    validate.add_argument(
        "path",
        nargs="?",
        default=DEFAULT_CONFIG_FILENAME,
        help=f"Path to the config file (default: ./{DEFAULT_CONFIG_FILENAME}).",
    )
    render = subcommands.add_parser(
        "render",
        help="Render the range egress policy into provider Terraform bridge .tfvars.",
        description=(
            "Render the validated settings.range_egress policy into the provider-specific "
            "Terraform bridge variables for the config's backend (AWS: victim_allowed_cidrs; "
            "GCP: range_egress_mode + range_egress_allowed_cidrs). The rendered file is the "
            "single source for the deployed allowlist, generated from shifter.yaml so the "
            "configured policy and deployed firewall rules cannot diverge."
        ),
    )
    render.add_argument(
        "path",
        nargs="?",
        default=DEFAULT_CONFIG_FILENAME,
        help=f"Path to the config file (default: ./{DEFAULT_CONFIG_FILENAME}).",
    )
    render.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        default=None,
        help="Write the rendered .tfvars to FILE (default: stdout).",
    )
    return parser


def _cmd_validate(path_str: str) -> int:
    config_path = Path(path_str)
    try:
        config = load_root_config(config_path)
    except InstallationConfigError as exc:
        print(f"{config_path}: invalid", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue.render()}", file=sys.stderr)
        return 1
    print(
        f"{config_path}: OK — root config shape is valid "
        f"(backend={config.backend}, profile={config.deployment.profile})"
    )
    return 0


def _emit_rendered(rendered: str, output: str | None, backend: str) -> int:
    """Write rendered tfvars to ``output`` (or stdout when None); return the exit code."""
    if output is None:
        sys.stdout.write(rendered)
        return 0
    output_path = Path(output)
    try:
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        detail = getattr(exc, "strerror", None) or str(exc)
        print(f"{output_path}: could not write rendered tfvars: {detail}", file=sys.stderr)
        return 1
    print(f"{output_path}: wrote range egress bridge tfvars ({backend}).", file=sys.stderr)
    return 0


def _cmd_render(path_str: str, output: str | None) -> int:
    """Render the range egress bridge tfvars for the config at ``path_str``."""
    config_path = Path(path_str)
    try:
        config = load_root_config(config_path)
    except InstallationConfigError as exc:
        print(f"{config_path}: invalid", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue.render()}", file=sys.stderr)
        return 1
    return _emit_rendered(render_tfvars(config), output, config.backend)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args.path)
    if args.command == "render":
        return _cmd_render(args.path, args.output)
    # No subcommand given (argparse rejects unknown subcommands before this point).
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via ``python -m installation``
    sys.exit(main())
