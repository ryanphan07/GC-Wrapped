"""Small database helpers shared by the migration runner, the loader and the CLI."""

from psycopg.conninfo import conninfo_to_dict

from gcwrapped.config import get_database_settings


def database_url() -> str:
    """The connection string for GCW_DB_NAME. Never log it: it contains the password."""
    return get_database_settings().database_url.get_secret_value()


def database_name(url: str) -> str:
    """The database name inside a connection string, safe to print (no host, user or password)."""
    return str(conninfo_to_dict(url).get("dbname") or "(unknown)")
