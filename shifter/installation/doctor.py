"""Backend-aware ``doctor``: validate a selected backend before infrastructure is applied (#727).

``doctor`` reads the selected backend from ``shifter.yaml`` (via the one parser,
:func:`installation.loader.load_root_config`), resolves the backend's
:class:`~installation.contract.BackendBundle`, and runs the checks the bundle *already
declares* — required tools, required secret references, generated outputs, owned repo
paths, validation checks, and health checks. It adds no backend-specific check list of its
own: a new backend gets doctor coverage by declaring bundle metadata, not by editing a
central ``if backend == ...`` here (preflight #727).

Every check is labelled by side-effect tier so the operator knows what doctor did and what
it deliberately did not do:

* **local-only** (default): parse/validate config, look up tools on PATH, classify
  generated outputs, check owned repo paths exist, and run the bundle's credential-free,
  non-mutating validation checks (``terraform fmt -check``, ``helm template``,
  ``kubectl kustomize``, ``kube-linter lint``, root-config validate).
* **cloud-read-only** (opt-in, ``--checks cloud``): read-only health probes of the
  deployment endpoint. Never fetches a secret payload, never mutates.
* **deployment-mutating**: never run by doctor — only reported, so the operator knows the
  step is required and stays owned by the deploy workflow.

Reports are sanitized: they name the backend, profile, check names, missing tools, and
repo-relative paths, never config bodies, secret references or values, or captured command
output. The execution seams (tool lookup, command runner, health probe) are injected so the
logic is testable without a real subprocess or network. Constrained by ADR-011 / ADR-035.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess  # nosec B404
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .contract import BackendBundle
from .errors import ConfigIssue, InstallationConfigError
from .loader import load_root_config
from .registry import get_backend_bundle

#: How long a single validation-check subprocess may run before doctor gives up on it.
_COMMAND_TIMEOUT_SECONDS = 120

#: HTTP status range the health probe treats as healthy. Only 2xx is healthy; a 3xx means a
#: redirect the hardened opener refused to follow (SSRF guard), so it is reported, not passed.
_HEALTHY_MIN, _HEALTHY_MAX = 200, 299

#: The literal a bundle health-check target uses for the deployment's public hostname.
_DOMAIN_PLACEHOLDER = "<deployment.domain>"


class CheckTier(StrEnum):
    """The side-effect tier of a check (preflight #727 classification)."""

    LOCAL = "local-only"
    CLOUD_READ = "cloud-read-only"
    MUTATING = "deployment-mutating"


class CheckStatus(StrEnum):
    """The outcome of a single doctor check."""

    # These are check-status labels, not credentials — silence the "pass" heuristics.
    PASS = "pass"  # noqa: S105 # nosec B105
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"
    INFO = "info"


class CheckScope(StrEnum):
    """Which tiers of check doctor runs for this invocation."""

    LOCAL = "local"
    CLOUD = "cloud"
    ALL = "all"


@dataclass(frozen=True)
class CommandOutcome:
    """The result of running one validation-check command (no captured output — sanitized)."""

    returncode: int | None
    timed_out: bool = False
    error: str | None = None


@dataclass(frozen=True)
class HealthOutcome:
    """The result of one read-only health probe."""

    status_code: int | None
    reachable: bool
    error: str | None = None


@dataclass(frozen=True)
class DoctorCheckResult:
    """One check's outcome, tier-labelled and sanitized."""

    name: str
    tier: CheckTier
    status: CheckStatus
    summary: str
    blocking: bool = False
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier.value,
            "status": self.status.value,
            "summary": self.summary,
            "blocking": self.blocking,
            "remediation": self.remediation,
        }


