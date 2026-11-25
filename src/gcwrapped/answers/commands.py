"""`make ask`, `answer-eval`, `answer-compare`, `answer-promote`, `grade-answers` and
`grade-summary` (D111-D113, D120, D121).

The retrieval commands' guards, in the same order: the database must be one of ALLOWED_DATABASES
(SYNTHETIC=1: gcw_dev only), a label directory must resolve outside the repo, and both model URLs
must be local. Only then are the db service and, for the promoted tag model (D92), the llm and
embed services started. A failure prints `<command>: failed at <step> (<exception type>)`.

answer-eval, answer-compare, answer-promote and grade-summary print fixed lines only. ask also
prints each answer and its cited messages, or after an abstention the messages the model was shown
(D115): to the terminal, on purpose, and nowhere else. It writes no file and logs nothing, and it
reads questions from a prompt, because argv is saved in shell history and shown in the process
list. grade-answers prints each question, its answer and the cited messages the same way, and
writes only the grade, IDs and numbers (D120).
"""

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import psycopg

from gcwrapped.answers import comparison, evaluation, grades, report
from gcwrapped.answers.excerpts import Source
from gcwrapped.answers.pipeline import (
    ABSTAINED,
    ANSWERED,
    OUTCOMES,
    REQUEST_TIMEOUT_SECONDS,
    TIMED_OUT,
    Answer,
    AnswerPipeline,
)
from gcwrapped.answers.prompt import (
    DATE_FORMAT,
    SOURCE_LINE_FORMAT,
    VARIANT_A,
    VARIANT_B,
    VARIANTS,
    UnknownVariantError,
    prompt_version_for,
)
from gcwrapped.docker import start_database
from gcwrapped.labeling.files import read_questions
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.labeling.source import load_eligible
from gcwrapped.labeling.terminal import EOF_KEY, StdTerminal, Terminal, safe_text
from gcwrapped.migrate import migrate
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval.search import Retriever
from gcwrapped.retrieval.window import embed_version
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import LlamaCppChat, check_local_url
from gcwrapped.tagging.runner import Timings

STEP_COUNT = "count"
STEP_VARIANT = "variant"
STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_EMBED_URL = "embed url"
STEP_LLM_URL = "llm url"
STEP_QUESTIONS = "questions"
STEP_DOCKER = "docker"
STEP_MODEL = "model"
STEP_RETRIEVAL = "retrieval"
STEP_CORPUS = "corpus"
STEP_GRADES = "grades"
STEP_ANSWERING = "answering"
STEP_SCORING = "scoring"
STEP_PREDICTIONS = "predictions"
STEP_VERDICT = "verdict"
STEP_PROMOTE = "promote"
STEP_RENDER = "render"

PROMPT = "question (blank line to finish): "
NOT_IN_CHAT = "not in the chat"
INVALID_ANSWER = "answer failed: invalid model output"
TIMED_OUT_ANSWER = "answer failed: model timed out"
GRADING_HELP = (
    "grading: each grade is saved as you enter it; q quits and Ctrl-C keeps what is saved"
)
GRADE_PROMPT = "answer: c correct  p partial  w wrong  |  q quit"
SUPPORT_PROMPT = "do the cited messages support it?  y yes  n no  |  q quit"
GRADE_KEYS = {"c": grades.CORRECT, "p": grades.PARTIAL, "w": grades.WRONG}
SUPPORT_KEYS = dict(zip("yn", grades.SUPPORTED, strict=True))


@dataclass(frozen=True)
class Services:
    """What a command starts, in order. None = do not start that one (it is already running)."""

    db: Callable[[], None] | None = start_database
    llm: Callable[[str], None] | None = tagging.start_llm
    embed: Callable[[], None] | None = retrieval_commands.start_embed


def chat_model(model_id: str) -> LlamaCppChat:
    return LlamaCppChat(
        tagging.llm_url(),
        model_id=model_id,
        timeout=REQUEST_TIMEOUT_SECONDS,
        chat_template_kwargs=model.spec_for(model_id).chat_template_kwargs,
    )


def _prompt_version(model_id: str, variant: str) -> str:
    return prompt_version_for(model.spec_for(model_id).chat_template_kwargs, variant)


