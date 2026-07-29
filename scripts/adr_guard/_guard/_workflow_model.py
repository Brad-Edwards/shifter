"""Workflow-as-data model (_dw_*): safe YAML load, constrained expr eval, path filters."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


_CORE_WORKFLOW_PATH = ".github/workflows/_core.yml"
_RANGE_WORKFLOW_PATH = ".github/workflows/_range.yml"
_PLATFORM_WORKFLOW_PATH = ".github/workflows/_shifter-platform.yml"


# ===========================================================================
# Deploy control-plane model + checks (ADR-003)
#
# The single workflow-as-data model for the deploy pipeline: it reads
# deploy.yml and the reusable deploy workflows as YAML and evaluates their
# `if:` gates, branch/event routing, and change filters semantically. The
# ADR-003-R5 runner-exposure check below runs on it as a hard gate; the
# consolidated test suite (scripts/adr_guard/tests/test_deploy_workflow.py)
# exercises the same model for the #781 upstream-gating, #892 branch/event
# matrix, and #913 change-filter invariants. No cloud calls, no Actions
# execution - only literal event/branch strings ever reach the env script.
# ===========================================================================
_ENGINE_WORKFLOW_PATH = ".github/workflows/_shifter-engine.yml"
_GCP_DEV_WORKFLOW_PATH = ".github/workflows/_gcp-dev.yml"
_DW_REUSABLE_WORKFLOW_PATHS = (
    _CORE_WORKFLOW_PATH,
    _RANGE_WORKFLOW_PATH,
    _ENGINE_WORKFLOW_PATH,
    _PLATFORM_WORKFLOW_PATH,
    _GCP_DEV_WORKFLOW_PATH,
)
_DW_RESULT_REF = re.compile(r"needs\.([A-Za-z0-9_-]+)\.result")
_DW_EXPR_TOKEN = re.compile(
    r"""\s+
        |(?P<str>'[^']*')
        |(?P<op>==|!=|&&|\|\||!|\(|\))
        |(?P<ident>[A-Za-z0-9_.\-]+)""",
    re.VERBOSE,
)
# `if:` accepts a condition with or without the `${{ }}` wrapper; the workflows
# here use both styles, so the evaluator normalizes to the bare expression.
_DW_EXPR_WRAPPER = re.compile(r"^\$\{\{(?P<body>.*)\}\}$", re.DOTALL)
# The one repository whose runs are the canonical CI. Steps that must not
# silently no-op compare `github.repository` against this literal rather than
# testing whether their configuration variables happen to be set (ADR-003-R7).
_DW_CANONICAL_REPOSITORY = "Brad-Edwards/shifter"


class _DwShapeError(Exception):
    """A deploy workflow is missing a structurally-required key.

    Raised instead of returning a default so the model fails closed: an absent
    job, filter, ``needs``, or ``if`` block is an error, never a silent
    "not applicable".
    """


class _DwExprError(_DwShapeError):
    """An ``if:`` expression used a construct the constrained evaluator rejects."""


def _dw_load_workflow(repo_root: Path, rel: str) -> dict:
    """Load a workflow as a dict, normalizing the YAML 1.1 ``on:`` key.

    PyYAML resolves the bare word ``on`` to the Python boolean ``True``; map it
    back to the string ``"on"`` so callers can read triggers normally.
    """
    import yaml  # local import: keeps PyYAML optional for non-deploy checks

    path = repo_root / rel
    if not path.is_file():
        raise _DwShapeError(f"workflow not found: {rel}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise _DwShapeError(f"{rel}: top-level YAML is not a mapping")
    if True in data:  # bare `on:` parsed as boolean True under YAML 1.1
        data["on"] = data.pop(True)
    return data


def _dw_jobs(wf: dict, name: str = "<workflow>") -> dict:
    js = wf.get("jobs")
    if not isinstance(js, dict) or not js:
        raise _DwShapeError(f"{name}: missing or empty 'jobs' mapping")
    return js


def _dw_get_job(wf: dict, job_id: str, name: str = "<workflow>") -> dict:
    js = _dw_jobs(wf, name)
    if job_id not in js:
        raise _DwShapeError(f"{name}: job '{job_id}' not found")
    return js[job_id]


def _dw_normalize_expr(expr) -> str:
    """Collapse whitespace (incl. block-scalar newlines) to single spaces."""
    return " ".join(str(expr or "").split())


def _dw_unwrap_expr(expr: str) -> str:
    """Strip a single enclosing ``${{ }}`` so the tokenizer sees a bare
    expression. Anything else is returned unchanged."""
    match = _DW_EXPR_WRAPPER.match(expr)
    return match.group("body").strip() if match else expr


def _dw_job_if(job: dict) -> str:
    return _dw_normalize_expr(job.get("if", ""))


def _dw_runs_on(job: dict):
    return job.get("runs-on")


# Runner labels the ADR-003-R5 exposure check treats as self-hosted-class. A
# GCP-native runner (issue #1546) registers with `--no-default-labels` + a custom
# label, so a job selecting it never carries the literal `self-hosted` label;
# without this set the exposure check would skip that job and leave a
# pull_request-reachability blind spot when GCP-dev CI is cut over to its own
# runner. New self-hosted runner labels (e.g. a future gcp-prod, or a per-account
# AWS tenant label) MUST be added here so the gate cannot be bypassed.
_SELF_HOSTED_CLASS_LABELS = frozenset({"self-hosted", "gcp-dev"})


def _dw_is_self_hosted(job: dict) -> bool:
    ro = _dw_runs_on(job)
    if isinstance(ro, str):
        return ro in _SELF_HOSTED_CLASS_LABELS
    if isinstance(ro, (list, tuple)):
        return any(label in _SELF_HOSTED_CLASS_LABELS for label in ro)
    return False


def _dw_result_guarded_upstreams(if_expr) -> set:
    """Upstream job ids referenced as ``needs.<job>.result`` in an ``if:``."""
    return set(_DW_RESULT_REF.findall(_dw_normalize_expr(if_expr)))


# --- Constrained GitHub Actions `if:` expression evaluator ----------------- #
# A substring check cannot PROVE fail-closed gating: an expression that also
# ORs in `failure`/`cancelled` still contains the `success || skipped` text,
# and a correct gate written a different way would be wrongly rejected. So the
# model parses the `if:` and evaluates the denied scenarios (`failure`,
# `cancelled`, `pull_request`) over the finite result/event vocabulary, then
# asserts the job does not run. Supports only the operators these workflows
# use - `==`, `!=`, `&&`, `||`, `!`, parentheses, string literals, and the
# `always()` status function; operands are `needs.<job>.result`,
# `needs.<job>.outputs.<key>`, `inputs.<key>`, `vars.<name>`, and
# `github.<field>`.
def _dw_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _dw_loose_eq(left, right) -> bool:
    # GitHub Actions `==` compares strings case-insensitively.
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    return left == right


def _dw_call_function(name: str) -> bool:
    if name == "always":
        return True
    raise _DwExprError(f"unsupported function in if-expression: {name}()")


def _dw_tokenize(expr: str) -> list:
    tokens: list = []
    pos, end = 0, len(expr)
    while pos < end:
        match = _DW_EXPR_TOKEN.match(expr, pos)
        if not match or match.end() == pos:
            raise _DwExprError(f"cannot tokenize: {expr[pos : pos + 20]!r}")
        pos = match.end()
        kind = match.lastgroup
        if kind == "str":
            tokens.append(("str", match.group("str")[1:-1]))
        elif kind == "op":
            tokens.append(("op", match.group("op")))
        elif kind == "ident":
            tokens.append(("ident", match.group("ident")))
        # whitespace (no named group) is skipped
    tokens.append(("end", ""))
    return tokens


class _DwParser:
    """Recursive-descent evaluator: `!` > comparison > `&&` > `||`."""

    def __init__(self, tokens, resolve):
        self._toks = tokens
        self._i = 0
        self._resolve = resolve

    def _peek(self):
        return self._toks[self._i]

    def _advance(self):
        tok = self._toks[self._i]
        self._i += 1
        return tok

    def _expect(self, op):
        if self._advance() != ("op", op):
            raise _DwExprError(f"expected {op!r}")

    def evaluate(self):
        value = self._parse_or()
        if self._peek()[0] != "end":
            raise _DwExprError(f"trailing tokens: {self._toks[self._i :]!r}")
        return value

    def _parse_or(self):
        value = self._parse_and()
        while self._peek() == ("op", "||"):
            self._advance()
            value = _dw_truthy(value) | _dw_truthy(self._parse_and())
        return value

    def _parse_and(self):
        value = self._parse_not()
        while self._peek() == ("op", "&&"):
            self._advance()
            value = _dw_truthy(value) & _dw_truthy(self._parse_not())
        return value

    def _parse_not(self):
        if self._peek() == ("op", "!"):
            self._advance()
            return not _dw_truthy(self._parse_not())
        return self._parse_cmp()

    def _parse_cmp(self):
        left = self._parse_primary()
        token = self._peek()
        if token in (("op", "=="), ("op", "!=")):
            self._advance()
            equal = _dw_loose_eq(left, self._parse_primary())
            return equal if token == ("op", "==") else not equal
        return left

    def _parse_primary(self):
        token = self._advance()
        if token == ("op", "("):
            value = self._parse_or()
            self._expect(")")
            return value
        if token[0] == "str":
            return token[1]
        if token[0] == "ident":
            if self._peek() == ("op", "("):
                self._advance()
                self._expect(")")
                return _dw_call_function(token[1])
            return self._resolve(token[1])
        raise _DwExprError(f"unexpected token {token!r}")


def _dw_evaluate_if(
    if_expr,
    *,
    results=None,
    event_name="workflow_dispatch",
    ref="refs/heads/aws-dev",
    base_ref="",
    repository=_DW_CANONICAL_REPOSITORY,
    inputs_true=True,
    vars_set=True,
    fork_pr=False,
) -> bool:
    """Evaluate a job or step ``if:`` against a permissive context; return
    whether it would run. Unspecified upstream results default to ``success``,
    every ``needs.*.outputs.*`` to ``true``, every ``inputs.*`` to
    ``inputs_true``, and every ``vars.*`` to a non-empty value when
    ``vars_set`` - so the only thing that flips the outcome is the scenario
    under test (a failed upstream, a pull_request event, a fork's
    ``repository``, an unset repository variable)."""
    expr = _dw_unwrap_expr(_dw_normalize_expr(if_expr))
    if not expr:
        return True  # no `if:` at all is always eligible
    results = results or {}

    def resolve(path):
        parts = path.split(".")
        head = parts[0]
        if head == "needs" and len(parts) >= 3:
            job, field = parts[1], parts[2]
            if field == "result":
                return results.get(job, "success")
            if field == "outputs":
                return "true"
            return "success"
        if head == "inputs":
            return inputs_true
        if head == "vars":
            return "true" if vars_set else ""
        if path in ("true", "false"):
            return path == "true"
        if head == "github":
            # Fork-origin PRs run in the base repository's context, so
            # `github.repository` alone cannot distinguish them.
            if path == "github.event.pull_request.head.repo.fork":
                return fork_pr
            if path == "github.event.pull_request.head.repo.full_name":
                # The head repo of a same-repo PR IS this repository; a
                # fork-origin PR's head repo belongs to a different owner. This
                # is the identity GitHub uses to withhold secrets, and unlike
                # head.repo.fork it stays correct when the base repository is
                # itself a fork of an upstream (which makes head.repo.fork true
                # for every same-repo PR branch).
                return repository if not fork_pr else "fork-owner/" + repository.split("/", 1)[-1]
            field = parts[1] if len(parts) > 1 else ""
            return {
                "event_name": event_name,
                "ref": ref,
                "base_ref": base_ref,
                "repository": repository,
            }.get(field, "")
        raise _DwExprError(f"unresolvable operand: {path}")

    return _dw_truthy(_DwParser(_dw_tokenize(expr), resolve).evaluate())


def _dw_job_denied_when_upstream(if_expr, upstream, result) -> bool:
    """True iff the job does NOT run when ``upstream`` has ``result`` (every
    other condition permissive). Proves a failed/cancelled upstream blocks the
    deploy job (#781)."""
    return not _dw_evaluate_if(if_expr, results={upstream: result})


def _dw_job_denied_on_pull_request(if_expr) -> bool:
    """True iff the job does NOT run on a ``pull_request`` event (every other
    condition permissive). Proves PR events cannot reach the job (ADR-003-R5)."""
    return not _dw_evaluate_if(if_expr, event_name="pull_request")


def _dw_job_runs_when_eligible(if_expr) -> bool:
    """Sanity: the permissive context actually runs the job, so a denied-case
    assertion is meaningful and not vacuously satisfied."""
    return _dw_evaluate_if(if_expr)


def _dw_upstream_gating_violations(wf, deploy_job_ids):
    """Return ``[(job_id, upstream, result), ...]`` for deploy jobs that still
    RUN when a result-gated upstream is ``failure`` or ``cancelled`` (fail-open,
    the #781 class). Empty list means every deploy job fails closed."""
    found = []
    for jid in deploy_job_ids:
        expr = _dw_job_if(_dw_get_job(wf, jid, "deploy.yml"))
        for upstream in sorted(_dw_result_guarded_upstreams(expr)):
            for bad in ("failure", "cancelled"):
                if not _dw_job_denied_when_upstream(expr, upstream, bad):
                    found.append((jid, upstream, bad))
    return found


# --- dorny/paths-filter change-filter coverage (#913 / R-A2) --------------- #
def _dw_parse_paths_filter(wf, job_id, step_id, name="deploy.yml") -> dict:
    """Return ``{filter_name: [patterns]}`` from a dorny/paths-filter step.

    The action's ``filters`` input is itself a YAML document (a block scalar in
    the workflow), so it is parsed a second time here."""
    import yaml

    job = _dw_get_job(wf, job_id, name)
    for step in job.get("steps", []) or []:
        if step.get("id") == step_id:
            raw = (step.get("with") or {}).get("filters")
            if not isinstance(raw, str):
                raise _DwShapeError(f"{name}:{step_id} has no string 'filters' input")
            parsed = yaml.safe_load(raw)
            if not isinstance(parsed, dict) or not parsed:
                raise _DwShapeError(f"{name}:{step_id} filters not a mapping")
            return {key: list(val) for key, val in parsed.items()}
    raise _DwShapeError(f"{name}:{job_id} has no step with id '{step_id}'")


def _dw_glob_to_regex(pattern: str) -> str:
    """Translate a micromatch-style glob to an anchored regex for the features
    the deploy filters use: ``**`` (any depth, incl. a trailing ``/`` matching
    zero or more directories), ``*`` (one path segment), and literal text."""
    i, n = 0, len(pattern)
    out = ["^"]
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                j = i + 2
                if pattern[j : j + 1] == "/":
                    out.append("(?:.*/)?")  # `**/` => zero or more directories
                    i = j + 1
                else:
                    out.append(".*")
                    i = j
            else:
                out.append("[^/]*")
                i += 1
        else:
            out.append(re.escape(char))
            i += 1
    out.append("$")
    return "".join(out)


def _dw_path_matches_any(path: str, patterns) -> bool:
    """True iff ``path`` matches any positive pattern. The deploy filters use no
    ``!`` negation and the default ``some`` quantifier, so positive-pattern
    membership is the full contract for them."""
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        if re.match(_dw_glob_to_regex(pattern), path):
            return True
    return False


# --- branch/event routing (#892) ------------------------------------------- #
def _dw_extract_set_environment_script(wf, name="deploy.yml") -> str:
    """Return the ``run`` body of the ``changes`` job's ``Set environment`` step."""
    job = _dw_get_job(wf, "changes", name)
    for step in job.get("steps", []) or []:
        if step.get("id") == "env" or step.get("name") == "Set environment":
            run = step.get("run")
            if not isinstance(run, str):
                raise _DwShapeError(f"{name}: 'Set environment' step has no run script")
            return run
    raise _DwShapeError(f"{name}: no 'Set environment' step in 'changes' job")


def _dw_evaluate_env(script, event_name, ref="", base_ref="") -> dict:
    """Execute the workflow's own ``Set environment`` bash and return its
    ``GITHUB_OUTPUT`` key/value pairs. Only literal event/branch strings reach
    bash - no secrets, no shell trace - matching GitHub's default
    ``bash -e -o pipefail`` shell."""
    import tempfile

    rendered = script.replace("${{ github.event_name }}", event_name).replace(
        "${{ github.base_ref }}", base_ref
    )
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "github_output")
        Path(out_path).touch()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "GITHUB_REF": ref,
            "GITHUB_OUTPUT": out_path,
        }
        proc = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", rendered],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise _DwShapeError(
                f"Set environment script exited {proc.returncode}: {proc.stderr.strip()}"
            )
        outputs = {}
        for line in Path(out_path).read_text().splitlines():
            line = line.strip()
            if "=" in line:
                key, val = line.split("=", 1)
                outputs[key] = val
    return outputs
