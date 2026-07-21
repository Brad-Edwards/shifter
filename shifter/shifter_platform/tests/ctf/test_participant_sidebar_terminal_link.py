"""The embedded terminal stays out of participant UX in favor of Guacamole."""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string
from django.urls import reverse


@pytest.mark.django_db
def test_ctf_participant_sidebar_hides_terminal_nav_link(participant_user):
    html = render_to_string(
        "partials/ctf_participant_sidebar.html",
        {"user": participant_user, "active_nav": ""},
    )
    terminal_url = reverse("ctf:participant_terminal")
    assert f'href="{terminal_url}"' not in html


@pytest.mark.django_db
def test_ctf_participant_dashboard_hides_terminal_sidebar_link(authenticated_participant_client, ctf_participant):
    response = authenticated_participant_client.get(reverse("ctf:participant_dashboard"))
    assert response.status_code == 200
    terminal_url = reverse("ctf:participant_terminal")
    assert terminal_url.encode() not in response.content


@pytest.mark.django_db
def test_ctf_participant_can_open_terminal_page(authenticated_participant_client, ctf_participant):
    response = authenticated_participant_client.get(reverse("ctf:participant_terminal"))
    assert response.status_code == 200
