"""Guest and per-range credential operation bindings for GCE range cells."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from config import GCERangeCellConfig
from gcp_guest_secrets import (
    delete_participant_ssh_secret,
    delete_rdp_password_secret,
    delete_ssh_secret,
    ensure_participant_ssh_secret,
    ensure_rdp_password_secret,
    ensure_ssh_secret,
)
from gcp_range_cell_types import ScenarioInstance
from gcp_range_vertex_creds import delete_range_vertex_key, ensure_range_vertex_key


@dataclass(frozen=True)
class GCEGuestSecretOps:
    """Guest credential operations used by the GCE range-cell backend."""

    ensure_ssh: Callable[[int, ScenarioInstance], tuple[str, str]]
    ensure_participant_ssh: Callable[[int, ScenarioInstance], tuple[str, str]]
    ensure_rdp_password: Callable[[int, ScenarioInstance], tuple[str, str]]
    delete_ssh: Callable[[int, ScenarioInstance], None]
    delete_participant_ssh: Callable[[int, ScenarioInstance], None]
    delete_rdp_password: Callable[[int, ScenarioInstance], None]


def _default_secret_ops() -> GCEGuestSecretOps:
    """Return the production guest-secret operation bindings."""
    return GCEGuestSecretOps(
        ensure_ssh=ensure_ssh_secret,
        ensure_participant_ssh=ensure_participant_ssh_secret,
        ensure_rdp_password=ensure_rdp_password_secret,
        delete_ssh=delete_ssh_secret,
        delete_participant_ssh=delete_participant_ssh_secret,
        delete_rdp_password=delete_rdp_password_secret,
    )


@dataclass(frozen=True)
class GCEVertexCredentialOps:
    """Per-range Vertex agent-credential operations used by the GCE backend.

    ``ensure``/``delete`` take the range project id so the SA key and Secret
    Manager secret are managed in the range project, not the control-plane
    project (which may be a deploy-overlay placeholder).
    """

    ensure: Callable[[int, str, str, str], str]
    delete: Callable[[int, str], None]


def mint_range_vertex_key(
    range_id: int, project_id: str, config: GCERangeCellConfig, vertex_ops: GCEVertexCredentialOps
) -> str | None:
    """Mint the per-range Vertex agent SA key when the tenant configures a Vertex SA.

    Stores the key in Secret Manager and grants the attached range-cell host SA
    (config.service_account_email) secretAccessor via ``vertex_ops.ensure`` so the
    polaris host can inject it into a14-kali. Returns the secret ref, or None when
    no Vertex SA is configured (ranges without the agent are unaffected). Shared by
    the RAES-native and legacy scenario provision paths.
    """
    if not config.vertex_service_account_email:
        return None
    return vertex_ops.ensure(range_id, config.vertex_service_account_email, project_id, config.service_account_email)


def _default_vertex_ops() -> GCEVertexCredentialOps:
    """Return the production per-range Vertex credential bindings."""
    return GCEVertexCredentialOps(
        ensure=lambda range_id, sa_email, project_id, host_sa_email: ensure_range_vertex_key(
            range_id, sa_email, project_id=project_id, host_service_account_email=host_sa_email
        ),
        delete=lambda range_id, project_id: delete_range_vertex_key(range_id, project_id=project_id),
    )


__all__ = [
    "GCEGuestSecretOps",
    "GCEVertexCredentialOps",
    "_default_secret_ops",
    "_default_vertex_ops",
]
