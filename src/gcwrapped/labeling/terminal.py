"""Terminal I/O for the labeling flows, behind a small interface so tests can script it.

Real message text is printed here and nowhere else: to the terminal, never to a log or a file.

`safe_text` strips control characters before anything is printed. A chat message is untrusted
input, and an escape sequence in one could rewrite the screen or put text in the terminal's title.
"""

import sys
import unicodedata
from typing import Protocol

EOF_KEY = ""  # what `key` returns when input is exhausted; `line` returns None. Both mean "quit".


class Terminal(Protocol):
    def key(self) -> str:
        """One keypress, without waiting for Enter. EOF_KEY at end of input."""

    def line(self, prompt: str) -> str | None:
        """A line typed after `prompt`, without its newline. None at end of input."""

    def out(self, text: str) -> None:
        """Print one line."""


def safe_text(text: str) -> str:
    """`text` with every control or format character replaced, and newlines shown as a space.

    Categories Cc (controls, including ESC), Cf (format, e.g. bidi overrides), Cs, Co, Cn are
    replaced by U+FFFD, except that line breaks and tabs become a space, so a multi-line message
    stays one readable line.
    """
    out: list[str] = []
    for ch in text:
        if ch in "\n\r\t":
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            out.append("�")
        else:
            out.append(ch)
    return "".join(out)


class StdTerminal:
    """stdin/stdout. On a real terminal `key` uses cbreak mode (Ctrl-C still interrupts); with
    piped input (scripted runs) it reads one character at a time, so the same flow works both ways.
    """

    def key(self) -> str:
        stream = sys.stdin
        if not stream.isatty():
            return stream.read(1)
        import termios
        import tty

        fd = stream.fileno()
        saved = termios.tcgetattr(fd)
        try:
            # TCSANOW, not tty's default TCSAFLUSH: a flush would throw away a key typed ahead
            # between two prompts, which is how a fast "1g" loses its second key.
            tty.setcbreak(fd, termios.TCSANOW)
            return stream.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def line(self, prompt: str) -> str | None:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        raw = sys.stdin.readline()
        return raw.rstrip("\n") if raw else None

    def out(self, text: str) -> None:
        print(text, flush=True)
