"""Admin registrations for the ``shared`` app.

Django autodiscovers ``shared/admin.py``; the cohesive token-admin code lives in
the ``shared.api_tokens`` package, so import it here to register ``ApiToken``.
"""

from __future__ import annotations

from shared.api_tokens import admin as _api_tokens_admin  # noqa: F401  (registers ApiToken admin)
