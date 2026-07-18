"""Notification creation form."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django import forms

from ctf.forms._shared import DATETIME_LOCAL_FORMAT, DATETIME_SECONDS_FORMAT
from ctf.models import CTFNotification

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class CTFNotificationForm(forms.ModelForm):
    """Form for creating notifications."""

    class Meta:
        model = CTFNotification
        fields = [
            "notification_type",
            "subject",
            "body",
            "recipient_filter",
            "scheduled_at",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 6}),
            "scheduled_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format=DATETIME_LOCAL_FORMAT,
            ),
        }

    def __init__(self, *args, event=None, **kwargs):
        """Initialize form with event context.

        Args:
            event: The CTFEvent this notification belongs to.
        """
        super().__init__(*args, **kwargs)
        self.event = event

        # Set input formats for datetime fields
        if "scheduled_at" in self.fields:
            self.fields["scheduled_at"].input_formats = [
                DATETIME_LOCAL_FORMAT,
                DATETIME_SECONDS_FORMAT,
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ]

        # Add CSS classes
        for _field_name, field in self.fields.items():
            existing_classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing_classes} form-control".strip()
