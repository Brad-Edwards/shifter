"""Capacity assessment and reservation models (PLAT-201, #680).

:class:`CapacityDeclaration` records *intent* -- how big an event expects to be.
These two record what the Engine did with that intent:

``CapacityAssessment`` is an immutable snapshot of one admission decision,
pinned to the partition, the policy version, and the time the underlying
readings were taken. It is append-only history: a re-assessment writes a new row
rather than mutating the old one, so a later retry, destroy, or reconciliation
reads the decision that was actually made rather than re-deriving it against
configuration that has since drifted.

``CapacityReservation`` is the durable commitment that makes overlapping events
account for each other. Provider quotas cannot be reserved server-side, so this
table is the only place Shifter knows that another event has already promised to
consume capacity in the same partition during an overlapping window.
"""

from __future__ import annotations

from django.db import models


class CapacityAssessment(models.Model):
    """One immutable capacity-admission decision for one event."""

    event_ref = models.UUIDField(
        db_index=True,
        help_text="Upstream event identifier (opaque to the engine; no FK by design)",
    )
    partition_name = models.CharField(
        max_length=100,
        help_text="Deployment-declared target partition this decision was made against",
    )
    policy_version = models.CharField(
        max_length=64,
        help_text="Fingerprint of the capacity catalog that produced the decision",
    )
    outcome = models.CharField(
        max_length=20,
        help_text="admitted | warning | indeterminate | rejected",
    )
    verdicts = models.JSONField(
        default=list,
        blank=True,
        help_text="Per-metric bounded reason codes; never raw provider limits or usage figures",
    )
    observed_at = models.DateTimeField(
        help_text="When the underlying provider readings were taken (not when we asked)",
    )
    assessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table metadata."""

        db_table = "engine_capacity_assessment"
        ordering = ["-assessed_at"]
        indexes = [
            models.Index(fields=["event_ref", "-assessed_at"]),
            models.Index(fields=["partition_name", "-assessed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_ref}@{self.partition_name} -> {self.outcome} ({self.assessed_at})"


class CapacityReservation(models.Model):
    """Capacity one event has committed to consume in a partition and window.

    Open rows (``released_at`` null) whose window overlaps a candidate event's
    window are subtracted from observed headroom, so two events planned into the
    same account cannot each be told the whole quota is free.
    """

    assessment = models.ForeignKey(
        CapacityAssessment,
        on_delete=models.CASCADE,
        related_name="reservations",
        null=True,
        blank=True,
        help_text="Assessment that created this reservation",
    )
    event_ref = models.UUIDField(
        db_index=True,
        help_text="Upstream event identifier holding the reservation",
    )
    request_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Provisioning request correlation id, when the reservation is request-scoped",
    )
    partition_name = models.CharField(max_length=100, help_text="Partition the capacity is held in")
    metric_name = models.CharField(max_length=100, help_text="Catalog metric the capacity is held against")
    amount = models.FloatField(help_text="Amount of the metric's unit committed")
    window_start = models.DateTimeField(help_text="Start of the window this reservation occupies")
    window_end = models.DateTimeField(help_text="End of the window this reservation occupies")
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the reservation is released; released rows no longer consume headroom",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table metadata."""

        db_table = "engine_capacity_reservation"
        ordering = ["-created_at"]
        indexes = [
            # The overlap query filters on exactly this shape.
            models.Index(fields=["partition_name", "metric_name", "released_at"]),
            models.Index(fields=["event_ref", "released_at"]),
        ]

    def __str__(self) -> str:
        state = "released" if self.released_at else "open"
        return f"{self.metric_name}={self.amount} @ {self.partition_name} ({state})"
