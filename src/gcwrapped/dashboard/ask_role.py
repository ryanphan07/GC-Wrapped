"""gcw_ask, the role the Ask page connects as (D127): it can read what answering reads and
nothing else, and can write nothing, whatever its session's own settings say.

`gcw dashboard-services` runs `ensure` through the pipeline's superuser connection before the
dashboard starts. It works on a volume of any age, which a Postgres init script would not. dbt
grants SELECT on the relations it rebuilds (dbt_project.yml), so a build keeps them. The
password is never logged or printed, and the server only ever receives its SCRAM verifier, so a
logged statement cannot show it either.
"""

import psycopg
from psycopg import sql

from gcwrapped.config import PasswordSettings

ROLE = "gcw_ask"
# Every relation `answers.pipeline` and the defaults it reads touch, derived from its queries.
# Messages and vectors come only from the two consent-filtered views (D131), which run with their
# owner's rights, so nothing behind them is needed: no table that holds every sender's text.
# A new read there fails the Ask tests until it is listed here (and in the README and, for a dbt
# relation, dbt_project.yml).
RELATIONS = (
    "analytics.ask_messages",
    "analytics.ask_message_embeddings",
    "enrich.tag_default",
    "enrich.retrieval_default",
    "enrich.answer_prompt_default",
)
# public holds the vector type and its operators.
SCHEMAS = ("enrich", "analytics", "public")


class AskPasswordUnset(Exception):
    """GCW_ASK_PASSWORD is unset or empty. Only the class name is ever printed."""


class AskPasswordReused(Exception):
    """GCW_ASK_PASSWORD equals another password in the env file."""


def require_password() -> str:
    """GCW_ASK_PASSWORD, refused if it is missing or is also another role's password: the
    dashboard holds it, and must not thereby hold the pipeline's or the reader's."""
    settings = PasswordSettings()
    password = settings.gcw_ask_password.get_secret_value()
    if not password:
        raise AskPasswordUnset
    if password in settings.other_passwords():
        raise AskPasswordReused
    return password


def ensure(url: str, password: str) -> None:
    """Create or update the role and leave it exactly the grants above, in one transaction.
    `url` is a superuser connection to the target database; the grants apply to that database."""
    role = sql.Identifier(ROLE)
    with psycopg.connect(url) as conn:

        def run(statement: str, *parts: sql.Composable) -> None:
            conn.execute(sql.SQL(statement).format(*parts, role=role))

        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone() is None:
            run("CREATE ROLE {role}")
        verifier = conn.pgconn.encrypt_password(password.encode(), ROLE.encode(), b"scram-sha-256")
        run(
            "ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "NOBYPASSRLS PASSWORD {}",
            sql.Literal(verifier.decode()),
        )
        database = sql.Identifier(conn.info.dbname)
        run("ALTER ROLE {role} RESET ALL")
        run("ALTER ROLE {role} IN DATABASE {} RESET ALL", database)
        run("ALTER ROLE {role} SET default_transaction_read_only = on")
        # Revoke first, so a grant made by hand or by an older version does not survive.
        schemas = conn.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'"
        ).fetchall()
        for (name,) in schemas:
            schema = sql.Identifier(name)
            run("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {role}", schema)
            run("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {role}", schema)
            run("REVOKE ALL ON SCHEMA {} FROM {role}", schema)
        for name in SCHEMAS:
            run("GRANT USAGE ON SCHEMA {} TO {role}", sql.Identifier(name))
        for relation in RELATIONS:
            run("GRANT SELECT ON {} TO {role}", sql.Identifier(*relation.split(".")))
        # Two ways to write that need no grant on any schema, both given to everyone by default:
        # temporary tables and large objects.
        run("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC", database)
        run(
            "REVOKE EXECUTE ON FUNCTION lo_creat(integer), lo_create(oid), "
            "lo_from_bytea(oid, bytea) FROM PUBLIC"
        )
