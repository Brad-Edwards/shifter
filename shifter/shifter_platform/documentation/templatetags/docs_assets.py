"""Documentation template tags (#1520, ADR-033).

Exposes the same-origin, Vite-built Mermaid bundle URL to the documentation
templates so they no longer import Mermaid from a public package CDN.
"""

from __future__ import annotations

from django import template

from shared.spa import mermaid_bundle_url as _resolve_mermaid_bundle_url

register = template.Library()


@register.simple_tag
def mermaid_bundle_url() -> str:
    """Return the same-origin Mermaid bundle URL, or ``""`` when it is unbuilt."""
    return _resolve_mermaid_bundle_url() or ""
