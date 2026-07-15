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


# ---------------------------------------------------------------------------
# Organizer serializers (event management)
# ---------------------------------------------------------------------------


class EventSummarySerializer(serializers.Serializer):
    """List projection of one of an organizer's events."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    event_start = serializers.DateTimeField(read_only=True)
    event_end = serializers.DateTimeField(read_only=True)
    team_mode = serializers.BooleanField(read_only=True)


class EventListResponseSerializer(serializers.Serializer):
    """Envelope returned by the organizer event list."""

    events = EventSummarySerializer(many=True, read_only=True)


class EventDetailSerializer(serializers.Serializer):
    """Full organizer-facing event detail projection."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    status = serializers.CharField(read_only=True)
    event_start = serializers.DateTimeField(read_only=True)
    event_end = serializers.DateTimeField(read_only=True)
    registration_deadline = serializers.DateTimeField(read_only=True, allow_null=True)
    scenario_id = serializers.CharField(read_only=True, allow_blank=True)
    auto_cleanup = serializers.BooleanField(read_only=True)
    cleanup_delay_hours = serializers.IntegerField(read_only=True)
    max_participants = serializers.IntegerField(read_only=True, allow_null=True)
    team_mode = serializers.BooleanField(read_only=True)
    team_size_limit = serializers.IntegerField(read_only=True, allow_null=True)
    range_config = serializers.DictField(read_only=True)
    range_spinup_minutes = serializers.IntegerField(read_only=True)
    submission_cooldown_seconds = serializers.IntegerField(read_only=True)
    attempt_limit_mode = serializers.CharField(read_only=True)
    attempt_limit_cooldown_seconds = serializers.IntegerField(read_only=True)
    rating_visibility = serializers.CharField(read_only=True)
    scoring_mode = serializers.CharField(read_only=True)
    scoreboard_visible = serializers.BooleanField(read_only=True)
    scoreboard_freeze_at = serializers.DateTimeField(read_only=True, allow_null=True)


class EventWriteSerializer(serializers.Serializer):
    """Create/update request body: the mutable event fields only.

    ``status``, ``created_by``, ``id``, and timestamps are intentionally absent;
    the service layer also filters to mutable fields, so mass assignment is
    prevented at two layers. Updates are validated ``partial=True`` by the view.
    """

    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    event_start = serializers.DateTimeField()
    event_end = serializers.DateTimeField()
    registration_deadline = serializers.DateTimeField(required=False, allow_null=True)
    scenario_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    auto_cleanup = serializers.BooleanField(required=False)
    cleanup_delay_hours = serializers.IntegerField(required=False)
    max_participants = serializers.IntegerField(required=False, allow_null=True)
    team_mode = serializers.BooleanField(required=False)
    team_size_limit = serializers.IntegerField(required=False, allow_null=True)
    range_spinup_minutes = serializers.IntegerField(required=False)
    range_config = serializers.DictField(required=False)
    submission_cooldown_seconds = serializers.IntegerField(required=False)
    attempt_limit_mode = serializers.CharField(required=False)
    attempt_limit_cooldown_seconds = serializers.IntegerField(required=False)
    rating_visibility = serializers.CharField(required=False)
    scoring_mode = serializers.CharField(required=False)
    scoreboard_visible = serializers.BooleanField(required=False)
    scoreboard_freeze_at = serializers.DateTimeField(required=False, allow_null=True)


class EventMutationResultSerializer(serializers.Serializer):
    """Compact result returned after creating or updating an event."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)


class ForceDeleteEventRequestSerializer(serializers.Serializer):
    """Force-delete confirmation body."""

    confirmation_name = serializers.CharField()


class ForceDeleteEventResultSerializer(serializers.Serializer):
    """Summary returned by a force-delete (range teardown counts)."""

    event_id = serializers.CharField(read_only=True)
    event_name = serializers.CharField(read_only=True)
    ranges_destroyed = serializers.IntegerField(read_only=True)
    ranges_failed = serializers.IntegerField(read_only=True)


class ScenarioRefSerializer(serializers.Serializer):
    """A CMS scenario id/name pair available for a CTF event."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class ScenarioListResponseSerializer(serializers.Serializer):
    """Envelope returned by the scenario list."""

    scenarios = ScenarioRefSerializer(many=True, read_only=True)


