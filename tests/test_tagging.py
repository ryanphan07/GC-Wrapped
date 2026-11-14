"""Tagging without a database (D89, D90): prompt, schema, client, guard, stub, model download."""

import ast
import hashlib
import http.server
import re
import socket
import threading
import urllib.request
from pathlib import Path

import pytest

from gcwrapped.labeling.schema import SENTIMENTS, TAG_CONTEXT_MESSAGES, TOPICS
from gcwrapped.labeling.source import EligibleIndex, Message
from gcwrapped.tagging import model, prompt
from gcwrapped.tagging.llamacpp import (
    LlamaCppProvider,
    NonLocalUrlError,
    check_local_url,
    parse_tag,
)
from gcwrapped.tagging.provider import (
    ExternalCallBlocked,
    HostedProvider,
    ProviderUnavailable,
    TagFailure,
    make_provider,
)
from gcwrapped.tagging.runner import Timings
from tagging_stub import StubLlm

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "gcwrapped" / "tagging"


def _index() -> EligibleIndex:
    texts = ["one SENTINELA", "two SENTINELB", "three SENTINELC", "four SENTINELD"]
    return EligibleIndex(Message(f"k{n}", "t1", text) for n, text in enumerate(texts))


# --- schema and prompt ---------------------------------------------------------------------------


def test_response_schema_enums_are_the_labeling_schema() -> None:
    props = prompt.RESPONSE_SCHEMA["properties"]
    assert props["topic"]["enum"] == list(TOPICS)
    assert props["sentiment"]["enum"] == list(SENTIMENTS)
    assert prompt.RESPONSE_SCHEMA["required"] == ["topic", "sentiment"]
    assert prompt.RESPONSE_SCHEMA["additionalProperties"] is False


def test_migration_and_dbt_enums_equal_the_labeling_schema() -> None:
    def quoted(text: str) -> list[str]:
        return re.findall(r"'([a-z_]+)'", text)

    sql = (REPO / "migrations" / "004_enrich_message_tags.sql").read_text()
    topic_check = re.search(r"topic\s+text NOT NULL CHECK \(topic IN \((.*?)\)\)", sql, re.S)
    sentiment_check = re.search(r"CHECK \(sentiment IN \((.*?)\)\)", sql, re.S)
    assert topic_check and sentiment_check
    assert quoted(topic_check.group(1)) == list(TOPICS)
    assert quoted(sentiment_check.group(1)) == list(SENTIMENTS)
    for path in (
        REPO / "dbt/models/staging/enrich/_enrich__models.yml",
        REPO / "dbt/models/marts/_marts.yml",
    ):
        text = path.read_text()
        assert f"values: [{', '.join(TOPICS)}]" in text
        assert f"values: [{', '.join(SENTIMENTS)}]" in text


def test_system_prompt_defines_every_topic_and_sentiment() -> None:
    assert set(prompt.TOPIC_DEFINITIONS) == set(TOPICS)
    assert set(prompt.SENTIMENT_DEFINITIONS) == set(SENTIMENTS)
    for name in (*TOPICS, *SENTIMENTS):
        assert f"- {name}:" in prompt.SYSTEM_PROMPT


def test_user_content_is_the_labelers_context_and_no_sender_names() -> None:
    index = _index()
    system, user = prompt.messages_for(index, index.get("k3"))
    assert system == prompt.SYSTEM_PROMPT
    # The same neighbours the labeling flow shows (index.before with TAG_CONTEXT_MESSAGES).
    shown = [m.text for m in index.before("k3", TAG_CONTEXT_MESSAGES)]
    assert shown == ["two SENTINELB", "three SENTINELC"]
    assert user == (
        "Earlier messages (context only):\n- two SENTINELB\n- three SENTINELC\n\n"
        "Message to label:\nfour SENTINELD"
    )
    assert "one SENTINELA" not in user  # older than the context window


def test_first_message_has_no_context_section() -> None:
    index = _index()
    _, user = prompt.messages_for(index, index.get("k0"))
    assert user == "Message to label:\none SENTINELA"


def test_the_system_prefix_is_identical_across_requests_and_the_version_is_a_hash() -> None:
    index = _index()
    assert (
        prompt.messages_for(index, index.get("k1"))[0]
        == prompt.messages_for(index, index.get("k3"))[0]
    )
    assert re.fullmatch(r"[0-9a-f]{12}", prompt.PROMPT_VERSION)


# --- the URL guard and the client ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8081",
        "http://127.0.0.1:8081/",
        "http://llm:8080",
        "http://LOCALHOST:8081",
    ],
)
def test_local_urls_are_accepted(url: str) -> None:
    assert check_local_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://example.com:8081",
        "http://localhost.evil.com:8081",
        "http://127.0.0.1.evil.com",
        "http://evil.com@127.0.0.1:8081",
        "http://127.0.0.1@evil.com",
        "http://[::1]:8081",
        "http://0.0.0.0:8081",
        "http://192.168.1.5:8081",
        "https://localhost:8081",
        "ftp://localhost",
        "localhost:8081",
        "http://localhost:99999",
        "http://localhost:8081?x=1",
        "",
    ],
)
def test_non_local_urls_are_refused(url: str) -> None:
    with pytest.raises(NonLocalUrlError) as raised:
        check_local_url(url)
    assert url == "" or url not in str(raised.value)  # the message never echoes the URL


