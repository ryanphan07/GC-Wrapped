"""The llama.cpp chat client (D89, D111): schema-constrained JSON from a server on this machine.

Privacy properties, each enforced here and tested:
- `check_local_url` refuses any base URL whose host is not localhost, 127.0.0.1 or a compose
  service name (`llm`, `embed`), so message text cannot be sent to another machine by a typo or a
  bad env.
- The opener has no proxy handler and no redirect handler: an HTTP(S)_PROXY variable cannot route
  the request elsewhere, and a redirect cannot move it to another host.
- Errors carry no message text and no response body.

Constrained decoding (the `response_format` JSON schema; the tagger's enums are labeling.schema's)
makes an invalid answer near impossible. The answer is still validated. A tag gets one retry, then
the message counts as a failure and stays untagged. An answer is never retried (see `answers`).
"""

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS
from gcwrapped.tagging.prompt import RESPONSE_SCHEMA
from gcwrapped.tagging.provider import ProviderUnavailable, Tag, TagFailure

# llm and embed are the compose service names, which the dashboard container uses (D124).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "llm", "embed"})
REQUEST_TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 2  # the first try plus one retry
MAX_TOKENS = 48
MAX_RESPONSE_BYTES = 1 << 20


class NonLocalUrlError(ValueError):
    """The llm base URL names a host that is not on this machine. The message omits the URL."""


def check_local_url(url: str) -> str:
    """Return the base URL without a trailing slash, or raise NonLocalUrlError."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018  # raises ValueError on a malformed port
    except ValueError:
        raise NonLocalUrlError("llm URL is not valid") from None
    if parts.scheme != "http" or host not in LOCAL_HOSTS:
        raise NonLocalUrlError("llm URL must be http://localhost, 127.0.0.1, llm or embed")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise NonLocalUrlError("llm URL must not carry credentials, a query or a fragment")
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # an HTTPError is raised instead of following the Location header


def local_opener() -> urllib.request.OpenerDirector:
    """An opener that follows no proxy and no redirect: a request stays where it was sent."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def parse_tag(body: object) -> Tag:
    """The Tag in a chat-completions response, or TagFailure if anything is off."""
    try:
        assert isinstance(body, dict)
        content = body["choices"][0]["message"]["content"]
        answer = json.loads(content)
        topic, sentiment = answer["topic"], answer["sentiment"]
    except (AssertionError, KeyError, IndexError, TypeError, ValueError):
        raise TagFailure from None
    if topic not in TOPICS or sentiment not in SENTIMENTS or set(answer) != {"topic", "sentiment"}:
        raise TagFailure
    return Tag(topic=topic, sentiment=sentiment)


class ChatFailure(Exception):
    """One request gave no usable response, though a server is there. Raised as a subclass."""


class ChatTimeout(ChatFailure):
    """No response within the timeout (D116)."""


class ChatRefused(ChatFailure):
    """The server answered with an HTTP error, or with a body that is not JSON."""


class LlamaCppChat:
    """One schema-constrained chat request to a llama.cpp server on this machine (D89, D111):
    temperature 0, the registry entry's template setting, and a JSON schema the server turns into
    a grammar. The tagger and the answer pipeline both go through here."""

    def __init__(
        self,
        base_url: str,
        *,
        model_id: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        chat_template_kwargs: dict[str, bool] | None = None,
    ) -> None:
        self._base = check_local_url(base_url)
        self.model_id = model_id
        self._timeout = timeout
        self._chat_template_kwargs = chat_template_kwargs  # from the registry entry (D98)
        self._opener = local_opener()

    def complete(
        self,
        system: str,
        user: str,
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> object:
        """The response body of one request. ChatTimeout if it took too long, ChatRefused if the
        server answered with an error or not with JSON, ProviderUnavailable if nothing is
        listening."""
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "cache_prompt": True,  # the system prefix is identical, so the server reuses it
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        if self._chat_template_kwargs:
            payload["chat_template_kwargs"] = self._chat_template_kwargs
        request = urllib.request.Request(
            f"{self._base}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return json.loads(response.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError:
            raise ChatRefused from None  # the server answered, with an error (bad request, 5xx)
        except TimeoutError:
            raise ChatTimeout from None  # one slow request
        except (urllib.error.URLError, ConnectionError, OSError):
            raise ProviderUnavailable from None  # nothing is listening: stop the run
        except ValueError:
            raise ChatRefused from None  # the body was not JSON


class LlamaCppProvider(LlamaCppChat):
    """The tagger (D89): the chat request above with the tag schema, retried once."""

    def tag(self, system: str, user: str) -> Tag:
        for attempt in range(MAX_ATTEMPTS):
            try:
                body = self.complete(
                    system, user, schema_name="tag", schema=RESPONSE_SCHEMA, max_tokens=MAX_TOKENS
                )
                return parse_tag(body)
            except (ChatFailure, TagFailure):
                if attempt == MAX_ATTEMPTS - 1:
                    raise TagFailure from None
        raise TagFailure  # unreachable; keeps the type checker satisfied
