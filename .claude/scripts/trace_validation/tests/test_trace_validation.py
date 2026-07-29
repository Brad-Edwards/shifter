"""Focused tests for the trace-validation package.

Integration-style: each test drives a real module API or the CLI boundary
against synthetic source written to a temporary directory. No mocks. Tests use
shared fixtures rather than one micro-test-with-inline-mock per assertion.

Coverage spans the behavior characterized before the split (extraction of
top-level functions vs. same-named methods, async functions, decorators,
annotations, defaults, calls, raises, missing files, syntax errors) and the
fail-closed strengthening (malformed blocks, missing selectors, empty claims,
missing batch files) that this refactor introduces.
"""

from __future__ import annotations

import json

import pytest

from trace_validation import (
    claim_has_recognized_field,
    extract_function_info,
    parse_validation_block,
    validate_claim,
    validate_trace_file,
)
from trace_validation import cli

SAMPLE_SOURCE = '''\
import os


def create_range(name: str, size: int = 3) -> RangeContext:
    """Make a range."""
    helper(name)
    obj.method_call()
    if not name:
        raise ValueError("empty name")
    return RangeContext()


async def fetch_data(url: str) -> dict:
    await do_thing()
    return {}


@decorator
@ns.deco2
def decorated(x, *args, **kwargs):
    return x


class Service:
    def create_range(self, name: str) -> None:
        inner()

    def process(self):
        def nested_fn():
            return 1

        return nested_fn()
'''


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(SAMPLE_SOURCE)
    return str(path)


# --- extraction ------------------------------------------------------------ #

def test_extract_top_level_function(sample):
    info = extract_function_info(sample, "create_range")
    assert info is not None
    assert info.name == "create_range"
    assert info.args == ["name", "size"]
    assert info.annotations == {"name": "str", "size": "int"}
    assert info.defaults == {"size": "3"}
    assert info.returns == "RangeContext"
    assert info.is_async is False
    assert info.is_method is False
    assert info.class_name is None
    call_names = {c["name"] for c in info.calls}
    assert {"helper", "method_call", "RangeContext"} <= call_names
    assert "ValueError" in info.raises


def test_method_selection_by_class(sample):
    info = extract_function_info(sample, "create_range", "Service")
    assert info is not None
    assert info.is_method is True
    assert info.class_name == "Service"
    assert info.args == ["self", "name"]
    assert info.returns == "None"
    assert {c["name"] for c in info.calls} == {"inner"}


def test_top_level_lookup_ignores_same_named_method(sample):
    # A top-level lookup must not return the Service.create_range method.
    info = extract_function_info(sample, "create_range")
    assert info is not None
    assert "self" not in info.args
    assert "size" in info.args


def test_extract_async_function(sample):
    info = extract_function_info(sample, "fetch_data")
    assert info is not None
    assert info.is_async is True
    assert info.returns == "dict"


def test_extract_decorators_and_varargs(sample):
    info = extract_function_info(sample, "decorated")
    assert info is not None
    assert info.decorators == ["decorator", "ns.deco2"]
    assert info.args == ["x", "*args", "**kwargs"]


def test_missing_file_returns_none():
    assert extract_function_info("/no/such/file.py", "whatever") is None