# ---------------------------------------------------------------------------
# Organizer serializers (challenge management)
# ---------------------------------------------------------------------------


class ChallengeWriteSerializer(serializers.Serializer):
    """Create/update request body for a challenge.

    Declares every field the ``ctf.services.create_challenge`` /
    ``update_challenge`` facade consumes so nothing is silently dropped: the
    mass-assignable challenge fields plus the write-only ``flag`` (plaintext),
    ``flags`` (multi-flag records), ``tags``, ``topics``, and ``next_challenge``.
    ``flag`` is intentionally optional at this layer; the service enforces the
    flag-or-flags rule and raises ``CTFValidationError`` (mapped to 400).
    Updates are validated ``partial=True`` by the view.
    """

    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    points = serializers.IntegerField(required=False)
    difficulty = serializers.CharField(required=False, allow_blank=True)
    flag_format = serializers.CharField(required=False, allow_blank=True)
    solution = serializers.CharField(required=False, allow_blank=True)
    max_attempts = serializers.IntegerField(required=False)
    release_time = serializers.DateTimeField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False)
    visibility = serializers.CharField(required=False, allow_blank=True)
    target_instance_name = serializers.CharField(required=False, allow_blank=True)
    target_port = serializers.IntegerField(required=False, allow_null=True)
    flag = serializers.CharField(required=False, allow_blank=True, write_only=True)
    flags = serializers.ListField(child=serializers.DictField(), required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    topics = serializers.ListField(child=serializers.CharField(), required=False)
    next_challenge = serializers.CharField(required=False, allow_null=True)


class ChallengeSummarySerializer(serializers.Serializer):
    """List projection of one challenge for an event."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.IntegerField(read_only=True)
    difficulty = serializers.CharField(read_only=True, allow_blank=True)
    order = serializers.IntegerField(read_only=True)
    tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    topics = serializers.ListField(child=serializers.CharField(), read_only=True)


class ChallengeListResponseSerializer(serializers.Serializer):
    """Envelope returned by the event challenge list."""

    challenges = ChallengeSummarySerializer(many=True, read_only=True)


class ChallengeHintSerializer(serializers.Serializer):
    """Organizer-facing hint projection (full text is exposed)."""

    id = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True, allow_blank=True)
    penalty = serializers.IntegerField(read_only=True)
    order = serializers.IntegerField(read_only=True)


class OrganizerChallengeDetailSerializer(serializers.Serializer):
    """Full organizer-facing challenge detail projection."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    category = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.IntegerField(read_only=True)
    difficulty = serializers.CharField(read_only=True, allow_blank=True)
    flag_format = serializers.CharField(read_only=True, allow_blank=True)
    hints = ChallengeHintSerializer(many=True, read_only=True)
    max_attempts = serializers.IntegerField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    release_time = serializers.DateTimeField(read_only=True, allow_null=True)
    tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    topics = serializers.ListField(child=serializers.CharField(), read_only=True)
    solution = serializers.CharField(read_only=True, allow_blank=True)


class ChallengeMutationResultSerializer(serializers.Serializer):
    """Compact result returned after creating or updating a challenge."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.IntegerField(read_only=True)


class DeleteSuccessSerializer(serializers.Serializer):
    """The ``{"success": true}`` envelope returned by service-backed deletes."""

    success = serializers.BooleanField(read_only=True)


class FlagWriteSerializer(serializers.Serializer):
    """Request body for adding a flag to a challenge.

    Mirrors the legacy view's ``flag_data`` construction: ``flag`` is optional
    here (the view enforces that a value is present for static/regex types), and
    ``case_sensitive`` / ``order`` / ``validator_config`` carry the same defaults
    the service consumes.
    """

    flag = serializers.CharField(required=False, allow_blank=True, default="")
    flag_type = serializers.CharField(required=False, default="static")
    case_sensitive = serializers.BooleanField(required=False, default=True)
    order = serializers.IntegerField(required=False, default=0)
    validator_config = serializers.DictField(required=False, allow_null=True, default=None)


class FlagCreateResultSerializer(serializers.Serializer):
    """Result returned after adding a flag (validator_config only when set)."""

    id = serializers.CharField(read_only=True)
    flag_type = serializers.CharField(read_only=True)
    case_sensitive = serializers.BooleanField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    validator_config = serializers.DictField(read_only=True, required=False)


class HintWriteSerializer(serializers.Serializer):
    """Request body for adding a hint. ``penalty`` / ``order`` are optional so the
    service applies its own defaults (order defaults to the current hint count).
    """

    text = serializers.CharField()
    penalty = serializers.IntegerField(required=False)
    order = serializers.IntegerField(required=False)


class HintListResponseSerializer(serializers.Serializer):
    """Envelope returned by the challenge hint list."""

    hints = ChallengeHintSerializer(many=True, read_only=True)


class ChallengeFileMetaSerializer(serializers.Serializer):
    """Metadata projection of one challenge attachment (organizer list view)."""

    id = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True, allow_blank=True)
    file_size_bytes = serializers.IntegerField(read_only=True)
    file_size_display = serializers.CharField(read_only=True)
    content_type = serializers.CharField(read_only=True, allow_blank=True)
    sha256_hash = serializers.CharField(read_only=True, allow_blank=True)
    order = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class ChallengeFileListResponseSerializer(serializers.Serializer):
    """Envelope returned by the challenge file list."""

    files = ChallengeFileMetaSerializer(many=True, read_only=True)


class ChallengeFileUploadSerializer(serializers.Serializer):
    """Multipart request body for uploading a challenge attachment."""

    file = serializers.FileField()
    display_name = serializers.CharField(required=False, allow_blank=True)


class ChallengeFileUploadResultSerializer(serializers.Serializer):
    """Result returned after uploading a challenge attachment."""

    id = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True, allow_blank=True)
    file_size_bytes = serializers.IntegerField(read_only=True)
    file_size_display = serializers.CharField(read_only=True)


class FileDownloadResponseSerializer(serializers.Serializer):
    """Presigned download URL for a challenge attachment."""

    url = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True)


class PrerequisiteWriteSerializer(serializers.Serializer):
    """Request body for adding a challenge prerequisite."""

    required_challenge_id = serializers.UUIDField()


class PrerequisiteSerializer(serializers.Serializer):
    """List projection of one challenge prerequisite."""

    id = serializers.CharField(read_only=True)
    required_challenge_id = serializers.CharField(read_only=True)
    required_challenge_name = serializers.CharField(read_only=True)
    required_challenge_category = serializers.CharField(read_only=True, allow_blank=True)
    required_challenge_points = serializers.IntegerField(read_only=True)


class PrerequisiteListResponseSerializer(serializers.Serializer):
    """Envelope returned by the challenge prerequisite list."""

    prerequisites = PrerequisiteSerializer(many=True, read_only=True)


class PrerequisiteCreateResultSerializer(serializers.Serializer):
    """Result returned after adding a prerequisite."""

    id = serializers.CharField(read_only=True)
    required_challenge_id = serializers.CharField(read_only=True)
    required_challenge_name = serializers.CharField(read_only=True)


class RateChallengeRequestSerializer(serializers.Serializer):
    """Participant request body for rating a challenge (1-5)."""

    value = serializers.IntegerField()


class RateChallengeResultSerializer(serializers.Serializer):
    """Result returned after recording a challenge rating."""

    value = serializers.IntegerField(read_only=True)
    challenge_id = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# Participant play serializers (flag submission, hints, own submissions)
# ---------------------------------------------------------------------------


class SubmitFlagRequestSerializer(serializers.Serializer):
    """Participant request body for submitting a flag.

    ``flag`` is blank-tolerant at this layer so the view can apply the legacy
    strip-then-reject rule and return the exact ``Could not process challenge
    action.`` 400 envelope for an empty or whitespace-only flag.
    """

    flag = serializers.CharField(required=False, allow_blank=True, allow_null=True, write_only=True)


class SubmitFlagResultSerializer(serializers.Serializer):
    """Scored result returned after a flag submission."""

    correct = serializers.BooleanField(read_only=True)
    points_awarded = serializers.IntegerField(read_only=True)
    attempt_number = serializers.IntegerField(read_only=True)
    score = serializers.IntegerField(read_only=True)
    rank = serializers.IntegerField(read_only=True, allow_null=True)
    message = serializers.CharField(read_only=True)


class UseHintRequestSerializer(serializers.Serializer):
    """Optional body for unlocking a hint.

    Omit the body (or send ``{}``) to unlock the next hint in order; pass
    ``hint_id`` to unlock a specific hint.
    """

    hint_id = serializers.UUIDField(required=False, write_only=True)


class UseHintResultSerializer(serializers.Serializer):
    """Result returned after unlocking a hint."""

    text = serializers.CharField(read_only=True)
    penalty = serializers.IntegerField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    total_penalty = serializers.IntegerField(read_only=True)
    already_unlocked = serializers.BooleanField(read_only=True)


class SubmissionListItemSerializer(serializers.Serializer):
    """One of the requesting participant's own submissions."""

    id = serializers.CharField(read_only=True)
    challenge_id = serializers.CharField(read_only=True)
    challenge_name = serializers.CharField(read_only=True)
    is_correct = serializers.BooleanField(read_only=True)
    points_awarded = serializers.IntegerField(read_only=True)
    attempt_number = serializers.IntegerField(read_only=True)
    submitted_at = serializers.DateTimeField(read_only=True, allow_null=True)


class SubmissionListResponseSerializer(serializers.Serializer):
    """Envelope returned by the participant submission history."""

    submissions = SubmissionListItemSerializer(many=True, read_only=True)
    total = serializers.IntegerField(read_only=True)


# ---------------------------------------------------------------------------
# Organizer serializers (participant management)
# ---------------------------------------------------------------------------


class ParticipantSummarySerializer(serializers.Serializer):
    """List projection of one participant for an event."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    team_name = serializers.CharField(read_only=True, allow_null=True)
    registered_at = serializers.DateTimeField(read_only=True, allow_null=True)
    total_score = serializers.IntegerField(read_only=True)


class ParticipantListResponseSerializer(serializers.Serializer):
    """Envelope returned by the event participant list."""

    participants = ParticipantSummarySerializer(many=True, read_only=True)
    total = serializers.IntegerField(read_only=True)


class ParticipantInviteSerializer(serializers.Serializer):
    """Request body for inviting a single participant.

    ``name`` and ``email`` are both required and non-blank (mirroring the legacy
    truthiness check). ``email`` is a plain ``CharField`` rather than an
    ``EmailField`` because the service layer owns email validation.
    """

    name = serializers.CharField()
    email = serializers.CharField()


class ParticipantInviteResultSerializer(serializers.Serializer):
    """Result returned after inviting a single participant."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    invited = serializers.BooleanField(read_only=True)


class ParticipantImportSerializer(serializers.Serializer):
    """Request body for bulk-importing participants.

    ``participants`` is typed as a bare list (each element validated per-item in
    the view, mirroring the legacy #1149 handling) rather than a strict
    ``ListField(child=DictField())``: a non-list still 400s at this layer, but a
    list carrying non-object elements must yield per-item errors, not a
    top-level 400.
    """

    participants = serializers.ListField(required=False, default=list)


class ParticipantImportedItemSerializer(serializers.Serializer):
    """One successfully imported participant."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)


class ParticipantImportErrorSerializer(serializers.Serializer):
    """One per-row import error (``email`` present only for service failures)."""

    index = serializers.IntegerField(read_only=True)
    email = serializers.CharField(read_only=True, required=False)
    error = serializers.CharField(read_only=True)


class ParticipantImportResultSerializer(serializers.Serializer):
    """Summary returned by a bulk import."""

    imported = serializers.IntegerField(read_only=True)
    participants = ParticipantImportedItemSerializer(many=True, read_only=True)
    errors = ParticipantImportErrorSerializer(many=True, read_only=True)


class ParticipantDetailSerializer(serializers.Serializer):
    """Full organizer-facing participant detail projection."""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    team_name = serializers.CharField(read_only=True, allow_null=True)
    registered_at = serializers.DateTimeField(read_only=True, allow_null=True)
    invited_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_active_at = serializers.DateTimeField(read_only=True, allow_null=True)
    total_score = serializers.IntegerField(read_only=True)
    solved_count = serializers.IntegerField(read_only=True)
    attempt_count = serializers.IntegerField(read_only=True)
    event_id = serializers.CharField(read_only=True)


class ParticipantDeleteResultSerializer(serializers.Serializer):
    """Confirmation returned after soft-deleting a participant."""

    deleted = serializers.BooleanField(read_only=True)
    id = serializers.CharField(read_only=True)


class ResendInviteResultSerializer(serializers.Serializer):
    """Confirmation returned after resetting and resending a participant invite."""

    success = serializers.BooleanField(read_only=True)
    id = serializers.CharField(read_only=True)
    invited = serializers.BooleanField(read_only=True)


class AssignBracketRequestSerializer(serializers.Serializer):
    """Request body for assigning or removing a participant's bracket.

    ``bracket_id`` is typed loosely (nullable string) so the view keeps the
    legacy branch mapping: a malformed value 400s as ``Invalid bracket ID
    format``, a same-event violation 400s, an unknown bracket 404s, and ``null``
    removes the current bracket.
    """

    bracket_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class AssignBracketResultSerializer(serializers.Serializer):
    """Result returned after assigning or removing a participant's bracket."""

    status = serializers.CharField(read_only=True)
    bracket = _NamedRefSerializer(read_only=True, allow_null=True)


# ---------------------------------------------------------------------------
# Range lifecycle serializers (participant status/access + organizer range ops)
# ---------------------------------------------------------------------------


class RangeStatusResponseSerializer(serializers.Serializer):
    """Participant range status projection (or the not-assigned sentinel)."""

    participant_id = serializers.CharField(read_only=True, required=False)
    status = serializers.CharField(read_only=True)
    range_instance_id = serializers.IntegerField(read_only=True, allow_null=True)


class RangeAccessResponseSerializer(serializers.Serializer):
    """Redirect pointer to the mission_control Guacamole RDP endpoint."""

    redirect = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)


