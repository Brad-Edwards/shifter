"""Structural gate: scenario editor templates must not use inline style attributes."""

from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "scenario_editor"

IN_SCOPE_TEMPLATES = (
    "base.html",
    "detail.html",
    "list.html",
    "form.html",
    "clone.html",
)


@pytest.mark.parametrize("template_name", IN_SCOPE_TEMPLATES)
def test_scenario_editor_templates_have_no_inline_style_attributes(template_name):
    content = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    assert 'style="' not in content, f"{template_name} still contains inline style= attributes"
