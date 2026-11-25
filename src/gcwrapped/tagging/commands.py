"""`make tag`, `tag-eval`, `tag-compare`, `tag-promote` and `llm-model` (D89, D90, D92, D93).

Guards come before anything starts: the database must be one of ALLOWED_DATABASES (a SYNTHETIC=1
eval: gcw_dev only), the model must be registered, any label directory must resolve outside the
repo, and the llm base URL must be local. Only then are the db and llm compose services started and
the model called. One model is in memory at a time: every start names the model, and compose
recreates the llm service when it differs from the running one. Output is fixed lines only (see
`report`); a failure prints `<command>: failed at <step> (<exception type>)` and nothing else.
"""

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gcwrapped.config import LabelSettings, TaggingSettings, env_file_path
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import COMPOSE_TIMEOUT_SECONDS, require_docker, start_database
from gcwrapped.labeling.commands import resolve_label_dir
from gcwrapped.labeling.files import read_tags
from gcwrapped.labeling.schema import TAG_LABELS_FILE
from gcwrapped.labeling.source import load_eligible
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError
from gcwrapped.migrate import migrate
from gcwrapped.tagging import evaluation, model, report
from gcwrapped.tagging.llamacpp import check_local_url
from gcwrapped.tagging.prompt import prompt_version_for
from gcwrapped.tagging.provider import TagProvider, make_provider
from gcwrapped.tagging.runner import run_tagging

SYNTHETIC_DATABASE = "gcw_dev"  # a SYNTHETIC=1 eval: the fixtures' IDs exist nowhere else
SYNTHETIC_LABELS = REPO_ROOT / "tests" / "fixtures" / "eval" / "tag_labels.csv"
LLM_HEALTH_WAIT_SECONDS = COMPOSE_TIMEOUT_SECONDS

STEP_DATABASE = "database"
STEP_DOCKER = "docker"
STEP_URL = "llm url"
STEP_MODEL = "model"
STEP_ELIGIBLE = "eligible messages"
STEP_TAGGING = "tagging"
STEP_LABELS = "labels"
STEP_LABEL_DIR = "label directory"
STEP_PREDICTIONS = "predictions"
STEP_SCORING = "scoring"
STEP_VERDICT = "verdict"
STEP_PROMOTE = "promote"
STEP_RENDER = "render"


def llm_url() -> str:
    """GCW_LLM_URL if set, else the compose service as published on this machine."""
    settings = TaggingSettings()
    return settings.gcw_llm_url or f"http://127.0.0.1:{settings.gcw_llm_port}"


class ModelFileMissingError(Exception):
    """The registered model's file is not in GCW_MODEL_DIR: run make llm-model MODEL=<id>."""


def start_llm(model_id: str) -> None:
    """(Re)start only the llm service of the compose project ENV_FILE names, serving `model_id`.

    The file and --alias come from the environment, so one service serves either model; compose
    recreates the container when they differ from the running one. Waits for the health check.
    Output is captured, never shown. `make down` stops it.
    """
    spec = model.spec_for(model_id)
    if not (model.model_dir() / spec.file).is_file():
        raise ModelFileMissingError
    require_docker()
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            env_file_path(),
            "--profile",
            "llm",
            "up",
            "-d",
            "--wait",
            "llm",
        ],
        check=True,
        capture_output=True,
        timeout=LLM_HEALTH_WAIT_SECONDS,
        env={**os.environ, "GCW_LLM_MODEL_FILE": spec.file, "GCW_LLM_MODEL_ALIAS": model_id},
    )


StartDb = Callable[[], None] | None
StartLlm = Callable[[str], None] | None


def _provider(model_id: str) -> TagProvider:
    kwargs = model.spec_for(model_id).chat_template_kwargs
    return make_provider(
        "llamacpp", llm_url=llm_url(), model_id=model_id, chat_template_kwargs=kwargs
    )