class RangeListItemSerializer(serializers.Serializer):
    """A single participant's range row in the organizer range list."""

    participant_id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)
    range_instance_id = serializers.IntegerField(read_only=True, allow_null=True)
    range_status = serializers.CharField(read_only=True)


class RangeListResponseSerializer(serializers.Serializer):
    """Organizer range list plus the provisioning-progress projection."""

    event_id = serializers.CharField(read_only=True)
    ranges = RangeListItemSerializer(many=True, read_only=True)
    progress = serializers.DictField(read_only=True)


class RangeProvisionQueuedSerializer(serializers.Serializer):
    """Acknowledgement returned when bulk range provisioning is enqueued."""

    event_id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    task_id = serializers.CharField(read_only=True)
    task_status = serializers.CharField(read_only=True)


class ParticipantRangeActionResultSerializer(serializers.Serializer):
    """Pass-through result of a single-participant range lifecycle action."""

    participant_id = serializers.CharField(read_only=True, required=False)
    range_instance_id = serializers.IntegerField(read_only=True, allow_null=True, required=False)
    status = serializers.CharField(read_only=True, required=False)


class RangeRecoveryRequestSerializer(serializers.Serializer):
    """Organizer range-recovery request body (issue #1018).

    ``strategy`` and ``spare_range_instance_id`` are typed loosely so the view
    keeps the legacy boundary validation (a non-string strategy or a
    non-positive-int spare id both 400) while the service validates ``strategy``
    against ``ctf.enums`` and resolves/validates the spare range itself.
    """

    strategy = serializers.CharField()
    spare_range_instance_id = serializers.IntegerField(required=False, allow_null=True)


