"""Vector and hybrid retrieval over the warehouse (D103, D104). Returns message IDs, never text.

Consent is applied at query time, not only when a row is written (D75, D88): a row is searchable
only if its centre and every message of its window are eligible now, so a revocation takes effect
when consent-load commits, before anything is pruned. Search reads only the consent-filtered views
(D131): analytics.ask_message_embeddings, which holds the rows with an eligible centre, and
analytics.ask_messages, the eligible messages every window member is checked against. Search is
exact (no ANN index), over rows of the current embed version only.

Hybrid (pre-registered, D104): the vector list and a full-text list (simple configuration, the
query's lexemes joined with OR, ranked by ts_rank, over the same window text), the top CANDIDATES
of each, fused by reciprocal rank fusion with k = RRF_K.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from gcwrapped.retrieval.client import Embedder, embed_query
from gcwrapped.retrieval.embed import vector_literal
from gcwrapped.retrieval.window import embed_version

CANDIDATES = 50
RRF_K = 60

# True for a row of `m` whose centre or any window member is not in `eligible`.
_TAINTED = """EXISTS (
    SELECT 1 FROM unnest(m.window_message_keys || m.message_key) AS w(key)
    WHERE w.key NOT IN (SELECT message_key FROM eligible)
)"""
_SEARCHABLE_CTE = f"""
WITH eligible AS (
    SELECT message_key FROM analytics.ask_messages
),
searchable AS (
    SELECT m.message_key, m.embedding, m.window_tsv
    FROM analytics.ask_message_embeddings AS m
    WHERE m.embed_version = %(version)s AND NOT {_TAINTED}
)"""
# consent-prune's delete (D105), run as the pipeline user over the table itself: the rows of every
# version that fail the same rule, so "searchable" and the deleted rows are exact complements (a
# test compares them). Returns the IDs each deleted row referred to.
PRUNE_EMBEDDINGS_SQL = f"""
WITH eligible AS (
    SELECT e.message_key
    FROM analytics.fct_message_eligibility AS e
    JOIN analytics.fct_message_text AS x USING (message_key)
    WHERE e.local_ok AND x.has_user_text
)
DELETE FROM enrich.message_embeddings AS m
WHERE {_TAINTED}
RETURNING m.window_message_keys || m.message_key
"""
SEARCHABLE_IDS_SQL = f"{_SEARCHABLE_CTE} SELECT message_key FROM searchable"
VECTOR_SQL = f"""{_SEARCHABLE_CTE}
SELECT message_key, 1 - (embedding <=> %(query)s::vector) AS score
FROM searchable
ORDER BY embedding <=> %(query)s::vector, message_key
LIMIT %(k)s
"""
# Each lexeme of the query, quoted for tsquery input (quotes and backslashes doubled), joined with
# OR. A query with no lexeme gives a NULL tsquery, which matches nothing.
TEXT_SQL = f"""{_SEARCHABLE_CTE},
query AS (
    SELECT string_agg(
        '''' || replace(replace(lexeme, '\\', '\\\\'), '''', '''''') || '''', ' | '
    )::tsquery AS q
    FROM unnest(tsvector_to_array(to_tsvector('simple', %(query)s))) AS lexeme
)
SELECT s.message_key, ts_rank(s.window_tsv, query.q) AS score
FROM searchable AS s, query
WHERE s.window_tsv @@ query.q
ORDER BY score DESC, s.message_key
LIMIT %(k)s
"""


@dataclass(frozen=True)
class Hit:
    message_id: str  # the centre of the matching window
    score: float  # cosine similarity, ts_rank, or the fused RRF score


def fuse(vector_ids: Sequence[str], text_ids: Sequence[str], rrf_k: int = RRF_K) -> list[Hit]:
    """Reciprocal rank fusion: each list adds 1 / (rrf_k + rank), rank counted from 1. Equal
    scores go to the better vector rank, then the message ID, so the order is deterministic."""
    scores: dict[str, float] = {}
    for ranked in (vector_ids, text_ids):
        for rank, message_id in enumerate(ranked, start=1):
            scores[message_id] = scores.get(message_id, 0.0) + 1 / (rrf_k + rank)
    vector_rank = {message_id: rank for rank, message_id in enumerate(vector_ids)}
    order = sorted(scores, key=lambda m: (-scores[m], vector_rank.get(m, len(vector_ids)), m))
    return [Hit(m, scores[m]) for m in order]


class Retriever:
    """Search bound to one connection and one local embedder."""

    def __init__(self, conn: psycopg.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder
        self.version = embed_version(embedder.model_id)

    def searchable_ids(self) -> frozenset[str]:
        rows = self._conn.execute(SEARCHABLE_IDS_SQL, {"version": self.version})
        return frozenset(str(key) for (key,) in rows)

    def _query(self, sql: str, query: str, k: int) -> list[Hit]:
        rows = self._conn.execute(sql, {"version": self.version, "query": query, "k": k})
        return [Hit(str(key), float(score)) for key, score in rows]

    def vector(self, query: str, k: int) -> list[Hit]:
        """The k windows closest to the query by cosine distance."""
        literal = vector_literal(embed_query(self._embedder, query))
        return self._query(VECTOR_SQL, literal, k)

    def text(self, query: str, k: int) -> list[Hit]:
        """The k windows that share a word with the query, best ts_rank first."""
        return self._query(TEXT_SQL, query.replace("\x00", " "), k)

    def hybrid(self, query: str, k: int) -> list[Hit]:
        """The vector and full-text top CANDIDATES, fused; the first k of the fusion."""
        by_vector = [h.message_id for h in self.vector(query, CANDIDATES)]
        by_text = [h.message_id for h in self.text(query, CANDIDATES)]
        return fuse(by_vector, by_text)[:k]
