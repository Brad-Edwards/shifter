"""Fresh foundation ordering and fail-closed target binding."""

import json
import subprocess
import tempfile

import pytest

from bootstrap_core import set_assume_yes
from gcp_foundation import bootstrap_gcp_foundation


@pytest.fixture
def inputs(tmp_path):
    path = tmp_path / "foundation.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "example-project",
                "project_number": "123",
                "environment": "example",
                "name_prefix": "shifter-example",
                "github_org": "example",
                "github_repo": "shifter",
                "github_repository_id": "456",
                "github_owner_id": "789",
                "purpose_contexts": {},
                "terraform_state_bucket_name": "example-project-state",
                "release_evidence_bucket_name": "example-project-evidence",
            }
        )
    )
    return path


def test_new_foundation_creates_backend_before_saved_plan_apply(inputs, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gcloud", "projects", "describe"]:
            return subprocess.CompletedProcess(argv, 0, "123\n")
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, '{"id":456,"owner":{"id":789}}')
        if argv[:4] == ["gcloud", "storage", "buckets", "describe"]:
            return subprocess.CompletedProcess(argv, 1, "")
        return subprocess.CompletedProcess(argv, 0, "")

    monkeypatch.setattr(subprocess, "run", run)
    set_assume_yes(True)
    try:
        bootstrap_gcp_foundation(str(inputs))
    finally:
        set_assume_yes(False)
    create = next(i for i, c in enumerate(calls) if c[:4] == ["gcloud", "storage", "buckets", "create"])
    init = next(i for i, c in enumerate(calls) if c[0] == "terraform" and c[2] == "init")
    assert create < init
    assert calls[-2][2:5] == ["plan", "-input=false", f"-var-file={inputs}"]
    plan = calls[-2][-1].removeprefix("-out=")
    assert plan.startswith(tempfile.gettempdir())
    assert calls[-1][2:] == ["apply", "-input=false", plan]


def test_wrong_project_number_stops_before_mutation(inputs, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "999\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="project number"):
        bootstrap_gcp_foundation(str(inputs))
    assert len(calls) == 1


def test_incomplete_inputs_fail_before_commands(tmp_path, monkeypatch):
    path = tmp_path / "incomplete.json"
    path.write_text("{}")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="Missing foundation inputs"):
        bootstrap_gcp_foundation(str(path))
    assert calls == []


def test_dry_run_executes_no_commands(inputs, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    bootstrap_gcp_foundation(str(inputs), dry_run=True)
    assert calls == []
