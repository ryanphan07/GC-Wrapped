"""The two views answering reads (D131), for integration tests that have no dbt build.

Unlike the other stand-ins, these are not restated: the SQL is the dbt models' own, with each
ref() and source() pointed at the test warehouse, so a change to a view's filter is what these
tests run. The stand-ins it reads (eligibility, user text, sessions) must exist first.
"""

import re
from pathlib import Path

import psycopg

MARTS = Path(__file__).resolve().parents[1] / "dbt" / "models" / "marts"
VIEWS = ("ask_messages", "ask_message_embeddings")  # in dependency order
RELATIONS = {
    "ref('stg_instagram__messages')": "raw.messages",
    "ref('fct_message_eligibility')": "analytics.fct_message_eligibility",
    "ref('fct_message_text')": "analytics.fct_message_text",
    "ref('fct_messages')": "analytics.fct_messages",
    "ref('ask_messages')": "analytics.ask_messages",
    "source('enrich', 'message_embeddings')": "enrich.message_embeddings",
}


def model_sql(name: str) -> str:
    """The model's SELECT, its config line dropped and its references resolved."""
    sql = (MARTS / f"{name}.sql").read_text(encoding="utf-8")
    sql, configs = re.subn(r"\A\{\{ config\(.*\) \}\}\n", "", sql)
    assert configs == 1, name
    for reference, relation in RELATIONS.items():
        sql = sql.replace("{{ " + reference + " }}", relation)
    assert "{{" not in sql, name  # a new reference needs its line above
    return sql


def create_ask_views(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    for name in VIEWS:
        conn.execute(
            f"CREATE VIEW analytics.{name} WITH (security_barrier = true) AS {model_sql(name)}"
        )


def drop_ask_views(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    for name in reversed(VIEWS):
        conn.execute(f"DROP VIEW IF EXISTS analytics.{name}")
