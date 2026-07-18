"""Bracket creation and editing form."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django import forms

from ctf.models import CTFBracket

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class CTFBracketForm(forms.ModelForm):
    """Form for creating and editing brackets."""

    class Meta:
        model = CTFBracket
        fields = [
            "name",
            "description",
            "display_order",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, event=None, **kwargs):
        """Initialize form with event context.

        Args:
            event: The CTFEvent this bracket belongs to.
        """
        super().__init__(*args, **kwargs)
        self.event = event

        # Add CSS classes
        for _field_name, field in self.fields.items():
            existing_classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing_classes} form-control".strip()

    def save(self, commit: bool = True) -> CTFBracket:
        """Save bracket with event assignment.

        Args:
            commit: Whether to save to database.

        Returns:
            The saved bracket instance.
        """
        bracket = super().save(commit=False)

        if self.event:
            bracket.event = self.event

        if commit:
            bracket.save()

        return bracket