def test_the_provider_refuses_a_non_local_url_before_any_request() -> None:
    with pytest.raises(NonLocalUrlError):
        LlamaCppProvider("http://example.com", model_id="m")


def test_a_valid_answer_is_returned_and_the_request_carries_the_schema() -> None:
    with StubLlm() as stub:
        tag = LlamaCppProvider(stub.url, model_id="m1").tag("SYS", "USER")
    assert tag.topic in TOPICS and tag.sentiment in SENTIMENTS
    (body,) = stub.requests
    assert body["model"] == "m1" and body["temperature"] == 0 and body["cache_prompt"] is True
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["schema"] == prompt.RESPONSE_SCHEMA


def test_an_invalid_answer_is_retried_once_then_succeeds() -> None:
    with StubLlm(mode="invalid_once") as stub:
        tag = LlamaCppProvider(stub.url, model_id="m").tag("S", "U")
    assert tag.topic in TOPICS
    assert len(stub.requests) == 2


def test_an_invalid_answer_twice_is_a_failure_after_exactly_two_requests() -> None:
    with StubLlm(mode="invalid") as stub, pytest.raises(TagFailure):
        LlamaCppProvider(stub.url, model_id="m").tag("S", "U")
    assert len(stub.requests) == 2


def test_an_http_error_is_a_failure_after_the_retry() -> None:
    with StubLlm(mode="error500") as stub, pytest.raises(TagFailure):
        LlamaCppProvider(stub.url, model_id="m").tag("S", "U")
    assert len(stub.requests) == 2


def test_nothing_listening_means_unavailable_not_a_per_message_failure() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # closed again on exit: nothing listens here
    with pytest.raises(ProviderUnavailable):
        LlamaCppProvider(f"http://127.0.0.1:{port}", model_id="m").tag("S", "U")


def test_a_redirect_is_not_followed() -> None:
    with StubLlm() as elsewhere, StubLlm(mode="redirect", redirect_to=elsewhere.url) as stub:
        with pytest.raises(TagFailure):
            LlamaCppProvider(stub.url, model_id="m").tag("S", "U")
        assert elsewhere.requests == []


def test_proxy_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{dead}")
    with StubLlm() as stub:
        LlamaCppProvider(stub.url, model_id="m").tag("S", "U")
        assert len(stub.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": '{"topic": "food"}'}}]},
        {"choices": [{"message": {"content": '{"topic": "food", "sentiment": "meh"}'}}]},
        {"choices": [{"message": {"content": '{"topic": "x", "sentiment": "positive"}'}}]},
        {"choices": [{"message": {"content": '{"topic":"food","sentiment":"positive","a":1}'}}]},
        {"choices": [{"message": {"content": "[1]"}}]},
    ],
)
def test_parse_tag_rejects_anything_off_schema(body: object) -> None:
    with pytest.raises(TagFailure):
        parse_tag(body)


# --- the hosted stub -----------------------------------------------------------------------------


def test_the_hosted_stub_always_raises() -> None:
    with pytest.raises(ExternalCallBlocked):
        HostedProvider().tag("S", "U")
    with pytest.raises(ExternalCallBlocked):
        make_provider("hosted", llm_url="http://localhost:1", model_id="m").tag("S", "U")


def test_the_hosted_stub_cannot_reach_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network touched")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    with pytest.raises(ExternalCallBlocked):
        make_provider("hosted", llm_url="http://example.com", model_id="m").tag("S", "U")


def test_provider_module_has_no_network_imports_and_only_the_client_module_has_any() -> None:
    network = {"socket", "http", "urllib", "requests", "httpx", "ssl", "aiohttp", "ftplib"}
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        if path.name not in {"llamacpp.py", "model.py"}:
            assert not imported & network, path.name  # model.py downloads a public file (D90)


def test_unknown_provider_kind_is_an_error() -> None:
    with pytest.raises(ValueError):
        make_provider("openai", llm_url="http://localhost:1", model_id="m")


# --- timings -------------------------------------------------------------------------------------


def test_percentiles_are_nearest_rank() -> None:
    t = Timings([float(n) for n in range(1, 101)])
    assert t.percentile(50) == 50.0 and t.percentile(95) == 95.0
    assert Timings([2.0]).percentile(95) == 2.0
    assert Timings().percentile(50) is None


# --- the model download --------------------------------------------------------------------------


