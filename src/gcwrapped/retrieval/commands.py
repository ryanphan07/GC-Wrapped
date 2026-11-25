"""`make embed`, `retrieval-eval` and `retrieval-compare` (D102-D104).

The tagging commands' guards, in the same order: the database must be one of ALLOWED_DATABASES (a
SYNTHETIC=1 run: gcw_dev only), a label directory must resolve outside the repo, and the embedding
URL must be local. Only then are the db and embed compose services started. Output is fixed lines
only (see `report`); a failure prints `<command>: failed at <step> (<exception type>)`.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import psycopg

from gcwrapped.config import EmbeddingSettings, env_file_path
from gcwrapped.docker import COMPOSE_TIMEOUT_SECONDS, require_docker, start_database
from gcwrapped.labeling.files import read_questions
from gcwrapped.labeling.schema import QUESTIONS_FILE
from gcwrapped.labeling.source import load_eligible
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.migrate import migrate
from gcwrapped.retrieval import evaluation, report
from gcwrapped.retrieval.client import Embedder, LlamaCppEmbedder
from gcwrapped.retrieval.embed import run_embed as embed_job
from gcwrapped.retrieval.search import Retriever
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import check_local_url

SYNTHETIC_QUESTIONS = REPO_ROOT / "tests" / "fixtures" / "eval" / QUESTIONS_FILE

STEP_METHOD = "method"
STEP_DATABASE = "database"
STEP_LABEL_DIR = "label directory"
STEP_URL = "embed url"
STEP_QUESTIONS = "questions"
STEP_PREDICTIONS = "predictions"
STEP_DOCKER = "docker"
STEP_EMBEDDING = "embedding"
STEP_RETRIEVAL = "retrieval"
STEP_SCORING = "scoring"
STEP_RENDER = "render"

StartDb = Callable[[], None] | None
StartEmbed = Callable[[], None] | None


def embed_url() -> str:
    """GCW_EMBED_URL if set, else the compose service as published on this machine."""
    settings = EmbeddingSettings()
    return settings.gcw_embed_url or f"http://127.0.0.1:{settings.gcw_embed_port}"


def start_embed() -> None:
    """Start only the embed service of the compose project ENV_FILE names, and wait for it to be
    healthy. Output is captured, never shown. `make down` does not stop profile services."""
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


def _embedder() -> Embedder:
    return LlamaCppEmbedder(embed_url(), model_id=model.EMBED_MODEL_ID)


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
        embedder = _embedder()
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        if start_service is not None:
            start_service()
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


def _questions_path(synthetic: bool, root: Path) -> Path:
    return SYNTHETIC_QUESTIONS if synthetic else root / QUESTIONS_FILE


def run_retrieval_eval(
    method: str,
    synthetic: bool = False,
    start_db: StartDb = start_database,
    start_service: StartEmbed = start_embed,
) -> int:
    """`make retrieval-eval METHOD=vector|hybrid [SYNTHETIC=1]`: search each question, store the
    results (IDs only) and score them."""
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
        embedder = _embedder()
        step = STEP_QUESTIONS
        questions = read_questions(_questions_path(synthetic, root))
        if not questions:
            raise evaluation.NoScorableQuestionsError
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        if start_service is not None:
            start_service()
        step = STEP_RETRIEVAL
        eligible = load_eligible(url)
        with psycopg.connect(url, autocommit=True) as conn:
            retriever = Retriever(conn, embedder)
            kept, _ = evaluation.split_questions(questions, eligible, retriever.searchable_ids())
            search = retriever.vector if method == evaluation.VECTOR else retriever.hybrid
            rows = evaluation.run_method(search, kept)
        step = STEP_SCORING
        result = evaluation.score(rows, len(questions))
        step = STEP_PREDICTIONS
        evaluation.write_predictions(
            root, evaluation.Predictions(method, retriever.version, source, tuple(rows))
        )
        step = STEP_RENDER
        lines = report.render_eval(database, source, method, retriever.version, result)
    except Exception as exc:
        print(report.failure_line("retrieval-eval", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def run_retrieval_compare(
    a: str, b: str, synthetic: bool = False, start_db: StartDb = start_database
) -> int:
    """`make retrieval-compare A=vector B=hybrid [SYNTHETIC=1]`: the D104 rule on stored results."""
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
    except Exception as exc:
        print(report.failure_line("retrieval-compare", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0
