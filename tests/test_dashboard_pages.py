"""The dashboard without a database or Streamlit (D123): the Wrapped card's words, the local-only
configuration and what `make dashboard-up` starts.
"""

import os
import subprocess
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from gcwrapped.answers import commands as answers
from gcwrapped.cli import build_parser
from gcwrapped.dashboard import services, wrapped
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import NonLocalUrlError, check_local_url

REPO = Path(__file__).resolve().parents[1]


def _profile(**changes: object) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "member_key": "k",
        "thread_id": "111",
        "member_name": "Ana",
        "messages_sent": 1234,
        "thread_share": 0.2345,
        "most_active_hour": 22,
        "most_active_weekday": 2,
        "median_reply_seconds": 190.0,
        "first_message_date": date(2024, 1, 2),
        "last_message_date": date(2026, 3, 5),
        "top_topic_1": "plans",
        "top_topic_2": "sports_fitness",
        "top_topic_3": "banter",
        "positive_share": 0.4,
        "neutral_share": 0.5,
        "negative_share": 0.1,
    }
    return {**profile, **changes}


def test_the_card_says_each_field_in_plain_words() -> None:
    assert wrapped.card_lines(_profile()) == [
        "Messages sent: 1,234",
        "Share of the thread's messages: 23%",
        "Most active: Tuesdays at 10 pm",
        "Typical reply time: 3 minutes",
        "First message: 2 January 2024",
        "Last message: 5 March 2026",
        "Top topics: plans, sports and fitness, banter",
        "Mood: 40% positive, 50% neutral, 10% negative",
    ]


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_missing_values_are_said_plainly(missing: object) -> None:
    lines = wrapped.card_lines(
        _profile(
            median_reply_seconds=missing,
            top_topic_1=missing,
            top_topic_2=missing,
            top_topic_3=missing,
            positive_share=missing,
            neutral_share=missing,
            negative_share=missing,
        )
    )
    assert "Typical reply time: no replies yet" in lines
    assert "Top topics: not tagged yet" in lines and "Mood: not tagged yet" in lines
    assert not any("nan" in line or "None" in line for line in lines)


def test_fewer_than_three_topics_are_listed_as_they_are() -> None:
    lines = wrapped.card_lines(_profile(top_topic_2=None, top_topic_3=None))
    assert "Top topics: plans" in lines


@pytest.mark.parametrize(
    ("hour", "text"), [(0, "12 am"), (1, "1 am"), (11, "11 am"), (12, "12 pm"), (23, "11 pm")]
)
def test_hours_are_said_on_a_twelve_hour_clock(hour: int, text: str) -> None:
    assert wrapped.hour_text(hour) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0 seconds"),
        (1, "1 second"),
        (89, "89 seconds"),
        (90, "2 minutes"),
        (5399, "90 minutes"),
        (5400, "1.5 hours"),
        (7200, "2 hours"),
    ],
)
def test_reply_times_are_said_in_a_fitting_unit(seconds: float, text: str) -> None:
    assert wrapped.duration_text(seconds) == text


def test_a_small_share_is_not_shown_as_nothing() -> None:
    assert wrapped.percent_text(0.001) == "under 1%"
    assert wrapped.percent_text(0) == "0%" and wrapped.percent_text(1) == "100%"


def test_members_who_share_a_name_are_told_apart_and_names_are_made_safe() -> None:
    profiles = [
        _profile(),
        _profile(thread_id="222"),
        _profile(member_name="Bo\x1b[2J", thread_id="222"),
    ]
    assert wrapped.member_labels(profiles) == [
        "Ana (thread 111)",
        "Ana (thread 222)",
        "Bo�[2J",
    ]


# --- local only (D123) -------------------------------------------------------------------------


def test_streamlit_listens_locally_and_sends_no_usage_statistics() -> None:
    config = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "127.0.0.1"
    assert config["server"]["allowedHosts"] == ["localhost", "127.0.0.1"]
    assert config["browser"]["gatherUsageStats"] is False
    assert config["client"]["showErrorDetails"] is False
    dockerfile = (REPO / "dashboard" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .streamlit ./.streamlit" in dockerfile  # the image runs under the same file


def _dashboard_service() -> str:
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    return compose.split("\n  dashboard:\n")[1].split("\n  # ")[0]


def test_the_dashboard_port_is_published_on_this_machine_only() -> None:
    service = _dashboard_service()
    ports = service.split("ports:\n")[1].split("healthcheck:")[0].strip().splitlines()
    assert [p.strip() for p in ports] == ['- "127.0.0.1:${DASHBOARD_PORT:-8501}:8501"']


def test_the_dashboard_container_reaches_the_models_by_compose_name_only() -> None:
    service = _dashboard_service()
    for line in ("GCW_LLM_URL: http://llm:8080", "GCW_EMBED_URL: http://embed:8080"):
        assert line in service
    for url in ("http://llm:8080", "http://embed:8080"):
        assert check_local_url(url) == url
    for url in ("http://embed.example.com", "http://embedding:8080", "https://embed:8080"):
        with pytest.raises(NonLocalUrlError):
            check_local_url(url)


# --- make dashboard-up -------------------------------------------------------------------------


def test_make_dashboard_up_starts_the_models_then_the_dashboard() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL")}
    shown = subprocess.run(
        ["make", "-n", "dashboard-up"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert shown[0] == "uv run gcw dashboard-services"
    assert shown[1].startswith("docker compose --env-file ")
    assert shown[1].endswith(" up -d --build --wait dashboard")
    assert build_parser().parse_args(["dashboard-services"]).command == "dashboard-services"


def test_the_services_refuse_a_non_local_url_before_starting_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def start(*_: object) -> None:
        pytest.fail("a service was started")

    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GCW_EMBED_URL", "http://example.com")
    from gcwrapped.config import get_database_settings

    get_database_settings.cache_clear()
    try:
        assert services.run(answers.Services(db=start, llm=start, embed=start)) == 1
    finally:
        get_database_settings.cache_clear()
    assert capsys.readouterr().out == "dashboard-up: failed at embed url (NonLocalUrlError)\n"


def test_the_served_model_is_the_default_when_none_is_promoted() -> None:
    assert model.MODEL_ID in model.REGISTRY
