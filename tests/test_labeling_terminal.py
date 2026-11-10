"""StdTerminal on a real (pseudo) terminal and on a pipe: single keypress, restored settings."""

import os
import pty
import select
import subprocess
import sys
import time

CHILD = r"""
import sys, termios
from gcwrapped.labeling.terminal import StdTerminal

term = StdTerminal()
before = termios.tcgetattr(0) if sys.stdin.isatty() else None
first = term.key()
after = termios.tcgetattr(0) if sys.stdin.isatty() else None
print("KEY", repr(first), "RESTORED", before == after, flush=True)
print("LINE", repr(term.line("")), flush=True)
print("KEY", repr(term.key()), flush=True)
"""


def _read_until(fd: int, needle: bytes, timeout: float = 20.0) -> bytes:
    seen = b""
    deadline = time.monotonic() + timeout
    while needle not in seen and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                seen += os.read(fd, 4096)
            except OSError:  # the child exited and closed its side
                break
    return seen


def test_a_key_is_read_without_enter_on_a_terminal_and_the_mode_is_restored() -> None:
    master, slave = pty.openpty()
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    os.close(slave)
    try:
        os.write(master, b"7")  # one key, no newline: returns only if cbreak mode is on
        out = _read_until(master, b"RESTORED")
        assert b"KEY '7' RESTORED True" in out
        os.write(master, b"my own words\n")  # a line is read in normal (cooked) mode again
        assert b"LINE 'my own words'" in _read_until(master, b"LINE 'my own words'")
        os.write(master, b"n")
        assert b"KEY 'n'" in _read_until(master, b"KEY 'n'")
        child.wait(timeout=20)
        assert child.returncode == 0
    finally:
        child.kill()
        os.close(master)


TWO_KEYS = r"""
import time
from gcwrapped.labeling.terminal import StdTerminal

term = StdTerminal()
first = term.key()
time.sleep(1.0)  # the second key is typed (and echoed) during this gap, before it is asked for
print("KEYS", repr(first), repr(term.key()), flush=True)
"""


def test_a_key_typed_ahead_between_two_prompts_is_not_lost() -> None:
    master, slave = pty.openpty()
    child = subprocess.Popen(
        [sys.executable, "-c", TWO_KEYS],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    os.close(slave)
    try:
        time.sleep(1.0)  # let the child start and block in its first read
        os.write(master, b"1")
        time.sleep(0.4)  # the child is now asleep, in normal mode, with no read pending
        os.write(master, b"g")
        assert b"KEYS '1' 'g'" in _read_until(master, b"KEYS '1' 'g'")
    finally:
        child.kill()
        os.close(master)


def test_a_pipe_is_read_one_character_at_a_time_and_end_of_input_is_empty() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        input="1\nsecond line\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "KEY '1' RESTORED True"  # not a terminal: nothing to restore, so equal
    assert lines[1] == "LINE ''"  # the rest of the first line, as a scripted run supplies it
    assert lines[2] == "KEY 's'"
