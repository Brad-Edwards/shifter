"""Provisioner-side extraction of ACES composition placements (ADR-031, ADR-032).

Split out of ``aces_plan`` (Sonar file-size): the value objects and pure accessors
for ``content-placement`` / ``feature-binding`` / ``account-placement`` resources
in the serialized ACES plan. Pure stdlib (no ``aces_*``, no Pydantic), mirroring
the compiler payload shapes; the realizer (``aces_gcp_composition``) turns these
into GCE guest bootstrap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AcesPlanContent:
    """A content placement (file/dataset/directory) targeting one node.

    ``text`` is inline file content (realized as a real file). Non-inline content
    (a ``source`` package, or dataset ``items``) is supplied by the baked image /
    guest repo at ``path``/``destination`` (ADR-032 baked-image delivery), so the
    realizer creates the structural target but does not fetch bytes.
    """

    name: str
    content_type: str
    target_address: str
    path: str | None = None
    destination: str | None = None
    text: str | None = None
    source_name: str | None = None
    file_format: str | None = None
    items: tuple[str, ...] = ()
    sensitive: bool = False


@dataclass(frozen=True)
class AcesPlanAccount:
    """A user account placement targeting one node."""

    username: str
    target_address: str
    groups: tuple[str, ...] = ()
    login_shell: str | None = None
    home: str | None = None
    mail: str | None = None
    spn: str | None = None
    auth_method: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class AcesPlanFeature:
    """A feature binding (service/artifact/configuration) targeting one node.

    A ``service`` feature realizes as an install+enable step whose package/artifact
    is provided by the baked image or the guest package repo (ADR-032); the backend
    does not fetch it.
    """

    name: str
    feature_type: str
    target_address: str
    source_name: str | None = None
    destination: str | None = None


def _mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _opt_str(value: object) -> str | None:
    """Return a stripped non-empty string, or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_tuple(value: object) -> tuple[str, ...]:
    """Return the non-empty strings in a list/tuple value as a tuple."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _source_name(spec: Mapping[str, Any]) -> str | None:
    """Return a ``source`` package name from a spec (string shorthand or {name})."""
    source = spec.get("source")
    if isinstance(source, str):
        return _opt_str(source)
    if isinstance(source, Mapping):
        return _opt_str(source.get("name"))
    return None


def _content_item_names(raw: object) -> tuple[str, ...]:
    """Return the ``name`` of each dataset item in a content ``items`` list."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(name for entry in raw if isinstance(entry, Mapping) and (name := _opt_str(entry.get("name"))))


def build_content(payload: Mapping[str, Any]) -> AcesPlanContent | None:
    """Build an AcesPlanContent from a content-placement payload (None if malformed)."""
    spec = _mapping(payload.get("spec"))
    content_type = _opt_str(spec.get("type"))
    target = _opt_str(payload.get("target_address")) or _opt_str(payload.get("target_node"))
    if content_type is None or target is None:
        return None
    text = spec.get("text")
    return AcesPlanContent(
        name=_opt_str(payload.get("content_name")) or _opt_str(payload.get("name")) or content_type.lower(),
        content_type=content_type.lower(),
        target_address=target,
        path=_opt_str(spec.get("path")),
        destination=_opt_str(spec.get("destination")),
        text=text if isinstance(text, str) else None,
        source_name=_source_name(spec),
        file_format=_opt_str(spec.get("format")),
        items=_content_item_names(spec.get("items")),
        sensitive=spec.get("sensitive") is True,
    )


def build_account(payload: Mapping[str, Any]) -> AcesPlanAccount | None:
    """Build an AcesPlanAccount from an account-placement payload (None if malformed)."""
    spec = _mapping(payload.get("spec"))
    username = _opt_str(spec.get("username")) or _opt_str(payload.get("account_name"))
    target = _opt_str(payload.get("target_address")) or _opt_str(payload.get("node_name"))
    if username is None or target is None:
        return None
    return AcesPlanAccount(
        username=username,
        target_address=target,
        groups=_str_tuple(spec.get("groups")),
        login_shell=_opt_str(spec.get("shell")),
        home=_opt_str(spec.get("home")),
        mail=_opt_str(spec.get("mail")),
        spn=_opt_str(spec.get("spn")),
        auth_method=_opt_str(spec.get("auth_method")),
        disabled=spec.get("disabled") is True,
    )


def build_feature(payload: Mapping[str, Any]) -> AcesPlanFeature | None:
    """Build an AcesPlanFeature from a feature-binding payload (None if malformed)."""
    template = _mapping(_mapping(payload.get("spec")).get("template"))
    feature_type = _opt_str(template.get("type"))
    target = _opt_str(payload.get("node_address")) or _opt_str(payload.get("node_name"))
    name = _opt_str(payload.get("feature_name")) or _opt_str(template.get("name"))
    if feature_type is None or target is None or name is None:
        return None
    return AcesPlanFeature(
        name=name,
        feature_type=feature_type.lower(),
        target_address=target,
        source_name=_source_name(template),
        destination=_opt_str(template.get("destination")),
    )
