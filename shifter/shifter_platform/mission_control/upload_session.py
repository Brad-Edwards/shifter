"""Upload session management.

This module handles the upload lock mechanism that prevents
concurrent uploads from the same user.
"""

import hashlib
import hmac
import time
from typing import Any

# Upload lock timeout in seconds (fallback for browser crash, network loss)
UPLOAD_LOCK_TIMEOUT = 30
UPLOAD_LOCK_FINGERPRINT_KEY = "upload_lock_fingerprint"


def upload_token_fingerprint(upload_token: str) -> str:
    """Return a non-secret fingerprint for correlating a cancel to a session lock."""
    return hashlib.sha256(upload_token.encode("utf-8")).hexdigest()


def check_upload_in_progress(session: dict[str, Any]) -> bool:
    """Check if user has an upload in progress (stored in session).

    Args:
        session: Django session object (or dict-like)

    Returns:
        bool: True if upload is in progress and lock is valid, False otherwise
    """
    lock_data = session.get("upload_lock")
    if not lock_data:
        return False

    if not isinstance(lock_data, dict):
        set_upload_in_progress(session, False)
        return False

    # Auto-expire stale locks
    if time.time() - lock_data.get("started_at", 0) > UPLOAD_LOCK_TIMEOUT:
        set_upload_in_progress(session, False)
        return False

    return True


def upload_lock_matches_token(session: dict[str, Any], upload_token: str) -> bool:
    """Return whether the current non-expired upload lock matches the token."""
    if not upload_token or not check_upload_in_progress(session):
        return False

    lock_data = session.get("upload_lock")
    if not isinstance(lock_data, dict):
        return False

    expected = lock_data.get(UPLOAD_LOCK_FINGERPRINT_KEY)
    if not isinstance(expected, str) or not expected:
        return False

    return hmac.compare_digest(expected, upload_token_fingerprint(upload_token))


def set_upload_in_progress(session: dict[str, Any], in_progress: bool, *, upload_token: str | None = None) -> None:
    """Set upload in progress flag in session.

    Args:
        session: Django session object (or dict-like)
        in_progress: True to set lock, False to clear it
        upload_token: Optional signed upload token to fingerprint for cancel correlation
    """
    if in_progress:
        lock_data: dict[str, Any] = {"started_at": time.time()}
        if upload_token:
            lock_data[UPLOAD_LOCK_FINGERPRINT_KEY] = upload_token_fingerprint(upload_token)
        session["upload_lock"] = lock_data
    else:
        session.pop("upload_lock", None)