def _version(model_id: str) -> str:
    """The prompt version of this model's requests, including its template setting (D98)."""
    return prompt_version_for(model.spec_for(model_id).chat_template_kwargs)


def run_tag(
    limit: int | None = None,
    model_id: str | None = None,
    start_db: StartDb = start_database,
    start_llm: StartLlm = start_llm,
) -> int:
    """`make tag`: the promoted model unless `model_id` is given (D92). None = do not start it."""
    step = STEP_DATABASE
    try:
        url = database_url()
        database = database_name(url)
        if database not in ALLOWED_DATABASES:
            raise UnexpectedDatabaseError
        step = STEP_MODEL
        if model_id is not None:
            model.spec_for(model_id)
        step = STEP_URL
        check_local_url(llm_url())
        step = STEP_DOCKER
        if start_db is not None:
            start_db()  # the promoted model is read from the database, so it comes up first
        step = STEP_MODEL
        chosen = model_id or evaluation.promoted_model(url, model.MODEL_ID)
        model.spec_for(chosen)
        provider = _provider(chosen)
        step = STEP_DOCKER
        if start_llm is not None:
            start_llm(chosen)
        step = STEP_TAGGING
        result = run_tagging(url, provider, limit=limit, prompt_version=_version(chosen))
        step = STEP_RENDER
        lines = report.render_tag(database, result)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("tag", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


# --- eval, compare, promote (D92, D93) ---------------------------------------------------------


@dataclass(frozen=True)
class Target:
    url: str
    database: str
    synthetic: bool
    root: Path  # predictions/ and experiments/ go under this directory, outside the repo

    @property
    def source(self) -> str:
        return "synthetic" if self.synthetic else "real"

    @property
    def labels(self) -> Path:
        return SYNTHETIC_LABELS if self.synthetic else self.root / TAG_LABELS_FILE


def synthetic_dir() -> Path:
    """A temporary directory shared by the synthetic runs of one user, never GCW_LABEL_DIR."""
    path = Path(tempfile.gettempdir()) / f"gcw-synthetic-eval-{os.getuid()}"
    real = Path(os.path.realpath(path))
    if real == REPO_ROOT or REPO_ROOT in real.parents:  # a TMPDIR inside the repo: create nothing
        raise evaluation.OutsideRepoError
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise evaluation.OutsideRepoError
    return real


def check_database(synthetic: bool) -> tuple[str, str]:
    """(URL, name) of the target database, or UnexpectedDatabaseError. Connects to nothing."""
    url = database_url()
    database = database_name(url)
    allowed = {SYNTHETIC_DATABASE} if synthetic else ALLOWED_DATABASES
    if database not in allowed:
        raise UnexpectedDatabaseError
    return url, database


def resolve_root(synthetic: bool) -> Path:
    """Where predictions and verdicts go: the temporary directory, or GCW_LABEL_DIR (outside the
    repo, else LabelDirError). A synthetic run never touches GCW_LABEL_DIR."""
    return synthetic_dir() if synthetic else resolve_label_dir(LabelSettings().gcw_label_dir)


def _read_labels(path: Path) -> dict[str, tuple[str, str]]:
    labels = {t.message_id: (t.topic, t.sentiment) for t in read_tags(path)}
    if not labels:
        raise evaluation.NoScorableLabelsError
    return labels


def run_tag_eval(
    model_id: str,
    synthetic: bool = False,
    start_db: StartDb = start_database,
    start_llm: StartLlm = start_llm,
) -> int:
    """`make tag-eval MODEL=<id> [SYNTHETIC=1]`: run one model over the labels, store, score."""
    step = STEP_MODEL
    try:
        model.spec_for(model_id)
        step = STEP_DATABASE
        url, database = check_database(synthetic)
        step = STEP_LABEL_DIR
        target = Target(url, database, synthetic, resolve_root(synthetic))
        step = STEP_URL
        check_local_url(llm_url())
        provider = _provider(model_id)
        step = STEP_LABELS
        labels = _read_labels(target.labels)
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_ELIGIBLE
        index = load_eligible(target.url)
        ids, _ = evaluation.split_labels(labels, index)
        if not ids:
            raise evaluation.NoScorableLabelsError
        step = STEP_DOCKER
        if start_llm is not None:
            start_llm(model_id)
        step = STEP_TAGGING
        rows = evaluation.run_model(provider, index, ids)
        step = STEP_PREDICTIONS
        version = _version(model_id)
        evaluation.write_predictions(target.root, model_id, version, rows)
        step = STEP_SCORING
        result = evaluation.evaluate(labels, {r.message_id: r for r in rows}, index)
        step = STEP_RENDER
        lines = report.render_eval(target.database, target.source, model_id, version, result)
    except Exception as exc:
        print(report.failure_line("tag-eval", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


class PromptVersionMismatch(Exception):
    """The two arms of a tag-compare send different prompts (D98); not comparable."""


def run_tag_compare(
    a_id: str, b_id: str, synthetic: bool = False, start_db: StartDb = start_database
) -> int:
    """`make tag-compare A=<id> B=<id> [SYNTHETIC=1]`: the D93 verdict on stored predictions."""
    step = STEP_MODEL
    try:
        model.spec_for(a_id)
        model.spec_for(b_id)
        if a_id == b_id:
            raise ValueError("A and B must differ")
        step = STEP_DATABASE
        url, database = check_database(synthetic)
        step = STEP_LABEL_DIR
        target = Target(url, database, synthetic, resolve_root(synthetic))
        step = STEP_LABELS
        labels = _read_labels(target.labels)
        step = STEP_PREDICTIONS
        version = _version(a_id)
        if _version(b_id) != version:
            raise PromptVersionMismatch
        rows_a = evaluation.read_predictions(target.root, a_id, version)
        rows_b = evaluation.read_predictions(target.root, b_id, version)
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_ELIGIBLE
        index = load_eligible(target.url)
        step = STEP_SCORING
        result = evaluation.compare(labels, rows_a, rows_b, index)
        step = STEP_RENDER
        lines = report.render_compare(target.database, target.source, version, a_id, b_id, result)
        step = STEP_VERDICT
        evaluation.write_verdict(target.root, a_id, b_id, version, result)
    except Exception as exc:
        print(report.failure_line("tag-compare", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def run_tag_promote(model_id: str, start_db: StartDb = start_database) -> int:
    """`make tag-promote MODEL=<id>`: refuses unless the latest verdict names it the winner."""
    step = STEP_MODEL
    try:
        model.spec_for(model_id)
        step = STEP_DATABASE
        url, database = check_database(synthetic=False)  # no synthetic promotion exists
        step = STEP_LABEL_DIR
        target = Target(url, database, False, resolve_root(synthetic=False))
        step = STEP_VERDICT
        if evaluation.latest_winner(target.root) != model_id:
            raise evaluation.NotWinnerError
        step = STEP_DOCKER
        if start_db is not None:
            start_db()
        step = STEP_PROMOTE
        migrate(target.url)  # the table is migration 005; consent-load and purge do the same
        previous = evaluation.promote(target.url, model_id)
        step = STEP_RENDER
        lines = report.render_promote(target.database, model_id, previous)
    except Exception as exc:
        print(report.failure_line("tag-promote", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def run_llm_model(model_id: str = model.MODEL_ID) -> int:
    step = STEP_MODEL
    try:
        spec = model.downloadable_spec(model_id)
        downloaded = model.ensure_model(model.model_dir(), spec)
    except Exception as exc:
        print(report.failure_line("llm-model", step, exc))
        return 1
    print(f"model id: {model_id}")
    print(f"model file: {spec.file}")
    print(f"model sha256: {spec.sha256}")
    print(f"model status: {'downloaded' if downloaded else 'already present'}")
    print("llm-model: ok")
    return 0
