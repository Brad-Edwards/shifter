"""ACES-native database readers for the Shifter Engine provisioner.

Split out of ``provisioner_db`` (Sonar S104). Owns the ACES-range and
image-registry reads used by the ``aces-range`` realization path (ADR-032).
"""

from __future__ import annotations

from typing import Any

from provisioner_db import get_db_connection


def get_aces_range_data_by_request_id(request_id: str) -> dict[str, Any]:
    """Read ACES-native range data: the serialized ACES plan + ids (ADR-032).

    Unlike :func:`get_range_data_by_request_id`, this does NOT run the cyberscript
    persisted-envelope unwrap or the NGFW attachment lookup: for the ACES-native
    path ``range_config`` is the serialized ACES ProvisioningPlan itself, returned
    verbatim as ``plan`` for the provisioner ``aces-range`` command to realize.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.request_id,
                rng.id AS range_id,
                rng.user_id,
                rng.range_config,
                rng.subnet_index,
                rng.status,
                rng.range_backend,
                rng.instantiation_purpose
            FROM engine_request r
            JOIN mission_control_range rng ON rng.request_id = r.id
            WHERE r.request_id = %s
            """,
            (request_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Range request not found: {request_id}")

    return {
        "request_id": str(row[0]),
        "range_id": row[1],
        "user_id": row[2],
        "plan": row[3] if row[3] else {},
        "subnet_index": row[4],
        "status": row[5],
        # #1666 write-once ownership binding (NULL for legacy/non-GCP rows).
        "range_backend": row[6],
        "instantiation_purpose": row[7],
    }


def get_aces_image_candidates(provider: str, source_name: str) -> list[dict[str, Any]]:
    """Return enabled ACES image mappings for (provider, source_name) (ADR-032-R2).

    The tenant-managed image registry the provisioner resolves against at
    realization. Returns the candidate rows for a source name; the pure resolver
    (``aces_image_resolver``) applies the exact-version / any-version rules.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_version, image_ref, machine_type, disk_size_gb, disk_type
            FROM engine_aces_image_mapping
            WHERE provider = %s AND source_name = %s AND enabled = TRUE
            """,
            (provider, source_name),
        )
        rows = cur.fetchall()

    return [
        {
            "source_version": row[0],
            "image_ref": row[1],
            "machine_type": row[2],
            "disk_size_gb": row[3],
            "disk_type": row[4],
        }
        for row in rows
    ]
