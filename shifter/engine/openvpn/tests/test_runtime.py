"""The server's metadata identity, Secret Manager reads, health endpoint and startup wait (#2480)."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
import urllib.request

import pytest

from shifter_openvpn import __main__ as entry
from shifter_openvpn import gcp


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def network(monkeypatch):
    """Replace only urllib's network call; record every request."""
    seen: list[urllib.request.Request] = []

    def urlopen(request, timeout):
        seen.append(request)
        url = request.full_url
        if url.endswith("/token"):
            return _Response(json.dumps({"access_token": "ya29.test"}).encode())
        if "/identity?" in url:
            return _Response(b"eyJ.identity.token\n")
        if url.startswith("https://secretmanager.googleapis.com/"):
            return _Response(json.dumps({"payload": {"data": base64.b64encode(b"material").decode()}}).encode())
        raise AssertionError(url)

    monkeypatch.setattr(gcp.urllib.request, "urlopen", urlopen)
    return seen


def test_identity_tokens_are_full_format_for_the_audience(network):
    assert gcp.identity_token("https://portal.example.com/vpn-control") == "eyJ.identity.token"
    url = network[0].full_url
    assert "audience=https%3A%2F%2Fportal.example.com%2Fvpn-control" in url and "format=full" in url
    assert network[0].get_header("Metadata-flavor") == "Google"


def test_secrets_are_read_with_the_attached_identity(network):
    assert gcp.read_secret("projects/p/secrets/server") == "material"
    access = network[-1]
    assert access.full_url == "https://secretmanager.googleapis.com/v1/projects/p/secrets/server/versions/latest:access"
    assert access.get_header("Authorization") == "Bearer ya29.test"


def test_the_health_endpoint_reflects_readiness(monkeypatch):
    monkeypatch.setattr(entry.socket, "gethostbyname", lambda _name: "127.0.0.1")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    healthy = threading.Event()
    threading.Thread(target=entry._serve_health, args=(port, healthy), daemon=True).start()

    def status(path: str = "/healthz") -> int:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
            except OSError:
                time.sleep(0.05)
        raise AssertionError("health server did not start")

    assert status() == 503
    healthy.set()
    assert status() == 200
    assert status("/other") == 503


class _Process:
    def __init__(self, exited: bool) -> None:
        self.exited = exited

    def poll(self):
        return 1 if self.exited else None


def test_startup_waits_for_the_management_socket(tmp_path):
    path = tmp_path / "management.sock"
    threading.Timer(0.3, path.touch).start()
    entry._wait_for_socket(str(path), _Process(exited=False), timeout=5)

    with pytest.raises(RuntimeError, match="management socket"):
        entry._wait_for_socket(str(tmp_path / "never"), _Process(exited=True), timeout=5)
