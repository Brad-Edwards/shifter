"""CTF challenge service, grouped by flow (#683).

Split from the single ``ctf/services/challenge.py`` module. The public
import surface is unchanged: every entry point is re-exported here, and the
API/view layers import these names late, at call time, so this package
boundary is also the patch point tests already target.
"""

from ctf.services.challenge.access import (
    assert_challenge_available_for_participant,
    assert_challenge_readable_for_participant,
    get_available_challenges,
    get_challenge,
    list_challenges_for_event,
)
from ctf.services.challenge.crud import (
    _CHALLENGE_MUTABLE_FIELDS,
    _sync_release_task,
    create_challenge,
    delete_challenge,
    release_challenge,
    update_challenge,
)
from ctf.services.challenge.flag_crud import (
    _flag_hash_for_payload,
    add_flag,
    remove_flag,
    update_flag,
)
from ctf.services.challenge.flags import (
    VALID_FLAG_TYPES,
    _verify_regex_flag,
    _verify_static_flag,
    hash_flag,
    verify_flag,
    verify_single_flag,
)
from ctf.services.challenge.prerequisites import (
    add_prerequisite,
    check_prerequisites_met,
    get_dependents,
    get_prerequisites,
    remove_prerequisite,
)

__all__ = [
    "VALID_FLAG_TYPES",
    "_CHALLENGE_MUTABLE_FIELDS",
    "_flag_hash_for_payload",
    "_sync_release_task",
    "_verify_regex_flag",
    "_verify_static_flag",
    "add_flag",
    "add_prerequisite",
    "assert_challenge_available_for_participant",
    "assert_challenge_readable_for_participant",
    "check_prerequisites_met",
    "create_challenge",
    "delete_challenge",
    "get_available_challenges",
    "get_challenge",
    "get_dependents",
    "get_prerequisites",
    "hash_flag",
    "list_challenges_for_event",
    "release_challenge",
    "remove_flag",
    "remove_prerequisite",
    "update_challenge",
    "update_flag",
    "verify_flag",
    "verify_single_flag",
]
