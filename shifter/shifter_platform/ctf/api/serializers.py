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
