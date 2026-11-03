from pathlib import Path

import pytest
from pydantic import ValidationError

from gcwrapped.config import DatabaseSettings, Settings, get_database_settings, get_settings


def test_settings_need_no_database_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    s = Settings(_env_file=None)

    assert s.log_level == "INFO"
    assert s.landing_dir == Path("data/landing")


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_TIMEZONE", "UTC")
    monkeypatch.setenv("LANDING_DIR", "somewhere/landing")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    s = Settings(_env_file=None)

    assert s.chat_timezone == "UTC"
    assert s.landing_dir == Path("somewhere/landing")
    assert s.log_level == "DEBUG"


def test_database_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_PORT", "5544")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5544/gcw_dev")

    s = DatabaseSettings(_env_file=None)

    assert s.postgres_user == "u"
    assert s.postgres_password.get_secret_value() == "p"
    assert s.postgres_port == 5544
    assert s.database_url.get_secret_value().endswith("/gcw_dev")


def test_database_settings_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "hunter2")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2@localhost/gcw_dev")

    assert "hunter2" not in repr(DatabaseSettings(_env_file=None))


def test_getters_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/gcw_dev")
    get_settings.cache_clear()
    get_database_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
        assert get_database_settings() is get_database_settings()
    finally:
        get_settings.cache_clear()
        get_database_settings.cache_clear()
