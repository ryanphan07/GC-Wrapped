import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from gcwrapped.config import (
    DashboardSettings,
    DatabaseSettings,
    Settings,
    get_database_settings,
    get_settings,
)
from gcwrapped.db import database_name

DBT_PROFILES = Path(__file__).resolve().parents[1] / "dbt" / "profiles.yml"


def test_settings_need_no_database_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("GCW_DB_NAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    s = Settings(_env_file=None)

    assert s.log_level == "INFO"


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    s = Settings(_env_file=None)

    assert s.log_level == "DEBUG"


def test_database_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
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
    for var in ("GCW_DB_NAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "hunter2")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2@localhost/gcw_dev")

    assert "hunter2" not in repr(DatabaseSettings(_env_file=None))


def test_getters_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
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


# --- one variable decides the target database -------------------------------------------------


def _db_env(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("GCW_DB_NAME", name)
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")  # needs quoting if put in a URL
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("GCW_READER_PASSWORD", "rp")


def test_url_is_built_from_components_when_database_url_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_env(monkeypatch, "gcw_real")

    url = DatabaseSettings(_env_file=None).database_url.get_secret_value()

    assert database_name(url) == "gcw_real"
    assert "host=db" in url and "port=5433" in url and "user=u" in url


def test_matching_database_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_env(monkeypatch, "gcw_dev")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/gcw_dev")

    assert DatabaseSettings(_env_file=None).database_url.get_secret_value().endswith("/gcw_dev")


def test_database_url_naming_another_database_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_env(monkeypatch, "gcw_real")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/gcw_dev")

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)


def test_dashboard_url_targets_the_same_database_as_gcw(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_env(monkeypatch, "gcw_real")

    gcw = database_name(DatabaseSettings(_env_file=None).database_url.get_secret_value())
    dash_url = DashboardSettings(_env_file=None).dashboard_database_url.get_secret_value()

    assert database_name(dash_url) == gcw == "gcw_real"
    assert "user=gcw_reader" in dash_url


def test_dashboard_url_naming_another_database_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_env(monkeypatch, "gcw_real")
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", "postgresql://gcw_reader:rp@localhost/gcw_dev")

    with pytest.raises(ValidationError):
        DashboardSettings(_env_file=None)


def test_dbt_profile_reads_the_same_variable_with_no_default() -> None:
    """dbt renders its dbname from an env var; it must be GCW_DB_NAME, with no fallback."""
    text = DBT_PROFILES.read_text(encoding="utf-8")
    [dbname] = re.findall(r"^\s*dbname:\s*(.+)$", text, flags=re.MULTILINE)

    assert dbname.strip('"') == "{{ env_var('GCW_DB_NAME') }}"
