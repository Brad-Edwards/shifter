"""Provider-neutral resolver tests for digest-pinned CTF content."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.test import override_settings

from ctf.exceptions import CTFValidationError
from ctf.services.content_resolution import resolve_scenario_ctf_content
from shared.schemas.ctf_content_reference import load_ctf_content_references_json


def _raw_bundle() -> bytes:
    return json.dumps(
        {
            "contract": "shifter-ctf-content/v1",
            "scenario_id": "scenario-one",
            "challenges": [
                {
                    "id": "challenge-one",
                    "name": "Challenge One",
                    "description": "Inspect the evidence.",
                    "category": "Module 1",
                    "points": 100,
                    "difficulty": "easy",
                    "order": 1,
                    "flags": [{"type": "static", "value": "TEST{not-a-secret}", "order": 0}],
                    "hints": [],
                    "prerequisites": [],
                }
            ],
        }
    ).encode()


def _references(raw: bytes, *, digest: str | None = None):
    expected = digest or f"sha256:{hashlib.sha256(raw).hexdigest()}"
    return load_ctf_content_references_json(
        json.dumps(
            {
                "contract": "shifter-ctf-content-references/v1",
                "references": [
                    {
                        "scenario_id": "scenario-one",
                        "object_key": "ctf/content-bundles/aa/bundle.json",
                        "digest": expected,
                    }
                ],
            }
        ),
        prefix="ctf/content-bundles",
    )


def _storage(raw: bytes) -> Mock:
    storage = Mock()
    storage.head_object.return_value = {"content_length": len(raw), "etag": "etag-one", "generation": "7"}

    def download(_bucket, _key, destination, **_kwargs):
        Path(destination).write_bytes(raw)
        return {"content_length": len(raw), "etag": "etag-one", "generation": "7"}

    storage.download_object.side_effect = download
    return storage


@pytest.mark.django_db
def test_resolver_binds_identity_digest_and_bundle(monkeypatch) -> None:
    raw = _raw_bundle()
    storage = _storage(raw)
    monkeypatch.setattr("shared.cloud.get_object_storage", lambda: storage)
    with override_settings(
        CTF_CONTENT_BUCKET="private-content",
        CTF_CONTENT_MAX_BYTES=1024 * 1024,
        CTF_CONTENT_REFERENCES=_references(raw),
    ):
        resolved = resolve_scenario_ctf_content("scenario-one")

    assert resolved.bundle.challenges[0].source_id == "challenge-one"
    assert resolved.evidence.declared_digest.startswith("sha256:")
    storage.download_object.assert_called_once()
    assert storage.download_object.call_args.kwargs["expected_identity"]["generation"] == "7"


@pytest.mark.django_db
def test_digest_mismatch_fails_before_parse(monkeypatch) -> None:
    raw = _raw_bundle()
    monkeypatch.setattr("shared.cloud.get_object_storage", lambda: _storage(raw))
    with override_settings(
        CTF_CONTENT_BUCKET="private-content",
        CTF_CONTENT_MAX_BYTES=1024 * 1024,
        CTF_CONTENT_REFERENCES=_references(raw, digest=f"sha256:{'0' * 64}"),
    ), pytest.raises(CTFValidationError) as error:
        resolve_scenario_ctf_content("scenario-one")
    assert error.value.code == "CTF_CONTENT_DIGEST_MISMATCH"


def test_unconfigured_scenario_performs_no_storage_call(monkeypatch) -> None:
    storage = Mock()
    monkeypatch.setattr("shared.cloud.get_object_storage", lambda: storage)
    with override_settings(
        CTF_CONTENT_REFERENCES=load_ctf_content_references_json(
            "",
            prefix="ctf/content-bundles",
        )
    ):
        assert resolve_scenario_ctf_content("scenario-one") is None
    storage.head_object.assert_not_called()
