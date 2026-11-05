"""Consent allowlist: which chats may be ingested (decisions.md, 2026-11-05).

Deny by default. A thread is ingested only if its numeric thread ID is listed in
GCW_THREAD_ALLOWLIST (comma-separated digits). There is no bypass switch and none may be added.

Privacy: an invalid entry could be a person's name, so errors name the entry's position, never
its value. Errors are raised outside any `except` block so no value rides along as a cause.
"""

import re

from gcwrapped.config import ConsentSettings

ALLOWLIST_VAR = "GCW_THREAD_ALLOWLIST"

# ASCII digits only: str.isdigit() would accept characters such as "²".
_DIGITS = re.compile(r"[0-9]+")


class AllowlistError(ValueError):
    """The allowlist is missing, empty or malformed. Messages never include entry values."""


def parse_allowlist(raw: str) -> frozenset[str]:
    """Parse "123, 456" into {"123", "456"}. Whitespace is ignored; an empty string gives {}.

    Every entry must be all digits. An empty entry ("1,,2" or a trailing comma) is not numeric,
    so it is an error too: a stray comma is more likely a typo than intent.
    """
    if not raw.strip():
        return frozenset()
    entries = [entry.strip() for entry in raw.split(",")]
    for position, entry in enumerate(entries, start=1):
        if _DIGITS.fullmatch(entry) is None:
            raise AllowlistError(
                f"{ALLOWLIST_VAR} entry {position} of {len(entries)} is not numeric"
            )
    return frozenset(entries)


def load_allowlist() -> frozenset[str]:
    """The configured allowlist, possibly empty. Raises AllowlistError if malformed."""
    return parse_allowlist(ConsentSettings().gcw_thread_allowlist)


def require_allowlist() -> frozenset[str]:
    """The configured allowlist; raises AllowlistError if it is missing or empty.

    Every path that loads data goes through this, so an unset variable stops ingestion instead of
    meaning "everything".
    """
    allowlist = load_allowlist()
    if not allowlist:
        raise AllowlistError(
            f"{ALLOWLIST_VAR} is unset or empty; ingestion is deny-by-default "
            "(list the numeric thread IDs you have consent for)"
        )
    return allowlist
