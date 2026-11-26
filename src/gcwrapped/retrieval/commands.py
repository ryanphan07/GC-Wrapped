"""`make embed`, `retrieval-eval`, `retrieval-compare` and `retrieval-promote` (D102-D104, D109,
D110).

The tagging commands' guards, in the same order: the database must be one of ALLOWED_DATABASES (a
SYNTHETIC=1 run: gcw_dev only), a label directory must resolve outside the repo, and the embedding
URL must be local. Only then are the db and embed compose services started. Output is fixed lines
only (see `report`); a failure prints `<command>: failed at <step> (<exception type>)`.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import psycopg

from gcwrapped import memory
from gcwrapped.config import EmbeddingSettings, env_file_path
from gcwrapped.docker import (
    COMPOSE_TIMEOUT_SECONDS,
    require_docker,
    require_no_other_stack,
    start_database,
)
from gcwrapped.labeling.files import read_questions
from gcwrapped.labeling.schema import QUESTIONS_FILE
from gcwrapped.labeling.source import EligibleIndex, load_eligible
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.migrate import migrate
from gcwrapped.retrieval import evaluation, report
from gcwrapped.retrieval.client import Embedder, LlamaCppEmbedder
from gcwrapped.retrieval.embed import run_embed as embed_job
from gcwrapped.retrieval.search import Retriever
from gcwrapped.retrieval.window import embed_version
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import check_local_url, require_served_model

SYNTHETIC_QUESTIONS = REPO_ROOT / "tests" / "fixtures" / "eval" / QUESTIONS_FILE

STEP_METHOD = "method"
STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_URL = "embed url"
STEP_QUESTIONS = "questions"
STEP_PREDICTIONS = "predictions"
STEP_DOCKER = "docker"
STEP_MODEL = "model"
STEP_MEMORY = "memory"
STEP_EMBEDDING = "embedding"
STEP_RETRIEVAL = "retrieval"
STEP_SCORING = "scoring"
STEP_CORPUS = "corpus"
STEP_VERDICT = "verdict"
STEP_PROMOTE = "promote"
STEP_RENDER = "render"

StartDb = Callable[[], None] | None
StartEmbed = Callable[[], None] | None


def embed_url() -> str:
    """GCW_EMBED_URL if set, else the compose service as published on this machine."""
    settings = EmbeddingSettings()
    return settings.gcw_embed_url or f"http://127.0.0.1:{settings.gcw_embed_port}"


def start_embed() -> None:
    """Start only the embed service of the compose project ENV_FILE names, and wait for it to be
    healthy. Output is captured, never shown. `make down` stops it."""
    if not (model.model_dir() / model.EMBED_MODEL_SPEC.file).is_file():
        raise tagging.ModelFileMissingError  # run make llm-model MODEL=<embedding model id>
    require_docker()
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            env_file_path(),
            "--profile",
            "embed",
            "up",
            "-d",
            "--wait",
            "embed",
        ],
        check=True,
        capture_output=True,
        timeout=COMPOSE_TIMEOUT_SECONDS,
    )


def local_embedder() -> Embedder:
    return LlamaCppEmbedder(embed_url(), model_id=model.EMBED_MODEL_ID)


def require_embed_model() -> None:
    require_served_model(embed_url(), model.EMBED_MODEL_ID)


def run_embed(
    synthetic: bool = False,
    start_db: StartDb = start_database,
    start_service: StartEmbed = start_embed,
) -> int:
    """`make embed [SYNTHETIC=1]`. None = do not start that service."""
    step = STEP_DATABASE
    try:
        url, database = tagging.check_database(synthetic)
        step = STEP_URL
        check_local_url(embed_url())
        embedder = local_embedder()
        step = STEP_DOCKER
        require_no_other_stack()
        if start_db is not None:
            start_db()
        if start_service is not None:
            start_service()
        step = STEP_MODEL
        require_embed_model()
        step = STEP_EMBEDDING
        migrate(url)  # the table is migration 006; consent-load and purge do the same
        result = embed_job(url, embedder)
        step = STEP_RENDER
        lines = report.render_embed(database, result)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("embed", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def questions_path(synthetic: bool, root: Path) -> Path:
    return SYNTHETIC_QUESTIONS if synthetic else root / QUESTIONS_FILE


def require_complete_corpus(eligible: EligibleIndex, searchable: frozenset[str]) -> None:
    """The D109 guard. A refusal prints the count first, so the next step is plain (make embed)."""
    ids = (m for thread in eligible.ids_by_thread().values() for m in thread)
    missing = evaluation.unembedded(ids, searchable)
    if missing:
        print(report.unembedded_line(missing))
        raise evaluation.IncompleteCorpusError


def run_retrieval_eval(
    method: str,
    synthetic: bool = False,
    start_db: StartDb = start_database,
    start_service: StartEmbed = start_embed,
) -> int:
    """`make retrieval-eval METHOD=vector|hybrid [SYNTHETIC=1]`: search each question, store the
    results (IDs only) and score them. Refused, storing nothing, unless every eligible message is
    searchable (D109)."""
    step = STEP_METHOD
    try:
        if method not in evaluation.METHODS:
            raise evaluation.UnknownMethodError
        step = STEP_DATABASE
        url, database = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        source = "synthetic" if synthetic else "real"
        step = STEP_URL
        check_local_url(embed_url())
        embedder = local_embedder()
        step = STEP_QUESTIONS
        questions = read_questions(questions_path(synthetic, root))
        if not questions:
            raise evaluation.NoScorableQuestionsError
        step = STEP_DOCKER
        require_no_other_stack()
        if start_db is not None:
            start_db()
        if start_service is not None:
            start_service()
        step = STEP_MODEL
        require_embed_model()
        step = STEP_RETRIEVAL
        eligible = load_eligible(url)
        with psycopg.connect(url, autocommit=True) as conn:
            retriever = Retriever(conn, embedder)
            searchable = retriever.searchable_ids()
            step = STEP_CORPUS
            require_complete_corpus(eligible, searchable)
            step = STEP_RETRIEVAL
            kept, _ = evaluation.split_questions(questions, eligible, searchable)
            search = retriever.vector if method == evaluation.VECTOR else retriever.hybrid
            step = STEP_MEMORY
            before = memory.start_pass()
            step = STEP_RETRIEVAL
            rows = evaluation.run_method(search, kept)
            step = STEP_MEMORY
            memory.end_pass(before)
        step = STEP_SCORING
        result = evaluation.score(rows, len(questions))
        step = STEP_PREDICTIONS
        corpus = evaluation.corpus_hash(searchable)
        evaluation.write_predictions(
            root, evaluation.Predictions(method, retriever.version, source, corpus, tuple(rows))
        )
        step = STEP_RENDER
        lines = report.render_eval(database, source, method, retriever.version, result)
    except Exception as exc:
        print(report.failure_line("retrieval-eval", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def run_retrieval_compare(
    a: str,
    b: str,
    synthetic: bool = False,
    start_db: StartDb = start_database,
    on_verdict: Callable[[str], None] | None = None,
) -> int:
    """`make retrieval-compare A=vector B=hybrid [SYNTHETIC=1]`: the D104 rule on stored results.
    `on_verdict` receives the winning method once the verdict is recorded (eval-all)."""
    step = STEP_METHOD
    try:
        if (a, b) != (evaluation.VECTOR, evaluation.HYBRID):  # D104 names baseline and variant
            raise evaluation.UnknownMethodError
        step = STEP_DATABASE
        url, _ = tagging.check_database(synthetic)
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic)
        step = STEP_PREDICTIONS
        predictions_a = evaluation.read_predictions(root, a)
        predictions_b = evaluation.read_predictions(root, b)
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_SCORING
        result = evaluation.compare(predictions_a, predictions_b, load_eligible(url))
        step = STEP_RENDER
        lines = report.render_compare(a, b, result)
        step = STEP_VERDICT
        evaluation.write_verdict(root, predictions_a, result)
    except Exception as exc:
        print(report.failure_line("retrieval-compare", step, exc))
        return 1
    print(*lines, sep="\n")
    if on_verdict is not None:
        on_verdict(b if result.b_wins else a)
    return 0


def run_retrieval_promote(method: str, start_db: StartDb = start_database) -> int:
    """`make retrieval-promote METHOD=vector|hybrid`: refused unless the latest retrieval-compare
    verdict names the method the winner and was made on the current embed version (D110)."""
    step = STEP_METHOD
    try:
        if method not in evaluation.METHODS:
            raise evaluation.UnknownMethodError
        step = STEP_DATABASE
        url, database = tagging.check_database(synthetic=False)  # no synthetic promotion exists
        step = STEP_LABEL_DIR
        root = tagging.resolve_root(synthetic=False)
        step = STEP_VERDICT
        winner, version = evaluation.latest_verdict(root)
        if winner != method:
            raise evaluation.NotWinnerError
        if version != embed_version():
            raise evaluation.EmbedVersionMismatch
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_PROMOTE
        migrate(url)  # the table is migration 007
        previous = evaluation.promote(url, method)
        step = STEP_RENDER
        lines = report.render_promote(database, method, previous)
    except Exception as exc:
        print(report.failure_line("retrieval-promote", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0
