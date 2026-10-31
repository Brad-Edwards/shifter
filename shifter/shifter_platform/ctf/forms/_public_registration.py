"""Bounded form for the public event-registration browser surface."""

from collections.abc import Mapping
from typing import cast

from django import forms

from ctf.exceptions import CTFValidationError
from ctf.services.participant.accounts import normalize_participant_username
from shared.time_zones import time_zone_choices


class PublicRegistrationForm(forms.Form):
    """The complete public signup input allowlist.

    The participant's single identity is the ``username``: it is the login handle
    and the public scoreboard display name (#2455). Email is only a delivery
    channel for login details. The optional time zone sets the zone of times in
    email (#582); the first choice uses the event time zone.
    """

    username = forms.CharField(
        max_length=32,
        strip=True,
        label="Username",
        help_text="Your username will be publicly visible on scoreboards.",
    )
    email = forms.EmailField(max_length=254)
    time_zone = forms.ChoiceField(required=False, label="Time zone")

    def __init__(self, data: Mapping[str, object] | None = None, *, event_timezone: str = "UTC") -> None:
        super().__init__(data)
        time_zone = cast(forms.ChoiceField, self.fields["time_zone"])
        time_zone.choices = [("", f"Event time zone ({event_timezone})"), *time_zone_choices()]

    def clean_username(self) -> str:
        """Validate the chosen handle with the canonical participant policy."""
        try:
            return normalize_participant_username(self.cleaned_data["username"])
        except CTFValidationError as exc:
            raise forms.ValidationError(exc.message) from exc
