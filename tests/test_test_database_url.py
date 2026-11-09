"""The gcw_test URL that conftest builds must be a postgresql:// URL the isolation guard accepts."""

import pytest
from psycopg.conninfo import conninfo_to_dict

from conftest import TEST_DB_NAME, to_test_db_url
from isolation_guard import IsolationError, check_database_url


@pytest.mark.parametrize(
    "dev_url",
    [
        "postgresql://gcw:pw@localhost:5432/gcw_dev",
        "postgres://gcw:p%40ss%2Fw@db.internal:5432/gcw_dev?sslmode=require",
        # config builds a keyword DSN when DATABASE_URL is empty
        "host=localhost port=5432 user=gcw password='p w' dbname=gcw_dev",
    ],
)
def test_url_form_passes_the_guard_and_names_gcw_test(dev_url: str) -> None:
    url = to_test_db_url(dev_url)

    assert url.startswith("postgresql://")
    check_database_url("test_database_url", url)  # raises IsolationError if rejected
    before, after = conninfo_to_dict(dev_url), conninfo_to_dict(url)
    assert after["dbname"] == TEST_DB_NAME
    for key in ("user", "password", "host", "port", "sslmode"):
        assert after.get(key) == before.get(key)


def test_a_keyword_dsn_would_have_been_rejected() -> None:
    with pytest.raises(IsolationError):
        check_database_url("x", "host=localhost dbname=gcw_test")


def test_the_real_port_is_still_refused() -> None:
    with pytest.raises(IsolationError):
        to_test_db_url("postgresql://gcw:pw@localhost:5433/gcw_dev")
