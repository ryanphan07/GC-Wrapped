"""`make tag`, `make tag-bench` and `make llm-model` (D89, D90).

Guards come before anything starts: the database must be one of ALLOWED_DATABASES (tag-bench:
gcw_dev only, because it is a synthetic check), and the llm base URL must be local. Only then are
the db and llm compose services started and the model called. Output is fixed lines only (see
`report`); a failure prints `<command>: failed at <step> (<exception type>)` and nothing else.
"""

import csv
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from gcwrapped.config import TaggingSettings, env_file_path
from gcwrapped.db import database_name, database_url
from gcwrapped.docker import COMPOSE_TIMEOUT_SECONDS, require_docker
from gcwrapped.labeling.source import load_eligible
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.metrics import ALLOWED_DATABASES, UnexpectedDatabaseError
from gcwrapped.tagging import model, report
from gcwrapped.tagging.llamacpp import check_local_url
from gcwrapped.tagging.prompt import PROMPT_VERSION
from gcwrapped.tagging.provider import TagFailure, TagProvider, make_provider
from gcwrapped.tagging.runner import RunResult, run_tagging, tag_message

BENCH_DATABASE = "gcw_dev"
BENCH_LABELS = REPO_ROOT / "tests" / "fixtures" / "eval" / "tag_labels.csv"
LLM_HEALTH_WAIT_SECONDS = COMPOSE_TIMEOUT_SECONDS

STEP_DATABASE = "database"
STEP_DOCKER = "docker"
STEP_URL = "llm url"
STEP_MODEL = "model"
STEP_ELIGIBLE = "eligible messages"
STEP_TAGGING = "tagging"
STEP_LABELS = "labels"
STEP_RENDER = "render"


def llm_url() -> str:
    """GCW_LLM_URL if set, else the compose service as published on this machine."""
    settings = TaggingSettings()
    return settings.gcw_llm_url or f"http://127.0.0.1:{settings.gcw_llm_port}"


def start_services() -> None:
    """Start only db and llm of the compose project ENV_FILE names (the `llm` profile is explicit).

    Waits for both health checks. Output is captured, never shown. `make down` stops them.
    """
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
            "db",
            "llm",
        ],
        check=True,
        capture_output=True,
        timeout=LLM_HEALTH_WAIT_SECONDS,
    )


def _provider() -> TagProvider:
    return make_provider("llamacpp", llm_url=llm_url(), model_id=model.MODEL_ID)


def run_tag(limit: int | None = None, start: Callable[[], None] | None = start_services) -> int:
    step = STEP_DATABASE
    try:
        url = database_url()
        database = database_name(url)
        if database not in ALLOWED_DATABASES:
            raise UnexpectedDatabaseError
        step = STEP_URL
        check_local_url(llm_url())
        provider = _provider()
        step = STEP_DOCKER
        if start is not None:
            start()
        step = STEP_TAGGING
        result = run_tagging(url, provider, limit=limit)
        step = STEP_RENDER
        lines = report.render_tag(database, result)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("tag", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def read_bench_labels(path: Path = BENCH_LABELS) -> dict[str, tuple[str, str]]:
    """message_id -> (topic, sentiment) from the committed synthetic labeled set."""
    with path.open(newline="", encoding="utf-8") as handle:
        return {r["message_id"]: (r["topic"], r["sentiment"]) for r in csv.DictReader(handle)}


def bench(
    url: str, provider: TagProvider, labels: dict[str, tuple[str, str]]
) -> tuple[RunResult, int, float | None, float | None]:
    """Tag the labeled synthetic messages with the model, storing nothing.

    Returns the timing result, how many labeled messages were found among the eligible ones, and
    topic and sentiment accuracy over the ones that got a valid answer. A sanity check, not an eval.
    """
    index = load_eligible(url)
    result = RunResult(model_id=provider.model_id, prompt_version=PROMPT_VERSION)
    ids = [mid for ids in index.ids_by_thread().values() for mid in ids if mid in labels]
    result.eligible = len(index)
    topic_hits = sentiment_hits = 0
    started = time.perf_counter()
    for message_id in ids:
        began = time.perf_counter()
        try:
            tag = tag_message(provider, index, index.get(message_id))
        except TagFailure:
            result.failures += 1
        else:
            result.count(tag)
            gold_topic, gold_sentiment = labels[message_id]
            topic_hits += tag.topic == gold_topic
            sentiment_hits += tag.sentiment == gold_sentiment
        finally:
            result.timings.seconds.append(time.perf_counter() - began)
    result.seconds = time.perf_counter() - started
    answered = result.tagged
    return (
        result,
        len(ids),
        topic_hits / answered if answered else None,
        sentiment_hits / answered if answered else None,
    )


def run_bench(start: Callable[[], None] = start_services) -> int:
    step = STEP_DATABASE
    try:
        url = database_url()
        database = database_name(url)
        if database != BENCH_DATABASE:  # synthetic only: the fixtures' IDs exist nowhere else
            raise UnexpectedDatabaseError
        step = STEP_URL
        check_local_url(llm_url())
        provider = _provider()
        step = STEP_LABELS
        labels = read_bench_labels()
        step = STEP_DOCKER
        start()
        step = STEP_TAGGING
        result, benchmarked, topic_acc, sentiment_acc = bench(url, provider, labels)
        step = STEP_RENDER
        lines = report.render_bench(database, result, benchmarked, topic_acc, sentiment_acc)
    except Exception as exc:
        print(report.failure_line("tag-bench", step, exc))
        return 1
    print(*lines, sep="\n")
    return 0


def run_llm_model() -> int:
    step = STEP_MODEL
    try:
        downloaded = model.ensure_model(model.model_dir())
    except Exception as exc:
        print(report.failure_line("llm-model", step, exc))
        return 1
    print(f"model file: {model.MODEL_FILE}")
    print(f"model sha256: {model.MODEL_SHA256}")
    print(f"model status: {'downloaded' if downloaded else 'already present'}")
    print("llm-model: ok")
    return 0
