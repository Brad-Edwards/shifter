"""Keyed GCE image-profile resolution for the RAES-native path.

Split from ``raes_range_ops`` to keep that module within the file-size budget.
"""

from __future__ import annotations

from config import GCERangeCellConfig, GCERangeImageProfile


def _keyed_image_profile(config: GCERangeCellConfig, source_name: str | None) -> GCERangeImageProfile | None:
    """Return the tenant's keyed GCE image profile whose logical key is ``source_name``.

    A node whose authored ``source.name`` is a configured logical image key in
    ``GCP_RANGE_IMAGE_KEY_PROFILES_JSON`` (for example ``polaris-vm`` /
    ``polaris-dc``) realizes that exact profile -- crucially carrying its
    ``bootstrap_capability`` (e.g. ``polaris-docker-host``,
    ``prepromoted-domain-controller``), which the base-registry projection never
    sets. Logical keys are unique across profile classes, so the first match is
    unambiguous. Returns ``None`` when the source is not a keyed logical image.
    """
    if not source_name:
        return None
    for entries in config.image_key_profiles.values():
        profile = entries.get(source_name)
        if profile is not None:
            return profile
    return None
