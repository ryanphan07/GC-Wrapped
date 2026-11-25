"""A mart_member_wrapped row in plain words (D123). Pure functions: no database, no Streamlit."""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from gcwrapped.labeling.terminal import safe_text

WEEKDAYS = (  # mart weekday 1 (Monday) to 7
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
NOT_TAGGED = "not tagged yet"
NO_PROFILES = (
    "No profiles yet. A member appears once their consent is loaded and the dbt models are built."
)
CARD_NOTE = (
    "Counts cover the messages this member typed, shared with their consent. The busiest day and "
    "the busiest hour are counted separately, in local time. Topics and mood are estimates from "
    "a local model."
)


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def hour_text(hour: int) -> str:
    """0 -> "12 am", 13 -> "1 pm", 22 -> "10 pm"."""
    return f"{hour % 12 or 12} {'am' if hour < 12 else 'pm'}"


def _count_of(amount: float, unit: str) -> str:
    shown = f"{amount:.1f}".removesuffix(".0")
    return f"{shown} {unit}{'' if shown == '1' else 's'}"


def duration_text(seconds: float) -> str:
    if seconds < 90:
        return _count_of(round(seconds), "second")
    if seconds < 5400:
        return _count_of(round(seconds / 60), "minute")
    return _count_of(seconds / 3600, "hour")


def percent_text(share: float) -> str:
    """Whole percent; a share that is not zero never reads as 0%."""
    if 0 < share < 0.005:
        return "under 1%"
    return f"{share * 100:.0f}%"


def date_text(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def card_lines(profile: Mapping[str, Any]) -> list[str]:
    """The card of one mart row. Every column is a number, a date or a name from a closed list,
    so nothing here is chat text (the member's name is shown by the caller)."""
    reply = profile["median_reply_seconds"]
    topics = [
        str(profile[name]).replace("sports_fitness", "sports and fitness")
        for name in ("top_topic_1", "top_topic_2", "top_topic_3")
        if not _missing(profile[name])
    ]
    shares = [profile[f"{name}_share"] for name in ("positive", "neutral", "negative")]
    if any(_missing(s) for s in shares):
        mood = NOT_TAGGED
    else:
        mood = ", ".join(
            f"{percent_text(float(share))} {name}"
            for share, name in zip(shares, ("positive", "neutral", "negative"), strict=True)
        )
    return [
        f"Messages sent: {int(profile['messages_sent']):,}",
        f"Share of the thread's messages: {percent_text(float(profile['thread_share']))}",
        f"Most active: {WEEKDAYS[int(profile['most_active_weekday']) - 1]} at "
        f"{hour_text(int(profile['most_active_hour']))}",
        "Typical reply time: "
        + ("no replies yet" if _missing(reply) else duration_text(float(reply))),
        f"First message: {date_text(profile['first_message_date'])}",
        f"Last message: {date_text(profile['last_message_date'])}",
        f"Top topics: {', '.join(topics) or NOT_TAGGED}",
        f"Mood: {mood}",
    ]


def member_labels(profiles: Sequence[Mapping[str, Any]]) -> list[str]:
    """One picker label per profile: the name, plus the thread ID where two share a name."""
    names = [safe_text(str(p["member_name"])) for p in profiles]
    shared = Counter(names)
    return [
        name if shared[name] == 1 else f"{name} (thread {profile['thread_id']})"
        for name, profile in zip(names, profiles, strict=True)
    ]