class RangeRecoveryResultSerializer(serializers.Serializer):
    """Result returned after a range recovery attempt."""

    recovery_id = serializers.CharField(read_only=True)
    participant_id = serializers.CharField(read_only=True)
    event_id = serializers.CharField(read_only=True)
    old_range_instance_id = serializers.IntegerField(read_only=True, allow_null=True)
    replacement_range_instance_id = serializers.IntegerField(read_only=True, allow_null=True)
    replacement_request_id = serializers.CharField(read_only=True, allow_null=True)
    strategy = serializers.CharField(read_only=True)
    phase = serializers.CharField(read_only=True)
    failure_category = serializers.CharField(read_only=True, allow_null=True)


class SparePoolRequestSerializer(serializers.Serializer):
    """Organizer spare-pool top-up request body (``count`` bounded non-negative)."""

    count = serializers.IntegerField()


class SpareProvisionResultSerializer(serializers.Serializer):
    """Summary returned after topping up an event's spare-range pool."""

    event_id = serializers.CharField(read_only=True)
    target_count = serializers.IntegerField(read_only=True)
    existing = serializers.IntegerField(read_only=True)
    created = serializers.IntegerField(read_only=True)


class SendInvitationsResultSerializer(serializers.Serializer):
    """Result returned after queuing invitation emails for an event."""

    success = serializers.BooleanField(read_only=True)
    event_id = serializers.CharField(read_only=True)
    total = serializers.IntegerField(read_only=True)
    sent = serializers.IntegerField(read_only=True)
    failed = serializers.IntegerField(read_only=True)


