import psycopg
import pytest

from gcwrapped.config import get_database_settings

pytestmark = pytest.mark.integration


def test_select_one_and_vector_extension() -> None:
    url = get_database_settings().database_url.get_secret_value()
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
        row = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        assert row is not None
