"""CTF participant sidebar must keep Terminal inside the CTF workspace.

Crossing into Mission Control changes the sidebar and strands participants away
from challenges, scoreboard, and help until they use browser Back.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string
from django.urls import reverse


@pytest.mark.django_db
def test_ctf_participant_sidebar_renders_terminal_nav_link(participant_user):
    html = render_to_string(
        "partials/ctf_participant_sidebar.html",
        {"user": participant_user, "active_nav": ""},
    )
    terminal_url = reverse("ctf:participant_terminal")
    assert f'href="{terminal_url}"' in html
    assert "Terminal" in html


@pytest.mark.django_db
def test_ctf_participant_dashboard_includes_terminal_sidebar_link(authenticated_participant_client, ctf_participant):
    response = authenticated_participant_client.get(reverse("ctf:participant_dashboard"))
    assert response.status_code == 200
    terminal_url = reverse("ctf:participant_terminal")
    assert terminal_url.encode() in response.content
    assert b"Terminal" in response.content


@pytest.mark.django_db
def test_ctf_participant_can_open_terminal_page(authenticated_participant_client, ctf_participant):
    response = authenticated_participant_client.get(reverse("ctf:participant_terminal"))
    assert response.status_code == 200