# ---------------------------------------------------------------------------
# Notification serializers (organizer announcements + email-template overrides)
# ---------------------------------------------------------------------------


class NotificationListItemSerializer(serializers.Serializer):
    """List projection of one notification for an event."""

    id = serializers.CharField(read_only=True)
    notification_type = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True, allow_blank=True)
    status = serializers.CharField(read_only=True)
    sent_count = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    sent_at = serializers.DateTimeField(read_only=True, allow_null=True)


class NotificationListResponseSerializer(serializers.Serializer):
    """Envelope returned by the event notification list."""

    notifications = NotificationListItemSerializer(many=True, read_only=True)


class NotificationAnnounceRequestSerializer(serializers.Serializer):
    """Request body for sending an announcement notification.

    ``subject`` and ``body`` are blank-tolerant at this layer so the view can
    apply the legacy strip-then-reject rule and return the exact ``Invalid
    notification request.`` 400 envelope for an empty or whitespace-only value.
    """

    subject = serializers.CharField(required=False, allow_blank=True, default="")
    body = serializers.CharField(required=False, allow_blank=True, default="")


class NotificationAnnounceResultSerializer(serializers.Serializer):
    """Result returned after creating and sending an announcement (201)."""

    id = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True, allow_blank=True)
    status = serializers.CharField(read_only=True)
    sent_count = serializers.IntegerField(read_only=True)


