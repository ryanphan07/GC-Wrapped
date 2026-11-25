"""`make ask` and `make answer-eval` (D111-D113).

The retrieval commands' guards, in the same order: the database must be one of ALLOWED_DATABASES
(SYNTHETIC=1: gcw_dev only), a label directory must resolve outside the repo, and both model URLs
must be local. Only then are the db service and, for the promoted tag model (D92), the llm and
embed services started. A failure prints `<command>: failed at <step> (<exception type>)`.

answer-eval prints fixed lines only. ask also prints each answer and its cited messages: to the
terminal, on purpose, and nowhere else. It writes no file and logs nothing, and it reads questions
from a prompt, because argv is saved in shell history and shown in the process list.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from gcwrapped.answers import evaluation, report
from gcwrapped.answers.pipeline import (
    ABSTAINED,
    ANSWERED,
    INVALID,
    OUTCOMES,
    REQUEST_TIMEOUT_SECONDS,
    Answer,
    AnswerPipeline,
)
from gcwrapped.answers.prompt import DATE_FORMAT, SOURCE_LINE_FORMAT, prompt_version_for
from gcwrapped.docker import start_database
from gcwrapped.labeling.files import read_questions
from gcwrapped.labeling.source import load_eligible
from gcwrapped.labeling.terminal import StdTerminal, Terminal, safe_text
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval.search import Retriever
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import LlamaCppChat, check_local_url
from gcwrapped.tagging.runner import Timings

STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_EMBED_URL = "embed url"
STEP_LLM_URL = "llm url"
STEP_QUESTIONS = "questions"
STEP_DOCKER = "docker"
STEP_MODEL = "model"
STEP_RETRIEVAL = "retrieval"
STEP_CORPUS = "corpus"
STEP_ANSWERING = "answering"
STEP_SCORING = "scoring"
STEP_PREDICTIONS = "predictions"
STEP_RENDER = "render"

PROMPT = "question (blank line to finish): "
NOT_IN_CHAT = "not in the chat"
INVALID_ANSWER = "answer failed: invalid model output"


@dataclass(frozen=True)
class Services:
    """What a command starts, in order. None = do not start that one (it is already running)."""

    db: Callable[[], None] | None = start_database
    llm: Callable[[str], None] | None = tagging.start_llm
    embed: Callable[[], None] | None = retrieval_commands.start_embed


def _chat_model(model_id: str) -> LlamaCppChat:
    return LlamaCppChat(
        tagging.llm_url(),
        model_id=model_id,
        timeout=REQUEST_TIMEOUT_SECONDS,
        chat_template_kwargs=model.spec_for(model_id).chat_template_kwargs,
    )


def run_answer_eval(synthetic: bool = False, services: Services | None = None) -> int:
    """`make answer-eval [SYNTHETIC=1]`: answer each scorable question once, store IDs and
    numbers, print the D112 lines. Refused unless every eligible message is searchable (D109)."""
    services = services or Services()
    step = STEP_DATABASE
    try:
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
        version = prompt_version_for(model.spec_for(model_id).chat_template_kwargs)
        chat = _chat_model(model_id)
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
            pipeline = AnswerPipeline(conn, retriever, chat, method)
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


def answer_lines(answer: Answer) -> list[str]:
    """What the terminal shows for one answer. Message and model text are untrusted, so control
    characters are replaced (an escape sequence could rewrite the screen)."""
    if answer.outcome == ABSTAINED:
        return [NOT_IN_CHAT]
    if answer.outcome == INVALID:
        return [INVALID_ANSWER]
    cited = (
        SOURCE_LINE_FORMAT.format(
            number=source.number,
            sender=safe_text(source.message.sender),
            sent=source.message.sent_local.strftime(DATE_FORMAT),
            text=safe_text(source.message.text),
        )
        for source in sorted(answer.cited, key=lambda s: s.number)
    )
    return [safe_text(answer.text), "", *cited]


def ask_loop(
    terminal: Terminal,
    answer: Callable[[str], Answer],
    clock: Callable[[], float] = time.perf_counter,
) -> list[str]:
    """Ask until a blank line, end of input or Ctrl-C; return the summary lines."""
    counts = dict.fromkeys(OUTCOMES, 0)
    timings = Timings([])
    try:
        while (question := terminal.line(PROMPT)) is not None and question.strip():
            began = clock()
            found = answer(question.strip())
            timings.seconds.append(clock() - began)
            counts[found.outcome] += 1
            for line in ("", *answer_lines(found), ""):
                terminal.out(line)
    except KeyboardInterrupt:
        pass  # the usual way to stop a slow answer: still report the finished ones
    return report.render_ask(counts[ANSWERED], counts[ABSTAINED], counts[INVALID], timings)


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
        chat = _chat_model(model_id)
        step = STEP_DOCKER
        if services.llm is not None:
            services.llm(model_id)
        if services.embed is not None:
            services.embed()
        step = STEP_RETRIEVAL
        with psycopg.connect(url, autocommit=True) as conn:  # each question sees consent as it is
            retriever = Retriever(conn, embedder)
            pipeline = AnswerPipeline(conn, retriever, chat, retrieval.default_method(conn))
            step = STEP_ANSWERING
            summary = ask_loop(terminal, pipeline.answer)
    except Exception as exc:
        print(report.failure_line("ask", step, exc))
        return 1
    for line in ("", *summary):  # a blank line first: the prompt may not have ended its own
        terminal.out(line)
    return 0
