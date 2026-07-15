"""Serializers for the canonical CTF DRF API.

Split by audience: participant-safe read projections (consumed by the
``/api/v1/ctf/me/*`` workspace) and, later, organizer projections. The
participant serializers are the typed contract over the dicts produced by
:mod:`ctf.api.projections`; they intentionally never declare flag, solution, or
validator-config fields so the generated OpenAPI schema (and therefore the SPA
types) cannot express those values.
"""

from __future__ import annotations

from rest_framework import serializers


class _NamedRefSerializer(serializers.Serializer):
    """A minimal ``{id, name}`` reference to a related entity."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class ParticipantSelfSerializer(serializers.Serializer):
    """The requesting participant's own state (never another participant's)."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    range_status = serializers.CharField(read_only=True, allow_blank=True)
    cached_score = serializers.IntegerField(read_only=True)
    cached_solve_count = serializers.IntegerField(read_only=True)
    team = _NamedRefSerializer(read_only=True, allow_null=True)
    bracket = _NamedRefSerializer(read_only=True, allow_null=True)


class ParticipantEventSerializer(serializers.Serializer):
    """Read-only participant-facing projection of the current CTF event.

    Organizer-only configuration (range config, reminder schedule, spare counts,
    email templates, cleanup policy) is deliberately omitted.
    """

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    status = serializers.CharField(read_only=True)
    team_mode = serializers.BooleanField(read_only=True)
    scoring_mode = serializers.CharField(read_only=True)
    rating_visibility = serializers.CharField(read_only=True)
    attempt_limit_mode = serializers.CharField(read_only=True)
    scoreboard_visible = serializers.BooleanField(read_only=True)
    event_start = serializers.DateTimeField(read_only=True, allow_null=True)
    event_end = serializers.DateTimeField(read_only=True, allow_null=True)


class ParticipantCurrentEventSerializer(serializers.Serializer):
    """The participant's current event plus their own participant state."""

    event = ParticipantEventSerializer(read_only=True)
    participant = ParticipantSelfSerializer(read_only=True)


class ParticipantChallengeListItemSerializer(serializers.Serializer):
    """A participant-safe browse entry for one available challenge.

    Carries only what the browse list renders. No flag hash, flag format,
    solution, or validator configuration is exposed.
    """

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.IntegerField(read_only=True)
    difficulty = serializers.CharField(read_only=True, allow_blank=True)
    order = serializers.IntegerField(read_only=True)
    solved = serializers.BooleanField(read_only=True)


class ParticipantTeamMemberSerializer(serializers.Serializer):
    """A teammate's display identity (no score or account fields)."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class ParticipantTeamSerializer(serializers.Serializer):
    """The participant's own team and its members."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    members = ParticipantTeamMemberSerializer(many=True, read_only=True)


class ParticipantHintSerializer(serializers.Serializer):
    """A progressive hint. ``text`` is populated only when unlocked."""

    id = serializers.CharField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    penalty = serializers.IntegerField(read_only=True)
    unlocked = serializers.BooleanField(read_only=True)
    text = serializers.CharField(read_only=True, allow_null=True)


class ParticipantChallengeFileSerializer(serializers.Serializer):
    """A participant-visible challenge attachment (metadata only)."""

    id = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True, allow_blank=True)
    size_bytes = serializers.IntegerField(read_only=True)
    content_type = serializers.CharField(read_only=True, allow_blank=True)


class ChallengeConnectionInfoSerializer(serializers.Serializer):
    """Target-instance connection info, surfaced only when the range is ready."""

    host = serializers.CharField(read_only=True)
    port = serializers.IntegerField(read_only=True, allow_null=True)
    instance_name = serializers.CharField(read_only=True)
    os_type = serializers.CharField(read_only=True, allow_blank=True)


class ChallengeRatingSerializer(serializers.Serializer):
    """Challenge rating projection (aggregate visible only when public)."""

    average = serializers.FloatField(read_only=True, allow_null=True)
    count = serializers.IntegerField(read_only=True)
    own_rating = serializers.IntegerField(read_only=True, allow_null=True)
    public = serializers.BooleanField(read_only=True)


class ParticipantChallengeDetailSerializer(serializers.Serializer):
    """Participant-safe challenge detail for the solve view.

    Never declares flag, flag-format, or validator-config fields. ``solution`` is
    non-null only after the event has ended/archived.
    """

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    category = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.IntegerField(read_only=True)
    difficulty = serializers.CharField(read_only=True, allow_blank=True)
    max_attempts = serializers.IntegerField(read_only=True)
    attempt_limit_mode = serializers.CharField(read_only=True)
    solved = serializers.BooleanField(read_only=True)
    attempt_count = serializers.IntegerField(read_only=True)
    attempts_remaining = serializers.IntegerField(read_only=True, allow_null=True)
    timeout_retry_after = serializers.IntegerField(read_only=True, allow_null=True)
    hints = ParticipantHintSerializer(many=True, read_only=True)
    next_hint_id = serializers.CharField(read_only=True, allow_null=True)
    next_hint_cost = serializers.IntegerField(read_only=True)
    points_after_next_hint = serializers.IntegerField(read_only=True)
    total_hint_penalty = serializers.IntegerField(read_only=True)
    files = ParticipantChallengeFileSerializer(many=True, read_only=True)
    prerequisites_met = serializers.BooleanField(read_only=True)
    unmet_prerequisites = _NamedRefSerializer(many=True, read_only=True)
    connection_info = ChallengeConnectionInfoSerializer(read_only=True, allow_null=True)
    show_solution = serializers.BooleanField(read_only=True)
    solution = serializers.CharField(read_only=True, allow_null=True)
    rating = ChallengeRatingSerializer(read_only=True, allow_null=True)
