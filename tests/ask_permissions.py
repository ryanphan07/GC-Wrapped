"""What gcw_ask may do in a database (D127), checked relation by relation from the catalog: used
on the stand-in warehouse in gcw_test and on the dbt-built one."""

import psycopg
import pytest
from psycopg import errors, sql

from gcwrapped.config import PasswordSettings
from gcwrapped.dashboard import ask_role

TEST_PASSWORD = "gcw-ask-test-password"

_USER_SCHEMAS = "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
_RELATIONS_SQL = f"""
SELECT n.nspname, c.relname, c.relkind::text,
       (SELECT a.attname FROM pg_attribute AS a
        WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum LIMIT 1)
FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f') AND {_USER_SCHEMAS}
"""


def set_up_gcw_ask(admin_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The role as dashboard-up sets it up. The configured password is kept when there is one, so
    a test run does not lock a running dev dashboard out (roles belong to the whole cluster)."""
    password = PasswordSettings().gcw_ask_password.get_secret_value() or TEST_PASSWORD
    monkeypatch.setenv("GCW_ASK_PASSWORD", password)
    ask_role.ensure(admin_url, password)


def _relations(admin_url: str) -> list[tuple[str, str, str, str]]:
    with psycopg.connect(admin_url) as conn:
        return conn.execute(_RELATIONS_SQL).fetchall()


def _denied(conn: psycopg.Connection, statement: sql.Composed | str) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


def assert_reads_only_the_listed_relations(admin_url: str, ask_url: str) -> None:
    found = _relations(admin_url)
    names = {f"{schema}.{name}" for schema, name, _, _ in found}
    assert set(ask_role.RELATIONS) <= names
    # tables the brief names, which must exist for their refusal to mean anything
    assert {"raw.sender_consent", "raw.load_runs"} <= names
    with psycopg.connect(ask_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user").fetchone() == ("gcw_ask",)
        for schema, name, _, _ in found:
            query = sql.SQL("SELECT * FROM {} LIMIT 0").format(sql.Identifier(schema, name))
            if f"{schema}.{name}" in ask_role.RELATIONS:
                conn.execute(query)
            else:
                _denied(conn, query)


def assert_writes_nothing(admin_url: str, ask_url: str) -> None:
    """Every write is refused by a grant, with the session's own read-only setting turned off."""
    with psycopg.connect(admin_url) as conn:
        schemas = conn.execute(
            f"SELECT n.nspname FROM pg_namespace AS n WHERE {_USER_SCHEMAS}"
        ).fetchall()
    with psycopg.connect(ask_url, autocommit=True) as conn:
        assert conn.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
        with pytest.raises(errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM raw.messages")
        conn.execute("SET default_transaction_read_only = off")
        for schema, name, kind, column in _relations(admin_url):
            relation = sql.Identifier(schema, name)
            privileges = conn.execute(
                "SELECT has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE') "
                "OR has_any_column_privilege(c.oid, 'INSERT, UPDATE') "
                "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = %s",
                (schema, name),
            ).fetchone()
            assert privileges == (False,), f"{schema}.{name}"
            if kind != "r":  # a view may refuse a write for its shape before asking for the right
                continue
            _denied(conn, sql.SQL("INSERT INTO {} DEFAULT VALUES").format(relation))
            _denied(
                conn,
                sql.SQL("UPDATE {} SET {} = DEFAULT").format(relation, sql.Identifier(column)),
            )
            _denied(conn, sql.SQL("DELETE FROM {}").format(relation))
        for (schema,) in schemas:
            _denied(
                conn,
                sql.SQL("CREATE TABLE {} (x int)").format(sql.Identifier(schema, "gcw_ask_probe")),
            )
        _denied(conn, "CREATE SCHEMA gcw_ask_probe")
        _denied(conn, "CREATE TEMPORARY TABLE gcw_ask_probe (x int)")
        _denied(conn, "SELECT lo_create(0)")
        _denied(conn, "SELECT lo_from_bytea(0, 'x')")
