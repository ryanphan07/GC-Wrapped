"""The real llama.cpp server leaks no text (D89). Runs only where the llm service is up.

Marked integration like the other tests that need a running service, and skipped when nothing
answers at the configured llm URL, so CI (which never starts the model) skips it. Locally:
`docker compose --env-file .env --profile llm up -d --wait db llm`, then `make test-integration`.
It sends a sentinel message through the real client and the real model, then asserts the sentinel
appears nowhere in the llm container's logs, and that the endpoints that could show prompts
(slots, props, the web UI) are closed.
"""

import subprocess
import urllib.error
import urllib.request

import pytest

from gcwrapped.tagging import commands, model
from gcwrapped.tagging.llamacpp import LlamaCppProvider
from gcwrapped.tagging.prompt import SYSTEM_PROMPT, build_user_content

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELLEAKCHECK"


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # the local llm service only
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


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
    provider = LlamaCppProvider(llm, model_id=model.MODEL_ID)
    context = [f"{SENTINEL} earlier one", f"{SENTINEL} earlier two"]
    tag = provider.tag(SYSTEM_PROMPT, build_user_content(context, f"{SENTINEL} the message"))
    assert tag.topic and tag.sentiment
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
    combined = logs.stdout + logs.stderr
    assert "request" in combined.lower() or "slot" in combined.lower()  # we did read real logs
    assert SENTINEL not in combined
    assert "earlier one" not in combined and "the message" not in combined
    assert _status(f"{llm}/slots") == 501  # --no-slots: no endpoint exposing in-flight prompts
    assert _status(f"{llm}/metrics") == 501  # off by default, and it stays off
    assert _status(f"{llm}/") == 404  # --no-webui
    with urllib.request.urlopen(f"{llm}/props", timeout=5) as props:  # settings, never prompts
        assert SENTINEL not in props.read().decode("utf-8", "replace")
