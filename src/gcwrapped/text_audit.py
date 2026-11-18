"""`make text-audit`: how much of the chat is user text and which placeholders it holds (D95).

Ryan runs it on the real database and reads the numbers, so it follows the `make redaction-audit`
rules: every output line is a fixed label plus a number (or the database name, or a pattern id
from the dbt seed, which is interface text and not message text), each line is checked with
fullmatch before anything is printed, and a failure prints one fixed line with the step and the
exception's TYPE name only. It reads fct_message_text (the one definition of user text) and the
length of each message's content, aggregates inside SQL, and writes nothing anywhere.

The residual lines (user text by length, emoji-only, URL-only) are how a placeholder template
nobody listed shows up: a large "user text 11+" count with a familiar shape means a pattern is
missing from the seed.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from gcwrapped.db import database_name, database_url
from gcwrapped.docker import start_database
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError

STEP_DOCKER = "docker"
STEP_DATABASE = "database"
STEP_QUERY = "query"
STEP_RENDER = "render"

_DATABASES = "|".join(sorted(ALLOWED_DATABASES))
_PATTERN_ID = r"[a-z][a-z0-9_]*"
OK_LINE = "text-audit: ok"
_FAILED_SHAPE = (
    rf"text-audit: failed at ({STEP_DOCKER}|{STEP_DATABASE}|{STEP_QUERY}|{STEP_RENDER}) "
    r"\([A-Za-z_][A-Za-z0-9_]*\)"
)

# Emoji-only: whitespace, joiners, variation selectors, and the symbol and pictograph blocks.
# URL-only: one http(s) link. Postgres ARE escapes (\u, \U), so the string is raw.
_EMOJI_ONLY = r"^[\s‍️←-⯿〰〽㊗㊙\U0001F000-\U0001FAFF]+$"
_URL_ONLY = r"^\s*https?://\S+\s*$"

PATTERNS_SQL = "SELECT pattern_id FROM analytics.instagram_placeholder_patterns ORDER BY 1"
COUNTS_SQL = f"""
SELECT count(*),
       count(*) FILTER (WHERE t.has_user_text OR t.placeholder_pattern_id IS NOT NULL),
       count(*) FILTER (WHERE t.placeholder_pattern_id IS NOT NULL),
       count(*) FILTER (WHERE t.has_user_text),
       count(*) FILTER (WHERE t.has_user_text AND char_length(btrim(m.content)) BETWEEN 1 AND 3),
       count(*) FILTER (WHERE t.has_user_text AND char_length(btrim(m.content)) BETWEEN 4 AND 10),
       count(*) FILTER (WHERE t.has_user_text AND char_length(btrim(m.content)) >= 11),
       count(*) FILTER (WHERE t.has_user_text AND m.content ~ '{_EMOJI_ONLY}'),
       count(*) FILTER (WHERE t.has_user_text AND m.content ~* '{_URL_ONLY}')
FROM raw.messages AS m
JOIN analytics.fct_message_text AS t USING (message_key)
"""
BY_PATTERN_SQL = """
SELECT placeholder_pattern_id, count(*) FROM analytics.fct_message_text
WHERE placeholder_pattern_id IS NOT NULL GROUP BY 1
"""


class ShapeError(Exception):
    """A rendered line did not match its allowed shape. Only the class name is ever printed."""


@dataclass(frozen=True)
class TextReport:
    database: str
    messages: int
    non_blank: int
    placeholders: int
    by_pattern: dict[str, int]  # every seed pattern id, zero when it matched nothing
    user_text: int
    short: int  # 1-3 characters
    medium: int  # 4-10
    long: int  # 11+
    emoji_only: int
    url_only: int


def audit_database(url: str, set_step: Callable[[str], None]) -> TextReport:
    """Count in the database `url` names. Reports the step it is on via set_step."""
    set_step(STEP_DATABASE)
    name = database_name(url)
    if name not in ALLOWED_DATABASES:
        raise UnexpectedDatabaseError
    set_step(STEP_QUERY)
    with psycopg.connect(url) as conn:
        ids = [str(r[0]) for r in conn.execute(PATTERNS_SQL)]
        found = {str(k): int(n) for k, n in conn.execute(BY_PATTERN_SQL)}
        row = conn.execute(COUNTS_SQL).fetchone()
    if row is None:
        raise ShapeError
    c = [int(n) for n in row]
    return TextReport(
        database=name,
        messages=c[0],
        non_blank=c[1],
        placeholders=c[2],
        by_pattern={pattern_id: found.get(pattern_id, 0) for pattern_id in ids},
        user_text=c[3],
        short=c[4],
        medium=c[5],
        long=c[6],
        emoji_only=c[7],
        url_only=c[8],
    )


def render(report: TextReport) -> list[str]:
    """The report lines, in order, without the final status line."""
    return [
        f"target database: {report.database}",
        f"messages: {report.messages}",
        f"non-blank content: {report.non_blank}",
        f"placeholders total: {report.placeholders}",
        *(f"placeholder {pattern_id}: {n}" for pattern_id, n in report.by_pattern.items()),
        f"user text messages: {report.user_text}",
        f"user text 1-3 characters: {report.short}",
        f"user text 4-10 characters: {report.medium}",
        f"user text 11+ characters: {report.long}",
        f"emoji-only user text: {report.emoji_only}",
        f"URL-only user text: {report.url_only}",
    ]


def _shapes(report: TextReport) -> list[str]:
    return [
        rf"target database: ({_DATABASES})",
        r"messages: \d+",
        r"non-blank content: \d+",
        r"placeholders total: \d+",
        *(rf"placeholder {_PATTERN_ID}: \d+" for _ in report.by_pattern),
        r"user text messages: \d+",
        r"user text 1-3 characters: \d+",
        r"user text 4-10 characters: \d+",
        r"user text 11\+ characters: \d+",
        r"emoji-only user text: \d+",
        r"URL-only user text: \d+",
    ]


def check_shapes(report: TextReport, lines: list[str]) -> None:
    """Raise ShapeError unless each line fully matches its shape, and the pattern ids line up."""
    shapes = _shapes(report)
    if len(lines) != len(shapes):
        raise ShapeError
    for line, shape in zip(lines, shapes, strict=True):
        if re.fullmatch(shape, line) is None:
            raise ShapeError


def failure_line(step: str, exc: Exception) -> str:
    line = f"text-audit: failed at {step} ({type(exc).__name__})"
    if re.fullmatch(_FAILED_SHAPE, line) is None:
        line = f"text-audit: failed at {step} (Exception)"
    return line


def run(
    docker: Callable[[], None] = start_database,
    audit: Callable[[Callable[[str], None]], TextReport] | None = None,
) -> int:
    """Run the audit, shape-check every line, then print. Exit 1 on any failure."""
    step = STEP_DOCKER

    def set_step(name: str) -> None:
        nonlocal step
        step = name

    try:
        docker()
        set_step(STEP_DATABASE)  # from here on a failure is the audit's, never docker's
        report = audit(set_step) if audit is not None else audit_database(database_url(), set_step)
        set_step(STEP_RENDER)
        lines = render(report)
        check_shapes(report, lines)
    except Exception as exc:  # type name only: messages from drivers can echo data
        print(failure_line(step, exc))
        return 1
    for line in lines:
        print(line)
    print(OK_LINE)
    return 0
