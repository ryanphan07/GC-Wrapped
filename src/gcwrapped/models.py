"""Canonical message types produced by platform parsers."""

from pydantic import AwareDatetime, BaseModel, ConfigDict


class Reaction(BaseModel):
    """One member's emoji reaction to a message."""

    model_config = ConfigDict(frozen=True)

    actor: str
    emoji: str


class ParsedMessage(BaseModel):
    """One message as produced by a platform parser, before loading into `raw`.

    The parser is faithful (see decisions.md): it repairs encoding and structure only. System
    messages, local time and member identity are handled later in dbt.
    """

    model_config = ConfigDict(frozen=True)

    message_key: str
    platform: str
    thread_id: str
    thread_title: str | None
    sender_name: str
    sent_at: AwareDatetime  # always UTC
    content: str | None
    media_count: int
    share_url: str | None
    reactions: list[Reaction]
    source_file: str  # POSIX path relative to the export root

    def __str__(self) -> str:
        # Never let a stray print/log of a message leak its text.
        return f"ParsedMessage(key={self.message_key[:12]})"

    __repr__ = __str__
