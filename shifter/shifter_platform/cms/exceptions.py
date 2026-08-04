"""CMS service exceptions.

Re-exports from shared.exceptions for backwards compatibility.
"""

from shared.exceptions import CMSError


class WorkspaceLaunchDenied(CMSError):
    """A launch's workspace selection is not available to the actor (ADR-046-R9).

    A subclass of ``CMSError`` so existing ``except CMSError`` sites keep treating
    it as a launch failure, while the launch command boundary can catch it
    specifically and map an authorized-shape-but-denied scope to an opaque 403 --
    distinct from the 400 a malformed UUID gets at input validation. The message
    stays non-enumerating: unknown workspace, non-membership, and a role that does
    not permit launching are deliberately indistinguishable. Never string-match
    this error; catch the type.
    """


__all__ = ["CMSError", "WorkspaceLaunchDenied"]