class NotificationSendResultSerializer(serializers.Serializer):
    """Result returned after dispatching a notification to its recipients."""

    notification_id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)


class EmailTemplateWriteSerializer(serializers.Serializer):
    """Request body for creating/updating a per-event email-template override.

    Fields are blank-tolerant so the view keeps the legacy validation: a missing
    or empty ``html_body`` / ``text_body`` and any disallowed template syntax
    both surface as a controlled 400. Organizer templates are restricted to the
    flat ``{{ name }}`` placeholder grammar over the per-notification-type
    allowlist enforced by ``ctf.services.email_template`` (CWE-1336, issue #1095).
    """

    subject = serializers.CharField(required=False, allow_blank=True, default="")
    html_body = serializers.CharField(required=False, allow_blank=True, default="")
    text_body = serializers.CharField(required=False, allow_blank=True, default="")


class EmailTemplateResponseSerializer(serializers.Serializer):
    """Per-event email-template override projection."""

    id = serializers.CharField(read_only=True)
    notification_type = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True, allow_blank=True)
    html_body = serializers.CharField(read_only=True, allow_blank=True)
    text_body = serializers.CharField(read_only=True, allow_blank=True)


class EmailTemplateRevertResultSerializer(serializers.Serializer):
    """Confirmation returned after reverting a template to the platform default."""

    status = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# Scoreboard serializers (per-participant timeline + public scoreboard)
# ---------------------------------------------------------------------------


class ScoreTimelineResponseSerializer(serializers.Serializer):
    """Per-participant chronological score progression for a step chart."""

    participant_id = serializers.CharField(read_only=True)
    participant_name = serializers.CharField(read_only=True)
    timeline = serializers.ListField(child=serializers.DictField(), read_only=True)


class PublicScoreboardResponseSerializer(serializers.Serializer):
    """Public scoreboard read surface.

    The runtime returns one of two shapes: the ``{"scoreboard_hidden": true}``
    sentinel when the event hides its scoreboard, or the full ranking payload
    (``event_id``, ``team_mode``, ``frozen``, ``rankings``, ``bracket_rankings``,
    ``brackets``). Every field is optional so this one serializer documents the
    union without changing the view's runtime ``JsonResponse``.
    """

    scoreboard_hidden = serializers.BooleanField(read_only=True, required=False)
    event_id = serializers.CharField(read_only=True, required=False)
    team_mode = serializers.BooleanField(read_only=True, required=False)
    frozen = serializers.BooleanField(read_only=True, required=False)
    rankings = serializers.ListField(child=serializers.DictField(), read_only=True, required=False)
    bracket_rankings = serializers.ListField(
        child=serializers.DictField(), read_only=True, required=False, allow_null=True
    )
    brackets = _NamedRefSerializer(many=True, read_only=True, required=False)
