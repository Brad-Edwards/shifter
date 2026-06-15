"""``shifter-config`` — inspect and validate Shifter installation config.

``validate`` checks the shape of ``shifter.yaml`` — the backend selector, deployment
identity, secret references, and backend-specific ``settings`` mapping — so CI, deploy
scripts, and operators catch malformed root config before Terraform, Helm, Django,
workers, or deployment scripts run. ``runtime-inventory`` checks the checked-in runtime
env surfaces by file path and env-key name only. The *contents* of ``settings`` (and
which settings a backend requires) are validated by the selected backend bundle's
contract (#1113); the backend-aware setup/doctor UX is #1115. This command deliberately
stays small: parse paths, read files, print sanitized results.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import InstallationConfigError
from .loader import load_root_config
from .runtime_inventory import RUNTIME_SURFACES, validate_runtime_inventory

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
    inventory = subcommands.add_parser(
        "runtime-inventory",
        help="List or check the repo runtime configuration inventory.",
        description=(
            "List or check runtime configuration surfaces by file path and key name only. "
            "The checker never prints env values."
        ),
    )
    inventory.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to check (default: current directory).",
    )
    inventory.add_argument(
        "--check",
        action="store_true",
        help="Validate tracked runtime env files against the inventory.",
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


def _cmd_runtime_inventory(repo_root_str: str, *, check: bool) -> int:
    """List or validate checked-in runtime configuration surfaces."""

    repo_root = Path(repo_root_str)
    if check:
        issues = validate_runtime_inventory(repo_root)
        if issues:
            print(f"{repo_root}: runtime inventory invalid", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue.render()}", file=sys.stderr)
            return 1
        print(f"{repo_root}: OK — runtime inventory is current")
        return 0

    print("Runtime configuration surfaces:")
    for surface in RUNTIME_SURFACES:
        print(f"- {surface.path}: {surface.authority} ({surface.owner})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the shifter-config command-line interface."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    if args.command == "validate":
        exit_code = _cmd_validate(args.path)
    elif args.command == "runtime-inventory":
        exit_code = _cmd_runtime_inventory(args.repo_root, check=args.check)
    else:
        parser.print_help(sys.stderr)  # pragma: no cover - argparse rejects unknown subcommands first
        exit_code = 2
    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via ``python -m installation``
    sys.exit(main())
