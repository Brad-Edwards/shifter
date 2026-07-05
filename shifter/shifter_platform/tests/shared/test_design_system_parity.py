"""Guard that the SPA-consumed design-system CSS matches the source of record.

The design system (#1299) lives at ``docs/design/design-system/`` (source of
record + standalone styleguide). The SPA build bundles a published copy under
``shifter_platform/static/design-system/`` so the CSS is inside the Docker build
context. These must stay byte-identical; re-copy on any change (#1302).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

BASE_DIR = Path(settings.BASE_DIR)
REPO_ROOT = BASE_DIR.parent.parent
DOCS_DS = REPO_ROOT / "docs" / "design" / "design-system"
PUBLISHED_DS = BASE_DIR / "static" / "design-system"


@pytest.mark.parametrize("name", ["tokens.css", "components.css"])
def test_published_design_system_matches_docs(name):
    source = (DOCS_DS / name).read_text(encoding="utf-8")
    published = (PUBLISHED_DS / name).read_text(encoding="utf-8")
    assert published == source, (
        f"static/design-system/{name} drifted from docs/design/design-system/{name}; re-copy the source of record."
    )