@dataclass
class DoctorReport:
    """The full doctor run: the selected backend/profile and every check result."""

    backend: str | None
    profile: str | None
    results: list[DoctorCheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no blocking check failed. A non-blocking failure warns; it does not fail
        the run (every ``FAIL`` result is blocking by construction, so this also holds)."""
        return not any(result.status is CheckStatus.FAIL and result.blocking for result in self.results)

    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "profile": self.profile,
            "ok": self.ok,
            "results": [result.to_dict() for result in self.results],
        }


# --- default execution seams (real subprocess / network) ------------------------------


def _default_tool_probe(name: str) -> bool:
    """Whether ``name`` resolves to an executable on PATH."""
    return shutil.which(name) is not None


def _default_command_runner(argv: Sequence[str], cwd: Path) -> CommandOutcome:
    """Run a validated argv array without a shell, capturing nothing sensitive.

    The command comes from the backend contract's :class:`~installation.contract.CommandSpec`,
    which already rejects shell metacharacters, absolute paths, and traversal, so it is run
    with ``shell=False``. Output is captured only to keep it off the terminal — it is never
    read into a result, so a Terraform plan or provider response cannot leak through doctor.
    """
    env = {**os.environ, "AWS_PAGER": ""}
    try:
        completed = subprocess.run(  # noqa: S603 # nosec B603
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            shell=False,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome(returncode=None, timed_out=True)
    except OSError as exc:
        return CommandOutcome(returncode=None, error=getattr(exc, "strerror", None) or "could not run command")
    return CommandOutcome(returncode=completed.returncode)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects so a public health target cannot bounce the probe to an
    internal endpoint. Returning ``None`` makes urllib raise the 3xx as an ``HTTPError``."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        # Deliberately blocks every redirect (SSRF guard) by declining to build a redirect request.
        return None


def _resolves_to_public_only(hostname: str, port: int) -> bool | None:
    """Whether every address ``hostname`` resolves to is a public (global) address.

    Returns ``None`` when the name does not resolve, ``False`` when any resolved address is
    loopback/private/link-local/reserved/multicast/unspecified (for example ``127.0.0.1``,
    ``10.0.0.0/8``, or the cloud metadata address ``169.254.169.254``), else ``True``. This is
    the SSRF guard: a config-controlled ``deployment.domain`` cannot point the probe at an
    internal address.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    if not infos:
        return None
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        except ValueError:  # pragma: no cover - getaddrinfo returns valid address literals
            return False
    return True


def _default_health_probe(target: str, timeout: int) -> HealthOutcome:
    """Read-only HTTP(S) GET of ``target`` with SSRF hardening; never sends credentials.

    The target is treated as untrusted (``deployment.domain`` is operator/attacker-controllable
    in a supplied ``shifter.yaml``). Before connecting: the scheme is restricted to http(s),
    userinfo is rejected, and the hostname must resolve to public addresses only; redirects are
    refused so the request cannot be bounced to an internal endpoint. A narrow DNS-rebinding
    window remains between resolution and connect; it is accepted for this local pre-deploy tool
    because the response body is never read and the primary vectors (direct internal resolution,
    redirect-to-internal) are closed.
    """
    parsed = urlsplit(target)
    if parsed.scheme not in ("http", "https"):
        return HealthOutcome(status_code=None, reachable=False, error="unsupported target scheme")
    if parsed.username or parsed.password:
        return HealthOutcome(status_code=None, reachable=False, error="target must not contain credentials")
    hostname = parsed.hostname
    if not hostname:
        return HealthOutcome(status_code=None, reachable=False, error="target has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    public = _resolves_to_public_only(hostname, port)
    if public is None:
        return HealthOutcome(status_code=None, reachable=False, error="host does not resolve")
    if not public:
        return HealthOutcome(status_code=None, reachable=False, error="host resolves to a non-public address")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(target, method="GET")  # noqa: S310 - scheme + address class guarded above
    try:
        with opener.open(request, timeout=timeout) as response:
            return HealthOutcome(status_code=response.status, reachable=True)
    except urllib.error.HTTPError as exc:
        # The server responded (reachable), just not with success (a refused redirect lands here too).
        return HealthOutcome(status_code=exc.code, reachable=True)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return HealthOutcome(status_code=None, reachable=False, error=exc.__class__.__name__)


ToolProbe = Callable[[str], bool]
CommandRunner = Callable[[Sequence[str], Path], CommandOutcome]
HealthProbe = Callable[[str, int], HealthOutcome]


# --- check executors ------------------------------------------------------------------


def _tool_results(bundle: BackendBundle, tool_probe: ToolProbe) -> list[DoctorCheckResult]:
    results: list[DoctorCheckResult] = []
    for tool in bundle.required_tools:
        if tool_probe(tool.name):
            results.append(
                DoctorCheckResult(f"tool:{tool.name}", CheckTier.LOCAL, CheckStatus.PASS, f"{tool.name} found on PATH")
            )
        else:
            results.append(
                DoctorCheckResult(
                    f"tool:{tool.name}",
                    CheckTier.LOCAL,
                    CheckStatus.FAIL,
                    f"{tool.name} not found on PATH",
                    blocking=True,
                    remediation=f"install {tool.name} — {tool.purpose}",
                )
            )
    return results


def _secret_results(bundle: BackendBundle, secrets: Mapping[str, Any]) -> DoctorCheckResult:
    issues = bundle.secret_reference_issues(secrets)
    if issues:
        summary = f"{len(issues)} secret-reference problem(s) for backend {bundle.name!r}: " + "; ".join(
            issue.render() for issue in issues
        )
        return DoctorCheckResult(
            "secret-references",
            CheckTier.LOCAL,
            CheckStatus.FAIL,
            summary,
            blocking=True,
            remediation="fix the secrets: block in shifter.yaml (references only — never secret values)",
        )
    count = len(bundle.required_secrets)
    return DoctorCheckResult(
        "secret-references",
        CheckTier.LOCAL,
        CheckStatus.PASS,
        f"{count} secret reference(s) present for backend {bundle.name!r}; "
        "values resolved from the secret store at deploy time",
    )


def _generated_env_result(bundle: BackendBundle) -> DoctorCheckResult:
    outputs = bundle.generated_outputs
    public = sum(1 for output in outputs if output.sensitivity.value == "public")
    secret_ref = sum(1 for output in outputs if output.sensitivity.value == "secret-reference")
    summary = (
        f"backend {bundle.name!r} generates {len(outputs)} runtime env output(s): "
        f"{public} public, {secret_ref} secret-reference (fetched from the secret store at startup)"
    )
    return DoctorCheckResult("generated-env", CheckTier.LOCAL, CheckStatus.INFO, summary)


def _declared_paths(bundle: BackendBundle) -> list[str]:
    owned = bundle.owned_files
    groups = (
        owned.infrastructure,
        owned.kubernetes,
        owned.scripts,
        owned.workflows,
        owned.examples,
        owned.docs,
        bundle.docs,
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for path in group:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return ordered


def _owned_path_results(bundle: BackendBundle, repo_root: Path) -> list[DoctorCheckResult]:
    declared = _declared_paths(bundle)
    missing = [path for path in declared if not (repo_root / path).exists()]
    if not missing:
        return [
            DoctorCheckResult(
                "owned-paths",
                CheckTier.LOCAL,
                CheckStatus.PASS,
                f"all {len(declared)} backend-owned path(s) present",
            )
        ]
    # A missing declared path is a bundle/repo-integrity signal, not an operator-config error,
    # so it warns without blocking the run.
    return [
        DoctorCheckResult(
            f"owned-path:{path}",
            CheckTier.LOCAL,
            CheckStatus.WARN,
            f"path {path!r} declared by backend {bundle.name!r} not found in the repository",
        )
        for path in missing
    ]


def _validation_check_results(
    bundle: BackendBundle, repo_root: Path, tool_probe: ToolProbe, command_runner: CommandRunner
) -> list[DoctorCheckResult]:
    results: list[DoctorCheckResult] = []
    for check in bundle.validation_checks:
        name = f"check:{check.name}"
        executable = check.command.argv[0]
        if not tool_probe(executable):
            results.append(
                DoctorCheckResult(
                    name,
                    CheckTier.LOCAL,
                    CheckStatus.SKIP,
                    f"skipped — required tool {executable!r} not installed",
                )
            )
            continue
        outcome = command_runner(check.command.argv, repo_root)
        results.append(_validation_outcome_result(name, check.description, check.blocking, check.command.argv, outcome))
    return results


def _validation_outcome_result(
    name: str, description: str, blocking: bool, argv: Sequence[str], outcome: CommandOutcome
) -> DoctorCheckResult:
    if outcome.returncode == 0:
        return DoctorCheckResult(name, CheckTier.LOCAL, CheckStatus.PASS, description)
    # Every non-success outcome (timeout, run error, or non-zero exit) fails a *blocking*
    # check and only warns a non-blocking one, so a FAIL result always implies a blocking
    # failure and the readiness contract (ok == no blocking failure) holds.
    if outcome.timed_out:
        detail = f"timed out after {_COMMAND_TIMEOUT_SECONDS}s"
    elif outcome.error is not None:
        detail = "could not run"
    else:
        detail = f"exited {outcome.returncode}"
    status = CheckStatus.FAIL if blocking else CheckStatus.WARN
    return DoctorCheckResult(
        name,
        CheckTier.LOCAL,
        status,
        f"{description} — {detail}",
        blocking=blocking,
        remediation=f"run `{' '.join(argv)}` from the repo root for details",
    )


def _health_results(
    bundle: BackendBundle, *, domain: str, scope: CheckScope, health_probe: HealthProbe
) -> list[DoctorCheckResult]:
    run_cloud = scope in (CheckScope.CLOUD, CheckScope.ALL)
    results: list[DoctorCheckResult] = []
    for check in bundle.health_checks:
        name = f"health:{check.name}"
        if not run_cloud:
            results.append(
                DoctorCheckResult(
                    name,
                    CheckTier.CLOUD_READ,
                    CheckStatus.SKIP,
                    f"{check.description} — cloud-read-only; re-run with --checks cloud to probe it",
                )
            )
            continue
        target = check.target.replace(_DOMAIN_PLACEHOLDER, domain)
        outcome = health_probe(target, check.timeout_seconds)
        results.append(_health_outcome_result(name, check.description, target, outcome))
    return results


def _health_outcome_result(name: str, description: str, target: str, outcome: HealthOutcome) -> DoctorCheckResult:
    if outcome.reachable and outcome.status_code is not None and _HEALTHY_MIN <= outcome.status_code <= _HEALTHY_MAX:
        return DoctorCheckResult(
            name, CheckTier.CLOUD_READ, CheckStatus.PASS, f"{description} — {target} responded {outcome.status_code}"
        )
    if outcome.reachable:
        return DoctorCheckResult(
            name,
            CheckTier.CLOUD_READ,
            CheckStatus.WARN,
            f"{description} — {target} responded HTTP {outcome.status_code}",
        )
    return DoctorCheckResult(
        name,
        CheckTier.CLOUD_READ,
        CheckStatus.WARN,
        f"{description} — {target} unreachable ({outcome.error or 'no response'}); expected before deploy",
    )


def _mutating_note() -> DoctorCheckResult:
    return DoctorCheckResult(
        "deployment-mutating",
        CheckTier.MUTATING,
        CheckStatus.INFO,
        "doctor runs no deployment-mutating steps (terraform apply/destroy, provider or GitHub secret writes, "
        "helm/kubectl apply, image promotion); run those through the deploy workflow after doctor passes",
    )


def check_backend(
    bundle: BackendBundle,
    *,
    domain: str,
    # ``profile`` is part of the stable check signature; reserved for profile-scoped checks.
    profile: str,
    secrets: Mapping[str, Any],
    scope: CheckScope,
    repo_root: Path,
    tool_probe: ToolProbe = _default_tool_probe,
    command_runner: CommandRunner = _default_command_runner,
    health_probe: HealthProbe = _default_health_probe,
) -> list[DoctorCheckResult]:
    """Run every declared check for ``bundle``, returning tier-labelled results.

    ``domain`` fills the health-check target's ``<deployment.domain>`` placeholder; ``secrets``
    is the root config's reference mapping (checked for presence/grammar, never resolved to a
    value). The execution seams default to the real implementations and are injected in tests.
    """
    results: list[DoctorCheckResult] = []
    results.extend(_tool_results(bundle, tool_probe))
    results.append(_secret_results(bundle, secrets))
    results.append(_generated_env_result(bundle))
    results.extend(_owned_path_results(bundle, repo_root))
    results.extend(_validation_check_results(bundle, repo_root, tool_probe, command_runner))
    results.extend(_health_results(bundle, domain=domain, scope=scope, health_probe=health_probe))
    results.append(_mutating_note())
    return results


def _config_failure_report(issues: Sequence[ConfigIssue]) -> DoctorReport:
    if not issues:
        results = [
            DoctorCheckResult(
                "root-config", CheckTier.LOCAL, CheckStatus.FAIL, "invalid root installation config", blocking=True
            )
        ]
    else:
        results = [
            DoctorCheckResult("root-config", CheckTier.LOCAL, CheckStatus.FAIL, issue.render(), blocking=True)
            for issue in issues
        ]
    return DoctorReport(backend=None, profile=None, results=results)


def run_doctor(
    config_path: str | Path,
    *,
    scope: CheckScope = CheckScope.LOCAL,
    repo_root: str | Path = Path("."),
    tool_probe: ToolProbe = _default_tool_probe,
    command_runner: CommandRunner = _default_command_runner,
    health_probe: HealthProbe = _default_health_probe,
) -> DoctorReport:
    """Validate the backend selected by ``config_path`` and return a tier-labelled report.

    The config is parsed by the single loader (:func:`load_root_config`); an invalid config
    is reported as blocking ``root-config`` failures and no backend checks run (there is no
    validated backend to check). A valid config resolves its bundle and runs every declared
    check via :func:`check_backend`.
    """
    root = Path(repo_root)
    try:
        config = load_root_config(config_path)
    except InstallationConfigError as exc:
        return _config_failure_report(exc.issues)

    bundle = get_backend_bundle(config.backend)
    if bundle is None:  # pragma: no cover - a validated backend always resolves to a bundle
        return DoctorReport(
            backend=config.backend,
            profile=config.deployment.profile,
            results=[
                DoctorCheckResult(
                    "root-config",
                    CheckTier.LOCAL,
                    CheckStatus.FAIL,
                    f"no backend bundle registered for {config.backend!r}",
                    blocking=True,
                )
            ],
        )

    results = [
        DoctorCheckResult(
            "root-config",
            CheckTier.LOCAL,
            CheckStatus.PASS,
            f"root config valid: backend {config.backend!r}, profile {config.deployment.profile!r}",
        )
    ]
    results.extend(
        check_backend(
            bundle,
            domain=config.deployment.domain,
            profile=config.deployment.profile,
            secrets=config.secrets,
            scope=scope,
            repo_root=root,
            tool_probe=tool_probe,
            command_runner=command_runner,
            health_probe=health_probe,
        )
    )
    return DoctorReport(backend=config.backend, profile=config.deployment.profile, results=results)