def run_answer_eval(
    synthetic: bool = False,
    services: Services | None = None,
    variant: str | None = None,
) -> int:
    """`make answer-eval [SYNTHETIC=1] [VARIANT=a|b]`: answer each scorable question once with
    that prompt variant (default: the promoted one), store IDs and numbers, print the D112 lines.
    Refused unless every eligible message is searchable (D109)."""
    services = services or Services()
    step = STEP_VARIANT
    try:
        if variant is not None and variant not in VARIANTS:
            raise UnknownVariantError
        step = STEP_DATABASE
        url, database = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        source = "synthetic" if synthetic else "real"
        step = STEP_EMBED_URL
        check_local_url(retrieval_commands.embed_url())
        embedder = retrieval_commands.local_embedder()
        step = STEP_LLM_URL
        check_local_url(tagging.llm_url())
        step = STEP_QUESTIONS
        questions = read_questions(retrieval_commands.questions_path(synthetic, root))
        if not questions:
            raise retrieval.NoScorableQuestionsError
        step = STEP_DOCKER
        if services.db is not None:
            services.db()  # the promoted model is read from the database, so it comes up first
        step = STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        variant = variant or comparison.promoted_variant(url)
        version = _prompt_version(model_id, variant)
        chat = chat_model(model_id)
        step = STEP_DOCKER
        if services.llm is not None:
            services.llm(model_id)
        if services.embed is not None:
            services.embed()
        step = STEP_RETRIEVAL
        eligible = load_eligible(url)
        with psycopg.connect(url, autocommit=True) as conn:
            retriever = Retriever(conn, embedder)
            method = retrieval.default_method(conn)
            searchable = retriever.searchable_ids()
            step = STEP_CORPUS
            retrieval_commands.require_complete_corpus(eligible, searchable)
            kept, _ = retrieval.split_questions(questions, eligible, searchable)
            step = STEP_ANSWERING
            pipeline = AnswerPipeline(conn, retriever, chat, method, variant=variant)
            rows = evaluation.run_questions(pipeline.answer, kept)
        step = STEP_SCORING
        result = evaluation.score(rows, len(questions))
        step = STEP_PREDICTIONS
        corpus = retrieval.corpus_hash(searchable)
        evaluation.write_results(
            root,
            model_id,
            version,
            evaluation.Results(retriever.version, corpus, method, tuple(rows)),
        )
        step = STEP_RENDER
        lines = report.render_eval(
            database, source, model_id, version, method, retriever.version, result
        )
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("answer-eval", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def _message_lines(sources: Iterable[Source]) -> list[str]:
    return [
        SOURCE_LINE_FORMAT.format(
            number=source.number,
            sender=safe_text(source.message.sender),
            sent=source.message.sent_local.strftime(DATE_FORMAT),
            text=safe_text(source.message.text),
        )
        for source in sources
    ]


def answer_lines(answer: Answer, *, show_context: bool = True) -> list[str]:
    """What the terminal shows for one answer: the text and its cited messages, or for an
    abstention "not in the chat" and, with `show_context`, the messages the model was shown, so a
    retrieval miss can be told from a cautious model (D115). Message and model text are untrusted,
    so control characters are replaced (an escape sequence could rewrite the screen)."""
    if answer.outcome == ABSTAINED:
        shown = _message_lines(answer.sources) if show_context else []
        return [NOT_IN_CHAT, "", *shown] if shown else [NOT_IN_CHAT]
    if answer.outcome == TIMED_OUT:
        return [TIMED_OUT_ANSWER]
    if answer.outcome == ANSWERED:
        cited = _message_lines(sorted(answer.cited, key=lambda s: s.number))
        return [safe_text(answer.text), "", *cited]
    return [INVALID_ANSWER]


def ask_loop(
    terminal: Terminal,
    answer: Callable[[str], Answer],
    clock: Callable[[], float] = time.perf_counter,
) -> list[str]:
    """Ask until a blank line, end of input or Ctrl-C; return the summary lines."""
    counts = dict.fromkeys(OUTCOMES, 0)
    timings = Timings([])
    prompt_tokens: list[int] = []
    try:
        while (question := terminal.line(PROMPT)) is not None and question.strip():
            began = clock()
            found = answer(question.strip())
            seconds = clock() - began
            timings.seconds.append(seconds)
            counts[found.outcome] += 1
            if found.prompt_tokens is not None:
                prompt_tokens.append(found.prompt_tokens)
            numbers = report.answer_line(len(found.sources), found.prompt_tokens, seconds)
            for line in ("", *answer_lines(found), "", numbers, ""):
                terminal.out(line)
    except KeyboardInterrupt:
        pass  # the usual way to stop a slow answer: still report the finished ones
    return report.render_ask(counts, timings, prompt_tokens)


def run_ask(
    synthetic: bool = False,
    terminal: Terminal | None = None,
    services: Services | None = None,
) -> int:
    """`make ask [SYNTHETIC=1]`: an interactive loop over questions about the chat."""
    services = services or Services()
    terminal = terminal or StdTerminal()
    step = STEP_DATABASE
    try:
        url, _ = tagging.check_database(synthetic)
        step = STEP_EMBED_URL
        check_local_url(retrieval_commands.embed_url())
        embedder = retrieval_commands.local_embedder()
        step = STEP_LLM_URL
        check_local_url(tagging.llm_url())
        step = STEP_DOCKER
        if services.db is not None:
            services.db()
        step = STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        variant = comparison.promoted_variant(url)
        chat = chat_model(model_id)
        step = STEP_DOCKER
        if services.llm is not None:
            services.llm(model_id)
        if services.embed is not None:
            services.embed()
        step = STEP_RETRIEVAL
        with psycopg.connect(url, autocommit=True) as conn:  # each question sees consent as it is
            retriever = Retriever(conn, embedder)
            pipeline = AnswerPipeline(
                conn, retriever, chat, retrieval.default_method(conn), variant=variant
            )
            step = STEP_ANSWERING
            summary = ask_loop(terminal, pipeline.answer)
    except Exception as exc:
        print(report.failure_line("ask", step, exc))
        return 1
    for line in ("", *summary):  # a blank line first: the prompt may not have ended its own
        terminal.out(line)
    return 0


def run_answer_compare(
    synthetic: bool = False,
    start_db: Callable[[], None] | None = start_database,
    on_verdict: Callable[[str], None] | None = None,
) -> int:
    """`make answer-compare [SYNTHETIC=1]`: the D121 rule on the stored A and B runs of the
    current model. `on_verdict` receives the winner once the verdict is recorded (eval-all)."""
    step = STEP_DATABASE
    try:
        url, _ = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        a_version = _prompt_version(model_id, VARIANT_A)
        b_version = _prompt_version(model_id, VARIANT_B)
        step = STEP_PREDICTIONS
        a = evaluation.read_results(root, model_id, a_version)
        b = evaluation.read_results(root, model_id, b_version)
        step = STEP_SCORING
        outcome = comparison.compare(a, b, load_eligible(url))
        step = STEP_RENDER
        lines = report.render_compare(outcome)
        step = STEP_VERDICT
        comparison.write_verdict(root, model_id, a_version, b_version, a, outcome)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("answer-compare", step, exc))
        return 1
    print(*lines, sep="\n")
    if on_verdict is not None:
        on_verdict(outcome.winner)
    return 0


