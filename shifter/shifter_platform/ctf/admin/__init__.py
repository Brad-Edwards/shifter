"""Django admin for CTF models, grouped by bounded context (#683).

Importing the submodules here is what registers every ModelAdmin with the
default admin site, exactly as the former single ``ctf/admin.py`` did.
"""

from ctf.admin import challenge, event, notifications, people, scoring
from ctf.admin._shared import SoftDeleteAdminMixin

__all__ = ["SoftDeleteAdminMixin", "challenge", "event", "notifications", "people", "scoring"]
