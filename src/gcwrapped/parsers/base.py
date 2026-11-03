"""The interface every platform parser implements."""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from gcwrapped.models import ParsedMessage


class Parser(Protocol):
    """Turns one export file into a stream of canonical messages."""

    def parse(self, path: Path) -> Iterator[ParsedMessage]:
        """Yield messages from the export at `path`, lazily."""
        ...
