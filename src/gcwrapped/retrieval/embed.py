"""The embedding run (D103): a vector for every eligible message, in committed batches.

A message is "already current" when its row has this embed version and model and lists exactly the
window the message has now. Anything else is embedded again and replaces the row: a new version, a
neighbour that became eligible or stopped being so, or newer messages joining the session. So a
rerun with nothing changed embeds nothing, and a stopped run resumes where it was.

This job deletes no row. A row whose centre or window includes an ineligible message is never
searched (see `search`) and is removed by `make consent-prune` (D105).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psycopg

from gcwrapped.retrieval.client import Embedder, EmbedFailure
from gcwrapped.retrieval.window import Window, embed_version, load_windows

BATCH_SIZE = 16

CURRENT_SQL = (
    "SELECT message_key, window_message_keys FROM enrich.message_embeddings "
    "WHERE embed_version = %s AND model_id = %s"
)
UPSERT_SQL = """
INSERT INTO enrich.message_embeddings
    (message_key, window_message_keys, embed_version, model_id, embedding, window_tsv)
VALUES (%s, %s, %s, %s, %s::vector, to_tsvector('simple', %s))
ON CONFLICT (message_key) DO UPDATE SET
    window_message_keys = excluded.window_message_keys,
    embed_version = excluded.embed_version,
    model_id = excluded.model_id,
    embedding = excluded.embedding,
    window_tsv = excluded.window_tsv,
    created_at = now()
"""


@dataclass
class EmbedResult:
    model_id: str
    version: str
    eligible: int = 0
    embedded: int = 0
    already_current: int = 0
    failures: int = 0
    seconds: float = 0.0

    @property
    def messages_per_second(self) -> float:
        attempted = self.embedded + self.failures
        return attempted / self.seconds if self.seconds > 0 else 0.0


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text input form."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _embed_batch(embedder: Embedder, batch: Sequence[Window]) -> list[tuple[Window, list[float]]]:
    """The batch's vectors. If the batch is refused, one request per window, so a single bad
    input costs one message and not sixteen. ProviderUnavailable propagates."""
    try:
        return list(zip(batch, embedder.embed([w.text for w in batch]), strict=True))
    except EmbedFailure:
        done: list[tuple[Window, list[float]]] = []
        for window in batch:
            try:
                done.append((window, embedder.embed([window.text])[0]))
            except EmbedFailure:
                continue
        return done


def run_embed(
    url: str,
    embedder: Embedder,
    *,
    batch_size: int = BATCH_SIZE,
    clock: Callable[[], float] = time.perf_counter,
) -> EmbedResult:
    """Embed every eligible message that is not current. A ProviderUnavailable from the embedder
    propagates; the batches before it are already committed."""
    started = clock()
    result = EmbedResult(model_id=embedder.model_id, version=embed_version(embedder.model_id))
    with psycopg.connect(url) as conn:
        windows = load_windows(conn)
        current = {
            str(key): tuple(members)
            for key, members in conn.execute(CURRENT_SQL, (result.version, result.model_id))
        }
        conn.commit()
        result.eligible = len(windows)
        todo = [w for w in windows if current.get(w.message_id) != w.member_ids]
        result.already_current = len(windows) - len(todo)
        for start in range(0, len(todo), batch_size):
            batch = todo[start : start + batch_size]
            done = _embed_batch(embedder, batch)
            if done:
                with conn.transaction():
                    conn.cursor().executemany(
                        UPSERT_SQL,
                        [
                            (
                                w.message_id,
                                list(w.member_ids),
                                result.version,
                                result.model_id,
                                vector_literal(vector),
                                w.text,
                            )
                            for w, vector in done
                        ],
                    )
            result.embedded += len(done)
            result.failures += len(batch) - len(done)
    result.seconds = clock() - started
    return result