def run_answer_promote(variant: str, start_db: Callable[[], None] | None = start_database) -> int:
    """`make answer-promote VARIANT=a|b`: refused unless the latest answer-compare verdict names
    the variant the winner and was made with the current model, prompt versions and embed version
    (D121)."""
    step = STEP_VARIANT
    try:
        if variant not in VARIANTS:
            raise UnknownVariantError
        step = STEP_DATABASE
        url, database = tagging.check_database(synthetic=False)  # no synthetic promotion exists
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic=False)
        step = STEP_VERDICT
        verdict = comparison.latest_verdict(root)
        if verdict.winner != variant:
            raise retrieval.NotWinnerError
        if verdict.embed_version != embed_version():
            raise retrieval.EmbedVersionMismatch
        step = STEP_DOCKER
        if start_db is not None:
            start_db()  # the current model is read from the database
        step = STEP_VERDICT
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        if verdict.model_id != model_id:
            raise comparison.ModelMismatch
        current = (_prompt_version(model_id, VARIANT_A), _prompt_version(model_id, VARIANT_B))
        if (verdict.a_prompt_version, verdict.b_prompt_version) != current:
            raise comparison.PromptVersionMismatch
        step = STEP_PROMOTE
        migrate(url)  # the table is migration 008
        previous = comparison.promote(url, variant)
        step = STEP_RENDER
        lines = report.render_promote(database, variant, previous)
    except Exception as exc:
        print(report.failure_line("answer-promote", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


# --- hand grading (D120) -----------------------------------------------------------------------


class InvalidCountError(ValueError):
    """N is not a positive number of answers."""


class GradingTerminal(Terminal, Protocol):
    def discard_input(self) -> None:
        """Drop keys typed before now, so a stray key cannot grade an unread answer."""


def _choose(terminal: Terminal, prompt: str, choices: Mapping[str, str]) -> str | None:
    """Prompt until a key in `choices` is pressed; None for q or end of input."""
    while True:
        terminal.out(prompt)
        key = terminal.key()
        if key == EOF_KEY or key.lower() == "q":
            return None
        value = choices.get(key.lower())
        if value is not None:
            return value


def grade_loop(
    terminal: GradingTerminal,
    answer: Callable[[str], Answer],
    pending: Sequence[QuestionLabel],
    save: Callable[[grades.GradeRow], None],
    clock: Callable[[], float] = time.perf_counter,
) -> None:
    """Answer and grade each pending question until done, q or Ctrl-C. `save` gets each row as
    soon as it is complete: after both keys for an answer, at once for any other outcome."""
    try:
        if pending:
            terminal.out(GRADING_HELP)
        for number, question in enumerate(pending, start=1):
            terminal.out("")
            terminal.out(f"[{number} of {len(pending)}] question: {safe_text(question.question)}")
            began = clock()
            found = answer(question.question)
            seconds = clock() - began
            for line in ("", *answer_lines(found, show_context=False), ""):
                terminal.out(line)
            grade = supported = ""
            if found.outcome == ANSWERED:
                terminal.discard_input()
                chosen = _choose(terminal, GRADE_PROMPT, GRADE_KEYS)
                if chosen is None:
                    return
                support = _choose(terminal, SUPPORT_PROMPT, SUPPORT_KEYS)
                if support is None:
                    return
                grade, supported = chosen, support
            cited = tuple(s.message.message_id for s in found.cited)
            save(
                grades.GradeRow(
                    question.question_id, found.outcome, grade, supported, cited, seconds
                )
            )
    except KeyboardInterrupt:
        pass  # the usual way to stop a slow answer: finished grades are already saved


def run_grade_answers(
    count: int = grades.DEFAULT_COUNT,
    synthetic: bool = False,
    terminal: GradingTerminal | None = None,
    services: Services | None = None,
) -> int:
    """`make grade-answers [N=50] [SYNTHETIC=1]`: grade the next N ungraded scorable questions,
    in the seeded order, with the promoted model, retrieval method and prompt variant. Refused
    unless every eligible message is searchable (D109)."""
    services = services or Services()
    terminal = terminal or StdTerminal()
    step = STEP_COUNT
    try:
        if count < 1:
            raise InvalidCountError
        step = STEP_DATABASE
        url, _ = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        step = STEP_EMBED_URL
        check_local_url(retrieval_commands.embed_url())
        embedder = retrieval_commands.local_embedder()
        step = STEP_LLM_URL
        check_local_url(tagging.llm_url())
        step = STEP_QUESTIONS
        questions = read_questions(retrieval_commands.questions_path(synthetic, root))
        if not questions:
            raise retrieval.NoScorableQuestionsError
        step = STEP_DOCKER
        if services.db is not None:
            services.db()
        step = STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        variant = comparison.promoted_variant(url)
        version = _prompt_version(model_id, variant)
        chat = chat_model(model_id)
        step = STEP_GRADES
        rows = grades.read_grades(root, model_id, version)  # before the slow starts
        step = STEP_DOCKER
        if services.llm is not None:
            services.llm(model_id)
        if services.embed is not None:
            services.embed()
        step = STEP_RETRIEVAL
        eligible = load_eligible(url)
        with psycopg.connect(url, autocommit=True) as conn:
            retriever = Retriever(conn, embedder)
            method = retrieval.default_method(conn)
            searchable = retriever.searchable_ids()
            step = STEP_CORPUS
            retrieval_commands.require_complete_corpus(eligible, searchable)
            kept, _ = retrieval.split_questions(questions, eligible, searchable)
            if not kept:
                raise retrieval.NoScorableQuestionsError
            done = {r.question_id for r in rows}
            ungraded = [q for q in grades.grading_order(kept) if q.question_id not in done]
            before = len(rows)

            def save(row: grades.GradeRow) -> None:
                rows.append(row)
                grades.write_grades(root, model_id, version, rows)

            step = STEP_ANSWERING
            pipeline = AnswerPipeline(conn, retriever, chat, method, variant=variant)
            grade_loop(terminal, pipeline.answer, ungraded[:count], save)
        step = STEP_RENDER
        graded_now = len(rows) - before
        lines = report.render_grading(graded_now, len(rows), len(ungraded) - graded_now)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("grade-answers", step, exc))
        return 1
    for line in ("", *lines):
        terminal.out(line)
    return 0


def run_grade_summary(
    synthetic: bool = False, start_db: Callable[[], None] | None = start_database
) -> int:
    """`make grade-summary [SYNTHETIC=1]`: the D120 counts and rates of the current model's and
    prompt variant's grades, with the current retrieval method."""
    step = STEP_DATABASE
    try:
        url, _ = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        with psycopg.connect(url, autocommit=True) as conn:
            variant = comparison.default_variant(conn)
            method = retrieval.default_method(conn)
        version = _prompt_version(model_id, variant)
        step = STEP_GRADES
        rows = grades.read_grades(root, model_id, version)
        step = STEP_SCORING
        summary = grades.summarize(rows)
        step = STEP_RENDER
        lines = report.render_summary(model_id, version, method, summary)
    except Exception as exc:
        print(report.failure_line("grade-summary", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0
