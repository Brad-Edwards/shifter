"""Structural gate: experiments templates must not use inline styles.

Inline ``style="..."`` attributes and ``<style>`` blocks are migrated to static
CSS files loaded via ``{% static %}`` (issue #414).
"""

from pathlib import Path

import pytest

# tests/cms/experiments/ -> shifter_platform/
ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = ROOT / "cms" / "experiments" / "templates" / "experiments"

TEMPLATES = sorted(TEMPLATE_DIR.rglob("*.html"))


def _ids(paths):
    return [str(p.relative_to(TEMPLATE_DIR)) for p in paths]


@pytest.mark.parametrize("template", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_inline_style_attributes(template):
    content = template.read_text(encoding="utf-8")
    assert 'style="' not in content, f"{template.name} still contains inline style= attributes"


@pytest.mark.parametrize("template", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_style_blocks(template):
    content = template.read_text(encoding="utf-8")
    assert "<style" not in content, f"{template.name} still contains a <style> block"
