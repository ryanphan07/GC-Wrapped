"""Stand-ins for the dbt objects of D95, for integration tests that have no dbt build.

`create_text_view` loads the real seed (dbt/seeds/instagram_placeholder_patterns.csv) into
analytics.instagram_placeholder_patterns and creates analytics.fct_message_text with the same
definition as dbt/models/marts/fct_message_text.sql, over raw.messages. Using the real seed means
these tests exercise the real patterns, and a change to one shows up here.
"""

import csv
from pathlib import Path

import psycopg

SEED = Path(__file__).resolve().parents[1] / "dbt" / "seeds" / "instagram_placeholder_patterns.csv"

# Same expression as the dbt model: the whole trimmed content, case-insensitive.
TEXT_VIEW_SQL = """
CREATE VIEW analytics.fct_message_text AS
SELECT m.message_key,
       coalesce(m.content ~ '\\S', false) AND x.placeholder_pattern_id IS NULL AS has_user_text,
       x.placeholder_pattern_id
FROM raw.messages AS m
LEFT JOIN (
    SELECT m2.message_key, min(p.pattern_id) AS placeholder_pattern_id
    FROM raw.messages AS m2
    JOIN analytics.instagram_placeholder_patterns AS p
        ON coalesce(m2.content ~ '\\S', false)
        AND btrim(m2.content) ~* ('^(?:' || p.pattern || ')$')
    GROUP BY m2.message_key
) AS x USING (message_key)
"""


def seed_rows() -> list[tuple[str, str]]:
    with SEED.open(encoding="utf-8", newline="") as handle:
        return [(r["pattern_id"], r["pattern"]) for r in csv.DictReader(handle)]


def create_text_view(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    conn.execute(
        "CREATE TABLE analytics.instagram_placeholder_patterns "
        "(pattern_id text, pattern text, description text)"
    )
    for pattern_id, pattern in seed_rows():
        conn.execute(
            "INSERT INTO analytics.instagram_placeholder_patterns VALUES (%s, %s, 'x')",
            (pattern_id, pattern),
        )
    conn.execute(TEXT_VIEW_SQL)


def drop_text_view(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    conn.execute("DROP VIEW IF EXISTS analytics.fct_message_text")
    conn.execute("DROP TABLE IF EXISTS analytics.instagram_placeholder_patterns")
