"""The real llama.cpp server leaks no text (D89). Runs only where the llm service is up.

Marked integration like the other tests that need a running service, and skipped when nothing
answers at the configured llm URL, so CI (which never starts the model) skips it. Locally:
`docker compose --env-file .env --profile llm up -d --wait db llm`, then `make test-integration`.
It sends a sentinel message through the real client and the real model, as a tag request and as
an answer request (D111), then asserts the sentinel appears nowhere in the llm container's logs,
and that the endpoints that could show prompts (slots, props, the web UI) are closed. The answer
request also shows that the pinned image's grammar builds the answer schema.
"""

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from gcwrapped.answers import pipeline, prompt
from gcwrapped.answers.excerpts import Source, SourceMessage
from gcwrapped.tagging import commands, model
from gcwrapped.tagging.llamacpp import LlamaCppChat, LlamaCppProvider
from gcwrapped.tagging.prompt import SYSTEM_PROMPT, build_user_content

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELLEAKCHECK"


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # the local llm service only
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _logs() -> str:
    logs = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "--profile",
            "llm",
            "logs",
            "--no-color",
            "llm",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return logs.stdout + logs.stderr


@pytest.fixture
def llm() -> str:
    url = commands.llm_url()
    try:
        if _status(f"{url}/health") != 200:
            pytest.skip("llm service is not healthy")
    except OSError:
        pytest.skip("llm service is not running")
    return url


def test_the_sentinel_never_reaches_the_server_logs_and_prompt_endpoints_are_closed(
    llm: str,
) -> None:
    provider = LlamaCppProvider(
        llm,
        model_id=model.MODEL_ID,
        chat_template_kwargs=model.spec_for(model.MODEL_ID).chat_template_kwargs,  # D98
    )
    context = [f"{SENTINEL} earlier one", f"{SENTINEL} earlier two"]
    tag = provider.tag(SYSTEM_PROMPT, build_user_content(context, f"{SENTINEL} the message"))
    assert tag.topic and tag.sentiment
    combined = _logs()
    assert "request" in combined.lower() or "slot" in combined.lower()  # we did read real logs
    assert SENTINEL not in combined
    assert "earlier one" not in combined and "the message" not in combined
    assert _status(f"{llm}/slots") == 501  # --no-slots: no endpoint exposing in-flight prompts
    assert _status(f"{llm}/metrics") == 501  # off by default, and it stays off
    assert _status(f"{llm}/") == 404  # --no-webui
    with urllib.request.urlopen(f"{llm}/props", timeout=5) as props:  # settings, never prompts
        assert SENTINEL not in props.read().decode("utf-8", "replace")


def test_an_answer_request_is_schema_constrained_and_never_reaches_the_server_logs(
    llm: str,
) -> None:
    messages = [
        SourceMessage(
            "0" * 64,
            "t",
            "t:1",
            n,
            f"{SENTINEL}SENDER",
            datetime(2026, 1, 2, 18, n),
            f"{SENTINEL} {text}",
        )
        for n, text in enumerate(("dinner is on friday at seven", "bring the soda"), start=1)
    ]
    excerpts = [[Source(n, message) for n, message in enumerate(messages, start=1)]]
    chat = LlamaCppChat(
        llm,
        model_id=model.MODEL_ID,
        timeout=pipeline.REQUEST_TIMEOUT_SECONDS,
        chat_template_kwargs=model.spec_for(model.MODEL_ID).chat_template_kwargs,
    )
    body = chat.complete(
        prompt.SYSTEM_PROMPT,
        prompt.build_user_content(excerpts, f"when is dinner {SENTINEL}ASKED"),
        schema_name=pipeline.SCHEMA_NAME,
        schema=prompt.response_schema(len(messages)),
        max_tokens=prompt.MAX_TOKENS,
    )
    assert isinstance(body, dict)
    output = json.loads(body["choices"][0]["message"]["content"])
    assert list(output) == ["answerable", "citations", "answer"]  # the grammar keeps the order
    assert set(output["citations"]) <= {1, 2}
    assert pipeline.parse_answer(body, len(messages))[0] != pipeline.INVALID
    combined = _logs()
    assert "request" in combined.lower() or "slot" in combined.lower()  # we did read real logs
    for secret in (SENTINEL, "dinner is on friday", "bring the soda", "when is dinner"):
        assert secret not in combined