def test_syntax_error_returns_none(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    pass\n")
    assert extract_function_info(str(bad), "broken") is None


def test_unknown_function_returns_none(sample):
    assert extract_function_info(sample, "does_not_exist") is None


AMBIGUOUS_SOURCE = '''\
class Service:
    def only_method(self, x: int) -> None:
        pass


def wrapper():
    def only_nested(y):
        return y

    return only_nested
'''


@pytest.fixture
def ambiguous(tmp_path):
    path = tmp_path / "ambiguous.py"
    path.write_text(AMBIGUOUS_SOURCE)
    return str(path)


def test_top_level_lookup_does_not_return_a_method(ambiguous):
    # `only_method` exists only as a method; a top-level lookup must not return it.
    assert extract_function_info(ambiguous, "only_method") is None
    info = extract_function_info(ambiguous, "only_method", "Service")
    assert info is not None and info.is_method


def test_top_level_lookup_does_not_return_a_nested_function(ambiguous):
    # `only_nested` exists only as a nested function; not a top-level definition.
    assert extract_function_info(ambiguous, "only_nested") is None


# --- policy ---------------------------------------------------------------- #

def test_validate_claim_all_pass(sample):
    info = extract_function_info(sample, "create_range")
    results = validate_claim(
        info,
        {"returns": "RangeContext", "args": ["name", "size"], "raises": ["ValueError"]},
    )
    assert all(r.valid for r in results)


def test_validate_claim_reports_mismatch(sample):
    info = extract_function_info(sample, "create_range")
    results = validate_claim(info, {"returns": "WrongType"})
    assert results and not all(r.valid for r in results)
    assert results[0].field == "returns"


def test_validate_claim_calls_lookup(sample):
    info = extract_function_info(sample, "create_range")
    results = validate_claim(info, {"calls": ["helper", "does_not_exist"]})
    by_claim = {r.claimed: r.valid for r in results}
    assert by_claim["helper"] is True
    assert by_claim["does_not_exist"] is False


def test_empty_claim_is_failclosed(sample):
    info = extract_function_info(sample, "create_range")
    results = validate_claim(info, {})
    assert results, "empty claim must not yield an empty (vacuously passing) list"
    assert not all(r.valid for r in results)
    assert claim_has_recognized_field({}) is False
    assert claim_has_recognized_field({"returns": "X"}) is True


# --- block parsing / reporting --------------------------------------------- #

def _block(payload: str) -> str:
    return f"<!-- VALIDATION_BLOCK {payload} END_VALIDATION_BLOCK -->"


def test_parse_wellformed_block():
    content = _block('{"file": "a.py", "function": "f"}')
    blocks = parse_validation_block(content)
    assert blocks == [{"file": "a.py", "function": "f"}]


def test_parse_malformed_block_is_not_dropped():
    content = _block("{not valid json}")
    blocks = parse_validation_block(content)
    assert len(blocks) == 1
    assert "__parse_error__" in blocks[0]


@pytest.mark.parametrize(
    "payload",
    [
        '{"file": "a.py"',  # truncated object (no closing brace)
        "file: a.py",       # no braces at all
        "[1, 2, 3]",        # valid JSON but not an object
    ],
)
def test_parse_incomplete_or_non_object_block_is_invalid(payload):
    # These recognized-but-malformed blocks must be surfaced as invalid, not
    # dropped by a regex that only matched a complete {...} shape.
    blocks = parse_validation_block(_block(payload))
    assert len(blocks) == 1
    assert "__parse_error__" in blocks[0]


def test_batch_counts_truncated_block_as_failed(tmp_path):
    trace = tmp_path / "trace.md"
    trace.write_text(_block('{"file": "x.py", "function": "f"'))  # truncated
    report = validate_trace_file(str(trace))
    assert report.total_functions == 1
    assert report.failed == 1


def test_validate_trace_file_mixed(tmp_path, sample):
    trace = tmp_path / "trace.md"
    trace.write_text(
        "\n".join(
            [
                _block(json.dumps({"file": sample, "function": "create_range",
                                   "returns": "RangeContext"})),
                _block("{malformed}"),
                _block(json.dumps({"note": "no selectors here"})),
                _block(json.dumps({"file": sample, "function": "ghost"})),
            ]
        )
    )
    report = validate_trace_file(str(trace))
    assert report.total_functions == 4
    assert report.passed == 1
    assert report.not_found == 1
    assert report.failed == 2  # malformed block + missing-selector block
    assert report.validated == 2  # only blocks that carried file+function


def test_validate_trace_file_all_pass(tmp_path, sample):
    trace = tmp_path / "trace.md"
    trace.write_text(
        _block(json.dumps({"file": sample, "function": "create_range",
                           "returns": "RangeContext"}))
    )
    report = validate_trace_file(str(trace))
    assert (report.total_functions, report.passed, report.failed, report.not_found) == (1, 1, 0, 0)


# --- CLI boundary ---------------------------------------------------------- #

def test_cli_extract_ok(sample, capsys):
    rc = cli.main(["extract", sample, "create_range"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "create_range"


def test_cli_extract_not_found(sample, capsys):
    rc = cli.main(["extract", sample, "ghost"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {"error": "Function not found"}


def test_cli_validate_pass(sample, capsys):
    rc = cli.main(["validate", sample, "create_range", '{"returns": "RangeContext"}'])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_cli_validate_mismatch_keeps_exit_zero(sample, capsys):
    rc = cli.main(["validate", sample, "create_range", '{"returns": "Wrong"}'])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_cli_validate_empty_claim_exits_nonzero(sample, capsys):
    rc = cli.main(["validate", sample, "create_range", "{}"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_cli_validate_bad_json_exits_nonzero(sample, capsys):
    rc = cli.main(["validate", sample, "create_range", "not-json"])
    assert rc == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_cli_batch_missing_file_exits_nonzero(capsys):
    rc = cli.main(["batch", "/no/such/trace.md"])
    assert rc == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_cli_batch_failures_exit_nonzero(tmp_path, sample, capsys):
    trace = tmp_path / "trace.md"
    trace.write_text(_block(json.dumps({"file": sample, "function": "ghost"})))
    rc = cli.main(["batch", str(trace)])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["not_found"] == 1


def test_cli_batch_all_pass_exit_zero(tmp_path, sample, capsys):
    trace = tmp_path / "trace.md"
    trace.write_text(
        _block(json.dumps({"file": sample, "function": "create_range",
                           "returns": "RangeContext"}))
    )
    rc = cli.main(["batch", str(trace)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["passed"] == 1


def test_cli_no_args_exits_nonzero(capsys):
    assert cli.main([]) == 1
    assert "Usage" in capsys.readouterr().out


def test_cli_unknown_command_exits_nonzero(capsys):
    assert cli.main(["frobnicate"]) == 1