class _Serve:
    """Serves one in-memory file on 127.0.0.1 and counts requests."""

    def __init__(self, data: bytes) -> None:
        outer = self
        self.hits = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                outer.hits += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/m.gguf"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_model_download_checks_the_hash_and_is_idempotent(tmp_path: Path) -> None:
    data = b"not a model" * 1000
    server = _Serve(data)
    try:
        spec = model.ModelSpec("m.gguf", hashlib.sha256(data).hexdigest(), server.url)
        assert model.ensure_model(tmp_path, spec) is True
        assert (tmp_path / "m.gguf").read_bytes() == data
        assert model.ensure_model(tmp_path, spec) is False
        assert server.hits == 1  # the second call did not download again
        assert [p.name for p in tmp_path.iterdir()] == ["m.gguf"]
    finally:
        server.close()


def test_a_wrong_download_never_gets_its_final_name(tmp_path: Path) -> None:
    server = _Serve(b"tampered")
    try:
        spec = model.ModelSpec("m.gguf", hashlib.sha256(b"expected").hexdigest(), server.url)
        with pytest.raises(model.ChecksumMismatchError):
            model.ensure_model(tmp_path, spec)
        assert list(tmp_path.iterdir()) == []  # no final file, no leftover .part
    finally:
        server.close()


def test_an_existing_file_with_the_wrong_hash_is_an_error_and_left_alone(tmp_path: Path) -> None:
    (tmp_path / "m.gguf").write_bytes(b"old")
    spec = model.ModelSpec("m.gguf", hashlib.sha256(b"new").hexdigest(), "https://example.com/m")
    with pytest.raises(model.ChecksumMismatchError):
        model.ensure_model(tmp_path, spec)
    assert (tmp_path / "m.gguf").read_bytes() == b"old"


def test_plain_http_model_urls_are_refused(tmp_path: Path) -> None:
    spec = model.ModelSpec("m.gguf", "0" * 64, "http://example.com/m.gguf")
    with pytest.raises(ValueError):
        model.ensure_model(tmp_path, spec)


def test_the_pinned_model_is_the_one_in_the_decision() -> None:
    assert model.MODEL_FILE == "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    assert re.fullmatch(r"[0-9a-f]{64}", model.MODEL_SHA256)
    assert model.MODEL_URL.startswith("https://") and model.MODEL_FILE in model.MODEL_URL
    assert re.search(r"/resolve/[0-9a-f]{40}/", model.MODEL_URL)  # an exact commit, not "main"


# --- compose and Makefile ------------------------------------------------------------------------


def _llm_service() -> str:
    text = (REPO / "docker-compose.yml").read_text()
    match = re.search(r"^  llm:\n(.*?)(?=^\S|^  \w+:\n)", text, re.S | re.M)
    assert match
    return match.group(1)


def test_llm_service_is_pinned_local_only_and_leaks_nothing() -> None:
    svc = _llm_service()
    assert re.search(r"image: ghcr\.io/ggml-org/llama\.cpp:server@sha256:[0-9a-f]{64}", svc)
    assert re.search(r'- "127\.0\.0\.1:\$\{GCW_LLM_PORT:-8081\}:8080"', svc)
    assert ":/models:ro" in svc
    assert 'profiles: ["llm"]' in svc
    for flag, value in (("--ctx-size", "2048"), ("--parallel", "1"), ("--threads", "8")):
        assert re.search(rf"- {flag}\n\s+- \"{value}\"", svc), flag
    assert "- --no-slots" in svc and "- --no-webui" in svc
    for forbidden in ("--props", "--metrics", "--verbose", "--log-verbose", "-lv", "--api-key"):
        assert forbidden not in svc
    assert "healthcheck:" in svc
    assert f"${{GCW_LLM_MODEL_FILE:-{model.MODEL_FILE}}}" in svc
    assert model.MODEL_ID in svc  # --alias, the id the client sends and the table records
    assert (
        svc.count("ports:") == 1 and "0.0.0.0:" not in svc.split("ports:")[1].split("volumes:")[0]
    )


def test_make_tag_passes_limit_through() -> None:
    from test_makefile_landing import _dry_run

    assert _dry_run("tag") == ["uv run gcw tag "]
    assert _dry_run("tag", LIMIT="5") == ["uv run gcw tag --limit 5"]
    assert _dry_run("tag-bench") == ["uv run gcw tag-bench"]
    assert _dry_run("llm-model") == ["uv run gcw llm-model"]


def test_the_dag_tag_task_skips_without_a_url_and_never_starts_docker() -> None:
    dag = (REPO / "airflow" / "dags" / "gcw_pipeline.py").read_text()
    assert '[ -z "${{GCW_LLM_URL:-}}" ]; then exit 99; fi' in dag  # Airflow's skip code
    assert "{GCW} tag --no-start" in dag  # the container has no docker
    compose = (REPO / "docker-compose.yml").read_text()
    assert "GCW_LLM_URL: ${GCW_AIRFLOW_LLM_URL:-}" in compose  # empty by default: skipped
