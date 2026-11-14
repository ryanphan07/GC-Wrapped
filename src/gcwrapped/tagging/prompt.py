"""The tagging prompt (D89): one fixed system prompt plus the same input the labeler saw (D85, D87).

The input is the message and up to TAG_CONTEXT_MESSAGES previous local-eligible messages of the
thread, raw text, oldest first. No sender names, no thread IDs and no timestamps are passed: the
labeler never saw them either. Redaction applies only to external calls (D70), and this prompt only
ever goes to a localhost server.

The system prompt is identical for every request so the server's prompt cache reuses its prefix.
`prompt_version` is a hash of everything that shapes the request (system prompt, user template and
JSON schema), so any edit makes old and new tags distinguishable (D89).
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from gcwrapped.labeling.schema import SENTIMENTS, TAG_CONTEXT_MESSAGES, TOPICS
from gcwrapped.labeling.source import EligibleIndex, Message

TOPIC_DEFINITIONS = {
    "plans": "making or changing plans, scheduling, logistics, meeting up",
    "school": "classes, exams, homework, professors, studying, campus life",
    "work": "jobs, internships, bosses, shifts, careers, job hunting",
    "food": "meals, restaurants, cooking, groceries, drinks",
    "entertainment": "movies, shows, music, games, concerts, social media content",
    "sports_fitness": "sports, teams, games played or watched, working out, running, the gym",
    "relationships": "family, friends, dating, roommates, feelings about people",
    "banter": "jokes, teasing, memes, reactions and playful back-and-forth with no other subject",
    "news": "current events, local happenings, politics, weather, prices, announcements",
    "other": "anything that fits none of the above, greetings, small talk, unclear messages",
}
SENTIMENT_DEFINITIONS = {
    "positive": "happy, excited, grateful, praising, enthusiastic",
    "neutral": "factual, logistical, a plain question, no clear feeling",
    "negative": "upset, annoyed, disappointed, complaining, worried",
}

SYSTEM_PROMPT = (
    "You label one message from a private group chat with exactly one topic and exactly one "
    "sentiment. Answer only with JSON that matches the schema.\n\n"
    "Topics:\n"
    + "\n".join(f"- {topic}: {TOPIC_DEFINITIONS[topic]}" for topic in TOPICS)
    + "\n\nSentiments:\n"
    + "\n".join(f"- {s}: {SENTIMENT_DEFINITIONS[s]}" for s in SENTIMENTS)
    + "\n\nThe message to label is the last one. Earlier messages, when shown, are context only: "
    "label the last message, using the earlier ones to understand what it refers to."
)

USER_CONTEXT_HEADER = "Earlier messages (context only):"
USER_MESSAGE_HEADER = "Message to label:"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "enum": list(TOPICS)},
        "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
    },
    "required": ["topic", "sentiment"],
    "additionalProperties": False,
}


def build_user_content(context: Sequence[str], text: str) -> str:
    """The user turn: context lines (if any), then the message, in fixed sections."""
    parts: list[str] = []
    if context:
        parts.append(USER_CONTEXT_HEADER)
        parts.extend(f"- {line}" for line in context)
        parts.append("")
    parts.append(USER_MESSAGE_HEADER)
    parts.append(text)
    return "\n".join(parts)


def _template_fingerprint() -> str:
    # The two headers stand for the user template; the context size is part of the contract (D85).
    material = json.dumps(
        [
            SYSTEM_PROMPT,
            USER_CONTEXT_HEADER,
            USER_MESSAGE_HEADER,
            TAG_CONTEXT_MESSAGES,
            RESPONSE_SCHEMA,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


PROMPT_VERSION = _template_fingerprint()[:12]


def messages_for(index: EligibleIndex, message: Message) -> tuple[str, str]:
    """(system, user) for one eligible message, with the labeler's context (`index.before`)."""
    context = [m.text for m in index.before(message.message_id, TAG_CONTEXT_MESSAGES)]
    return SYSTEM_PROMPT, build_user_content(context, message.text)
