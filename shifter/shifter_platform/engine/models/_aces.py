"""AcesImageMapping model."""

from django.db import models


class AcesImageMapping(models.Model):
    """Tenant-managed mapping from an authored ACES image identity to a concrete provider image.

    The ADR-032-R2 realization seam. An ACES scenario names an image via its
    ``source`` (name + optional version); a tenant operator maps that authored
    identity to a concrete provider image (and optional sizing defaults) here, on
    the running tenant. This is deliberately data, not code/config: new images are
    added to a deployed tenant at runtime and survive redeploys (the deployment
    model is updatable tenants, not repo-based config). The provisioner resolves
    against these rows at realization; the platform only manages them.

    A blank ``source_version`` is the any-version fallback for a ``source_name``.
    Retire a mapping with ``enabled=False`` -- which preserves audit history and
    makes realization fail loud -- rather than deleting it.
    """

    class Provider(models.TextChoices):
        """Cloud provider a mapping targets (ACES-scoped; extensible)."""

        GCE = "gce", "Google Compute Engine"
        AWS = "aws", "AWS EC2"

    provider = models.CharField(max_length=16, choices=Provider.choices)
    source_name = models.CharField(max_length=200, help_text="Authored ACES image source name (for example 'kali').")
    source_version = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Authored source version; blank matches any version for this source_name.",
    )
    image_ref = models.CharField(
        max_length=500,
        help_text="Concrete provider image (GCE source_image / family URL, AWS AMI id, ...).",
    )
    machine_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional provider machine type; blank lets the backend size from resources/defaults.",
    )
    disk_size_gb = models.PositiveIntegerField(
        null=True, blank=True, help_text="Optional boot disk size (GB); blank uses the backend default."
    )
    disk_type = models.CharField(
        max_length=100, blank=True, default="", help_text="Optional provider disk type; blank uses the backend default."
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Disabled mappings do not resolve (realization fails loud); use instead of deleting to keep audit.",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Table + uniqueness (one mapping per provider/source_name/source_version)."""

        db_table = "engine_aces_image_mapping"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "source_name", "source_version"],
                name="unique_aces_image_mapping",
            ),
        ]

    def __str__(self) -> str:
        version = self.source_version or "*"
        return f"{self.provider}:{self.source_name}@{version} -> {self.image_ref}"
