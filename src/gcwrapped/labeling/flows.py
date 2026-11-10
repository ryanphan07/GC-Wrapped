"""The two interactive labeling flows (D85, D86): tags and questions.

Both read from a frozen sample, write after every single decision (so quitting, a crash or Ctrl-C
loses nothing) and resume at the first item with no decision. All real text goes to the terminal
object passed in and nowhere else: no logger, no file. Only IDs, labels and Ryan's own questions are
stored, by the writers in `files`.

Keys: topic by number (1-9, then 0 for the tenth topic), sentiment by letter (g good/positive,
n neutral, b bad/negative), `u` undo, `q` quit and save. In the question flow: `w` write a question,
`s` skip. End of input counts as `q`.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gcwrapped.labeling.files import (
    read_questions,
    read_skips,
    read_tags,
    write_questions,
    write_skips,
    write_tags,
)
from gcwrapped.labeling.schema import (
    QUESTION_CONTEXT_EACH_SIDE,
    QUESTION_TARGET,
    SENTIMENTS,
    TAG_CONTEXT_MESSAGES,
    TOPICS,
    QuestionLabel,
    Skip,
    TagLabel,
)
from gcwrapped.labeling.source import EligibleIndex, Message
from gcwrapped.labeling.terminal import EOF_KEY, Terminal, safe_text

Clock = Callable[[], datetime]

TOPIC_KEYS = {str((i + 1) % 10): topic for i, topic in enumerate(TOPICS)}  # 1..9, then 0
SENTIMENT_KEYS = {"g": "positive", "n": "neutral", "b": "negative"}
assert tuple(SENTIMENT_KEYS.values()) == SENTIMENTS

_UNDO = "!undo"
_QUIT = "!quit"
_BACK = "!back"
_WRITE = "!write"
_SKIP = "!skip"


@dataclass(frozen=True)
class FlowEnd:
    finished: bool  # True: nothing left to do (all labeled, or the target was reached)
    total: int  # decisions on file when the flow ended (labels; for questions, answered)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(clock: Clock) -> str:
    return clock().isoformat(timespec="microseconds")


def _ask(term: Terminal, choices: Mapping[str, str]) -> str:
    """Read keys until one is in `choices`; returns its value. End of input is a quit."""
    while True:
        key = term.key()
        if key == EOF_KEY:
            return _QUIT
        value = choices.get(key.lower())
        if value is not None:
            return value


def _line(term: Terminal, prompt: str) -> str | None:
    raw = term.line(prompt)
    return None if raw is None else raw.strip()


# --- Tags -------------------------------------------------------------------------------------


def run_tags(
    term: Terminal,
    index: EligibleIndex,
    sample: Sequence[str],
    path: Path,
    clock: Clock = utc_now,
) -> FlowEnd:
    """Label the frozen tag sample, one topic and one sentiment per message (D85)."""
    labels = read_tags(path)
    topic_menu = "  ".join(f"{k} {t}" for k, t in TOPIC_KEYS.items())
    sentiment_menu = "  ".join(f"{k} {s}" for k, s in SENTIMENT_KEYS.items())
    topic_keys = {**TOPIC_KEYS, "u": _UNDO, "q": _QUIT}
    sentiment_keys = {**SENTIMENT_KEYS, "u": _BACK, "q": _QUIT}
    while True:
        done = {label.message_id for label in labels}
        pending = [m for m in sample if m not in done and m in index]
        if not pending:
            term.out(f"all done: {len(labels)} labeled")
            return FlowEnd(True, len(labels))
        message_id = pending[0]
        term.out("")
        term.out(f"[{len(labels) + 1} of {len(sample)}]  context first, then the message to label")
        for context in index.before(message_id, TAG_CONTEXT_MESSAGES):
            term.out(f"      {safe_text(context.text)}")
        term.out(f"  >>> {safe_text(index.get(message_id).text)}")
        term.out(f"topic:  {topic_menu}  |  u undo  q quit")
        choice = _ask(term, topic_keys)
        if choice == _QUIT:
            break
        if choice == _UNDO:
            if labels:
                labels.pop()
                write_tags(path, labels)
                term.out("undid the last label")
            else:
                term.out("nothing to undo")
            continue
        term.out(f"sentiment ({choice}):  {sentiment_menu}  |  u back  q quit")
        sentiment = _ask(term, sentiment_keys)
        if sentiment == _QUIT:
            break
        if sentiment == _BACK:
            continue
        labels.append(TagLabel(message_id, choice, sentiment, _stamp(clock)))
        write_tags(path, labels)
    term.out(f"saved: {len(labels)} labeled")
    return FlowEnd(False, len(labels))


# --- Questions --------------------------------------------------------------------------------


def _next_question_id(labels: Sequence[QuestionLabel]) -> str:
    numbers = [int(label.question_id[1:]) for label in labels]
    return f"q{max(numbers, default=0) + 1:03d}"


def _parse_numbers(text: str, limit: int) -> list[int] | None:
    """Context numbers typed as '1 3' or '1,3'. None if any entry is not a number in 1..limit."""
    picked: list[int] = []
    for part in text.replace(",", " ").split():
        if not part.isdecimal() or not 1 <= int(part) <= limit:
            return None
        picked.append(int(part))
    return picked


def _undo_last_question_action(
    labels: list[QuestionLabel], skips: list[Skip], questions_path: Path, skips_path: Path
) -> str | None:
    """Remove whichever of the last answered question or the last skip happened later."""
    last_label = datetime.fromisoformat(labels[-1].labeled_at) if labels else None
    last_skip = datetime.fromisoformat(skips[-1].skipped_at) if skips else None
    if last_label is None and last_skip is None:
        return None
    if last_skip is None or (last_label is not None and last_label >= last_skip):
        labels.pop()
        write_questions(questions_path, labels)
        return "question"
    skips.pop()
    write_skips(skips_path, skips)
    return "skip"


def run_questions(
    term: Terminal,
    index: EligibleIndex,
    candidates: Sequence[str],
    questions_path: Path,
    skips_path: Path,
    clock: Clock = utc_now,
    target: int = QUESTION_TARGET,
) -> FlowEnd:
    """Write retrieval questions from the frozen candidates until `target` are answered (D86).

    Ryan sees the candidate with up to QUESTION_CONTEXT_EACH_SIDE eligible neighbours on each
    side, the neighbours numbered. The gold is the candidate plus the neighbours he marks.
    """
    labels = read_questions(questions_path)
    skips = read_skips(skips_path)
    keys = {"w": _WRITE, "s": _SKIP, "u": _UNDO, "q": _QUIT}
    while True:
        if len(labels) >= target:
            term.out(f"target reached: {len(labels)} questions")
            return FlowEnd(True, len(labels))
        handled = {label.source_message_id for label in labels} | {s.message_id for s in skips}
        pending = [m for m in candidates if m not in handled and m in index]
        if not pending:
            term.out(f"no candidates left: {len(labels)} questions")
            return FlowEnd(True, len(labels))
        shown = index.get(pending[0])
        before = index.before(shown.message_id, QUESTION_CONTEXT_EACH_SIDE)
        after = index.after(shown.message_id, QUESTION_CONTEXT_EACH_SIDE)
        term.out("")
        term.out(
            f"[{len(labels)} of {target} questions, {len(skips)} skipped]  "
            "numbered lines are context; >>> is the message to ask about"
        )
        numbered: dict[int, Message] = {}
        for context in before:
            numbered[len(numbered) + 1] = context
            term.out(f"  {len(numbered)}. {safe_text(context.text)}")
        term.out(f"  >>> {safe_text(shown.text)}")
        for context in after:
            numbered[len(numbered) + 1] = context
            term.out(f"  {len(numbered)}. {safe_text(context.text)}")
        term.out("w write a question  s skip  u undo  q quit")
        action = _ask(term, keys)
        if action == _QUIT:
            break
        if action == _UNDO:
            undone = _undo_last_question_action(labels, skips, questions_path, skips_path)
            term.out(f"undid the last {undone}" if undone else "nothing to undo")
            continue
        if action == _SKIP:
            skips.append(Skip(shown.message_id, _stamp(clock)))
            write_skips(skips_path, skips)
            continue
        question = _line(term, "question (your own words; blank cancels): ")
        if question is None:
            break
        if not question:
            continue
        extra: list[int] = []
        if numbered:
            while True:
                reply = _line(term, "other numbered lines that also answer it (blank for none): ")
                if reply is None:
                    term.out(f"saved: {len(labels)} questions")
                    return FlowEnd(False, len(labels))
                parsed = _parse_numbers(reply, len(numbered))
                if parsed is not None:
                    extra = parsed
                    break
                term.out(f"use numbers from 1 to {len(numbered)}, e.g. 1 3")
        chosen = {shown.message_id} | {numbered[n].message_id for n in extra}
        gold = tuple(m.message_id for m in [*before, shown, *after] if m.message_id in chosen)
        labels.append(
            QuestionLabel(
                _next_question_id(labels), question, gold, shown.message_id, _stamp(clock)
            )
        )
        write_questions(questions_path, labels)
    term.out(f"saved: {len(labels)} questions")
    return FlowEnd(False, len(labels))
