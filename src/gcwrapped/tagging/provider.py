"""The tagging provider interface (D89) and the hosted stub (D84).

A provider turns (system prompt, user content) into a Tag. The llama.cpp client is the only real
one. `HostedProvider` has no network code at all: it exists so the code path that would call a
hosted model has a name, and it always raises. D84 keeps hosted providers off until the D76 NER
gate (P2-2b) has passed; whoever enables one must replace this stub, in a reviewed change.
"""

from dataclasses import dataclass
from typing import Protocol


class ExternalCallBlocked(Exception):
    """A hosted provider was asked to run. Always raised by `HostedProvider` (D84)."""


class TagFailure(Exception):
    """The provider could not produce a valid tag for one message (after its one retry)."""


class ProviderUnavailable(Exception):
    """The server cannot be reached, so every later message would fail too: stop the run."""


@dataclass(frozen=True)
class Tag:
    topic: str
    sentiment: str


class TagProvider(Protocol):
    model_id: str

    def tag(self, system: str, user: str) -> Tag: ...


class HostedProvider:
    """Stub for a hosted model. Never calls anything; always raises ExternalCallBlocked."""

    model_id = "hosted-blocked"

    def tag(self, system: str, user: str) -> Tag:
        raise ExternalCallBlocked


def make_provider(kind: str, *, llm_url: str, model_id: str) -> TagProvider:
    """ "llamacpp" gives the local client; "hosted" gives the stub; anything else is an error."""
    if kind == "llamacpp":
        from gcwrapped.tagging.llamacpp import LlamaCppProvider

        return LlamaCppProvider(llm_url, model_id=model_id)
    if kind == "hosted":
        return HostedProvider()
    raise ValueError("unknown provider kind")
