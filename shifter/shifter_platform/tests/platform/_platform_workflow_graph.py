"""Resolve the AWS platform workflow by its reusable-workflow call graph (#689).

The platform CI decomposed ``_shifter-platform.yml`` into per-operation reusable
children. Contract tests must follow the actual ``jobs.<id>.uses`` call graph
from the coordinator rather than concatenating every ``_platform-*.yml`` file:
a disconnected or obsolete child could otherwise keep the expected deploy
script / smoke command / worker-health args and pass a test while the executing
workflow has lost them. Resolution is structural (parsed YAML, job-level
``uses`` only), so a ``uses:`` line inside a ``run:`` scalar is not a call edge.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
PLATFORM_COORDINATOR = ".github/workflows/_shifter-platform.yml"
_LOCAL_PREFIX = "./.github/workflows/"


def _local_target(uses: str) -> str | None:
    ref = uses.strip()
    if not ref.startswith(_LOCAL_PREFIX) or "@" in ref:
        return None
    name = ref[len(_LOCAL_PREFIX) :]
    if not name or "/" in name or ".." in name or not name.endswith((".yml", ".yaml")):
        return None
    return f".github/workflows/{name}"


def reachable_workflow_texts(root_rel: str = PLATFORM_COORDINATOR) -> dict[str, str]:
    """``{repo_rel: text}`` for the coordinator and every reachable child, in
    graph order (the coordinator first)."""
    texts: dict[str, str] = {}

    def walk(rel: str) -> None:
        if rel in texts:
            return
        path = REPO_ROOT / rel
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        texts[rel] = text
        data = yaml.safe_load(text)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if isinstance(jobs, dict):
            for job in jobs.values():
                uses = job.get("uses") if isinstance(job, dict) else None
                target = _local_target(uses) if isinstance(uses, str) else None
                if target:
                    walk(target)

    walk(root_rel)
    return texts


def reachable_family_text(root_rel: str = PLATFORM_COORDINATOR) -> str:
    """Coordinator + every reachable child concatenated (graph order)."""
    return "\n".join(reachable_workflow_texts(root_rel).values())


def reachable_child_text_containing(marker: str, root_rel: str = PLATFORM_COORDINATOR) -> str:
    """Text of the single reachable workflow that contains ``marker`` (else '').

    Lets a test scope an ordering/behaviour assertion to the reachable child that
    actually owns the step, instead of a filename-ordered concatenation.
    """
    for text in reachable_workflow_texts(root_rel).values():
        if marker in text:
            return text
    return ""
