"""`make bench-retrieval` (D137): where retrieval time goes at real corpus size.

Synthetic only, in gcw_test. It builds a warehouse at the pilot's scale (about 11000 messages,
about 6000 of them eligible typed ones; senders who said yes, local, no or nothing, and Instagram
placeholders), embeds it with the production window builder and stub vectors, materializes the dbt
stand-ins as dbt does, and creates the two views answering reads from the dbt models' own SQL. Over
the committed synthetic questions it times, through the production code, the search and excerpt
queries (one connection each, so prepared statements behave as they do in an eval pass), and the
same queries as they were before D131, against the raw tables. Query embedding is timed against the
dev embed server with the real model; if that server cannot be started, only that part is skipped.
EXPLAIN (ANALYZE, BUFFERS) output goes to a new temporary directory outside the repo. The old and
current queries must return identical results, also after a revocation.

Prints counts, milliseconds and that directory's path. Not part of `make test` or
`make test-integration`.
"""

import hashlib
import random
import re
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import psycopg
import pytest

from ask_view_support import create_ask_views
from gcwrapped import docker
from gcwrapped.answers import excerpts
from gcwrapped.config import get_database_settings
from gcwrapped.labeling.files import read_questions
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.retrieval import commands, search
from gcwrapped.retrieval.client import embed_query
from gcwrapped.retrieval.embed import run_embed, vector_literal
from gcwrapped.retrieval.evaluation import DEPTH
from gcwrapped.retrieval.window import DIMENSIONS
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import check_local_url
from gcwrapped.tagging.runner import Timings
from placeholder_support import create_text_view

pytestmark = pytest.mark.bench

# Bound at import, before the autouse fixture that hides the machine's containers: the embed part
# starts a real server, so it must see a running real stack and refuse (D126).
RUNNING_CONTAINERS = docker.running_containers

SEED = 20261127
MESSAGES = 11_000
THREADS = ("111", "222", "333")
# (ledger value, share of messages) of each thread's senders; None = not in the ledger.
SENDERS = (("yes", 0.25), ("yes", 0.20), ("local", 0.17), ("no", 0.14), ("", 0.10), (None, 0.14))
PLACEHOLDER_SHARE = 0.12
PLACEHOLDERS = (
    "{sender} sent an attachment.",
    "Liked a message",
    "{sender} reacted ❤ to your message.",
    "the video chat ended.",
)
_COMMON = (
    "i you the a to is it and that we in lol for on me my are of what so do be just this have "
    "not at can no yeah ok but was with if when im like u get go how who where why dont know"
)
COMMON_WORDS = _COMMON.split()
# The dbt materialization of fct_messages' columns the views and window builder read (D20).
SESSIONS_SQL = """
CREATE TABLE analytics.fct_messages AS
SELECT message_key,
       thread_id || ':s' || sum(starts) OVER (PARTITION BY thread_id ORDER BY sent_at, message_key)
           AS session_id,
       sent_at AT TIME ZONE 'America/Los_Angeles' AS sent_at_local
FROM (
    SELECT message_key, thread_id, sent_at,
           coalesce(sent_at - lag(sent_at) OVER (PARTITION BY thread_id
                    ORDER BY sent_at, message_key) > interval '60 minutes', true)::int AS starts
    FROM raw.messages
) AS s
"""

# --- the queries as they were before D131 (commit 12a09b1), against the raw tables --------------

_BEFORE_CTE = """
WITH eligible AS (
    SELECT e.message_key
    FROM analytics.fct_message_eligibility AS e
    JOIN analytics.fct_message_text AS x USING (message_key)
    WHERE e.local_ok AND x.has_user_text
),
searchable AS (
    SELECT m.message_key, m.embedding, m.window_tsv
    FROM enrich.message_embeddings AS m
    WHERE m.embed_version = %(version)s AND NOT EXISTS (
        SELECT 1 FROM unnest(m.window_message_keys || m.message_key) AS w(key)
        WHERE w.key NOT IN (SELECT message_key FROM eligible)
    )
)"""
BEFORE_VECTOR_SQL = f"""{_BEFORE_CTE}
SELECT message_key, 1 - (embedding <=> %(query)s::vector) AS score
FROM searchable
ORDER BY embedding <=> %(query)s::vector, message_key
LIMIT %(k)s
"""
BEFORE_TEXT_SQL = f"""{_BEFORE_CTE},
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
BEFORE_WINDOWS_SQL = """
SELECT message_key, window_message_keys
FROM enrich.message_embeddings
WHERE embed_version = %(version)s AND message_key = ANY(%(keys)s)
"""
BEFORE_MESSAGES_SQL = """
WITH eligible AS (
    SELECT m.message_key, m.thread_id, f.session_id, m.sender_name, f.sent_at_local, m.content,
           row_number() OVER (
               PARTITION BY m.thread_id, f.session_id ORDER BY m.sent_at, m.message_key
           ) AS position
    FROM raw.messages AS m
    JOIN analytics.fct_message_eligibility AS e USING (message_key)
    JOIN analytics.fct_message_text AS t USING (message_key)
    JOIN analytics.fct_messages AS f USING (message_key)
    WHERE e.local_ok AND t.has_user_text
)
SELECT message_key, thread_id, session_id, position, sender_name, sent_at_local, content
FROM eligible
WHERE message_key = ANY(%(keys)s)
"""


class BeforeRetriever(search.Retriever):
    """The same Python path, each search query swapped for its pre-D131 text."""

    def _query(self, sql: str, query: str, k: int) -> list[search.Hit]:
        before = BEFORE_VECTOR_SQL if sql == search.VECTOR_SQL else BEFORE_TEXT_SQL
        return super()._query(before, query, k)


def _unit_vector(text: str) -> list[float]:
    """A dense vector fixed by the text, so storage and distance cost what real ones cost."""
    rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
    return [rng.gauss(0.0, 1.0) for _ in range(DIMENSIONS)]


class StubEmbedder:
    """Vectors with no server, computed once per text so a timed query pays no stub cost."""

    model_id = model.EMBED_MODEL_ID

    def __init__(self) -> None:
        self._cache: dict[str, list[float]] = {}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        for text in texts:
            if text not in self._cache:
                self._cache[text] = _unit_vector(text)
        return [self._cache[text] for text in texts]


def _vocabulary(rng: random.Random, questions: Sequence[str]) -> tuple[list[str], list[float]]:
    """Words with a Zipf-like frequency: common words, the questions' own words, then filler."""
    asked = sorted({w for q in questions for w in re.findall(r"[a-z]+", q.lower())})
    syllables = [c + v for c in "bdfgklmnprstvz" for v in "aeiou"]
    filler = sorted({"".join(rng.choices(syllables, k=rng.randint(2, 3))) for _ in range(5000)})
    words = list(dict.fromkeys([*COMMON_WORDS, *asked, *filler]))
    return words, [1 / (rank + 3) for rank in range(len(words))]


def _load_messages(conn: psycopg.Connection, questions: Sequence[str]) -> None:
    rng = random.Random(SEED)
    words, weights = _vocabulary(rng, questions)
    run = conn.execute(
        "INSERT INTO raw.load_runs (roots_count) VALUES (1) RETURNING load_id"
    ).fetchone()
    assert run is not None
    names = [f"Bench Sender {n}" for n in range(len(SENDERS))]
    for thread in THREADS:
        for name, (consent, _) in zip(names, SENDERS, strict=True):
            if consent is not None:
                conn.execute(
                    "INSERT INTO raw.sender_consent (thread_id, sender_name, consent) "
                    "VALUES (%s, %s, %s)",
                    (thread, name, consent),
                )
    minute = dict.fromkeys(THREADS, 0.0)
    shares = [share for _, share in SENDERS]
    with conn.cursor().copy(
        "COPY raw.messages (message_key, platform, thread_id, sender_name, sent_at, content, "
        "media_count, source_file, load_id) FROM STDIN"
    ) as copy:
        for n in range(MESSAGES):
            thread = THREADS[n % len(THREADS)]
            long_gap = rng.random() < 0.05  # starts a new session (D20)
            minute[thread] += rng.uniform(90, 900) if long_gap else rng.expovariate(0.5)
            sender = rng.choices(names, weights=shares)[0]
            if rng.random() < PLACEHOLDER_SHARE:
                content = rng.choice(PLACEHOLDERS).format(sender=sender)
            else:
                length = min(60, max(1, round(rng.lognormvariate(1.9, 0.8))))
                content = " ".join(rng.choices(words, weights=weights, k=length))
            sent = f"{1_704_067_200 + minute[thread] * 60:.3f}"
            copy.write_row(
                (
                    hashlib.sha256(f"bench {n}".encode()).hexdigest(),
                    "instagram",
                    thread,
                    sender,
                    time.strftime("%Y-%m-%d %H:%M:%S+00", time.gmtime(float(sent))),
                    content,
                    0,
                    "bench",
                    run[0],
                )
            )


@pytest.fixture
def warehouse(migrated_db: str) -> Iterator[str]:
    """gcw_test with the bench corpus. analytics holds only test stand-ins there, so the whole
    schema is dropped before and after: other tests expect views where this builds tables."""
    get_database_settings.cache_clear()
    questions = [q.question for q in read_questions(commands.SYNTHETIC_QUESTIONS)]
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == ("gcw_test",)
        conn.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
        conn.execute("CREATE SCHEMA analytics")
        _load_messages(conn, questions)
        # dbt: eligibility is a view (D75); fct_message_text and fct_messages are tables.
        conn.execute(
            "CREATE VIEW analytics.fct_message_eligibility AS "
            "SELECT m.message_key, m.thread_id, m.sender_name, "
            "coalesce(c.consent IN ('yes', 'local'), false) AS local_ok, "
            "coalesce(c.consent = 'yes', false) AS external_ok "
            "FROM raw.messages AS m LEFT JOIN raw.sender_consent AS c "
            "ON c.thread_id = m.thread_id AND c.sender_name = m.sender_name"
        )
        create_text_view(conn)
        conn.execute("ALTER VIEW analytics.fct_message_text RENAME TO bench_text_view")
        conn.execute(
            "CREATE TABLE analytics.fct_message_text AS SELECT * FROM analytics.bench_text_view"
        )
        conn.execute(SESSIONS_SQL)
        create_ask_views(conn)
    run_embed(migrated_db, StubEmbedder())
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("VACUUM ANALYZE")  # what autovacuum does to a warehouse that has sat a while
    yield migrated_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP SCHEMA analytics CASCADE")


def _percentiles(seconds: list[float]) -> str:
    timings = Timings(seconds)
    p50, p95 = timings.percentile(50), timings.percentile(95)
    assert p50 is not None and p95 is not None
    return f"p50 {p50 * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms"


def _timed[**P, T](
    seconds: list[float], call: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    began = time.perf_counter()
    result = call(*args, **kwargs)
    seconds.append(time.perf_counter() - began)
    return result


Results = list[tuple[list[search.Hit], list[search.Hit], list[list[excerpts.Source]]]]


def _pass(
    retriever: search.Retriever, conn: psycopg.Connection, questions: Sequence[str]
) -> tuple[dict[str, list[float]], Results]:
    """One warm-up query of each shape, then each question once, as the eval and answering do."""
    times: dict[str, list[float]] = {"vector search": [], "hybrid search": [], "excerpts": []}
    results: Results = []
    for number, question in enumerate([questions[0], *questions]):
        kept = times if number else {name: [] for name in times}
        by_vector = _timed(kept["vector search"], retriever.vector, question, DEPTH)
        by_hybrid = _timed(kept["hybrid search"], retriever.hybrid, question, DEPTH)
        top = [h.message_id for h in by_vector[: excerpts.TOP_K]]
        found = _timed(kept["excerpts"], excerpts.load_excerpts, conn, retriever.version, top)
        if number:
            results.append((by_vector, by_hybrid, found))
    return times, results


def _compare_versions(
    url: str, questions: Sequence[str], embedder: StubEmbedder, patch: pytest.MonkeyPatch
) -> dict[str, dict[str, list[float]]]:
    """Times of the current and pre-D131 queries; fails unless their results are identical."""
    with psycopg.connect(url, autocommit=True) as conn:
        now_times, now = _pass(search.Retriever(conn, embedder), conn, questions)
    with patch.context() as old, psycopg.connect(url, autocommit=True) as conn:
        old.setattr(excerpts, "WINDOWS_SQL", BEFORE_WINDOWS_SQL)
        old.setattr(excerpts, "MESSAGES_SQL", BEFORE_MESSAGES_SQL)
        old_times, before = _pass(BeforeRetriever(conn, embedder), conn, questions)
    # Rows of one excerpt query come in no set order, but the excerpts built from them do.
    assert now == before, "current and pre-D131 queries returned different results"
    return {"current": now_times, "before D131": old_times}


def _plan_line(name: str, plan: str) -> str:
    def number(pattern: str) -> str:
        found = re.search(pattern, plan)
        return found.group(1) if found else "none"

    return (
        f"plan {name}: planning {number(r'Planning Time: ([0-9.]+)')} ms, "
        f"execution {number(r'Execution Time: ([0-9.]+)')} ms, "
        f"JIT {number(r'(?s)JIT:.*?Total ([0-9.]+) ms')} ms, "
        f"top-node shared buffers hit {number(r'Buffers: shared hit=([0-9]+)')}"
    )


def _explain(url: str, question: str, embedder: StubEmbedder, out: Path) -> list[str]:
    """EXPLAIN (ANALYZE, BUFFERS) of each query shape, with the values inline (a custom plan)."""
    literal = vector_literal(embed_query(embedder, question))
    with psycopg.connect(url, autocommit=True) as conn:
        retriever = search.Retriever(conn, embedder)
        version = retriever.version
        top = [h.message_id for h in retriever.vector(question, excerpts.TOP_K)]
        keys = sorted(
            {
                m
                for _, members in conn.execute(
                    excerpts.WINDOWS_SQL, {"version": version, "keys": top}
                )
                for m in members
            }
        )
        vector = {"version": version, "query": literal, "k": DEPTH}
        candidates = {**vector, "k": search.CANDIDATES}
        text = {"version": version, "query": question, "k": search.CANDIDATES}
        shapes = (
            ("vector", search.VECTOR_SQL, BEFORE_VECTOR_SQL, vector),
            ("hybrid-vector", search.VECTOR_SQL, BEFORE_VECTOR_SQL, candidates),
            ("hybrid-text", search.TEXT_SQL, BEFORE_TEXT_SQL, text),
            (
                "excerpt-windows",
                excerpts.WINDOWS_SQL,
                BEFORE_WINDOWS_SQL,
                {"version": version, "keys": top},
            ),
            ("excerpt-messages", excerpts.MESSAGES_SQL, BEFORE_MESSAGES_SQL, {"keys": keys}),
        )
        lines = []
        for name, now, before, params in shapes:
            for label, sql in (("current", now), ("before-d131", before)):
                with psycopg.ClientCursor(conn) as cursor:
                    cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
                    plan = "\n".join(str(row[0]) for row in cursor.fetchall())
                (out / f"{name}.{label}.txt").write_text(plan + "\n", encoding="utf-8")
                lines.append(_plan_line(f"{name} {label}", plan))
    return lines


def _query_embedding(questions: Sequence[str], patch: pytest.MonkeyPatch) -> str:
    """Query embedding times against the dev embed server and the real model, or why not."""
    patch.setattr(docker, "running_containers", RUNNING_CONTAINERS)
    try:
        check_local_url(commands.embed_url())
        docker.require_no_other_stack()
        commands.start_embed()
        commands.require_embed_model()
        client = commands.local_embedder()
        embed_query(client, questions[0])  # warm-up, untimed
        seconds: list[float] = []
        for question in questions:
            _timed(seconds, embed_query, client, question)
    except Exception as exc:  # the rest of the benchmark does not need a model
        return f"query embedding: skipped ({type(exc).__name__})"
    return f"query embedding (real model): {_percentiles(seconds)}"


def test_bench_retrieval(warehouse: str, monkeypatch: pytest.MonkeyPatch) -> None:
    out = Path(tempfile.mkdtemp(prefix="gcw-bench-")).resolve()
    assert REPO_ROOT not in out.parents, "TMPDIR must be outside the repo"
    questions = [q.question for q in read_questions(commands.SYNTHETIC_QUESTIONS)]
    embedder = StubEmbedder()
    for question in questions:
        embed_query(embedder, question)  # fill the cache before anything is timed
    with psycopg.connect(warehouse, autocommit=True) as conn:
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM raw.messages), "
            "(SELECT count(*) FROM analytics.ask_messages)"
        ).fetchone()
        assert counts is not None
        messages, eligible = counts
        searchable = len(search.Retriever(conn, embedder).searchable_ids())
        settings = [
            f"{name} {conn.execute(f'SHOW {name}').fetchone()[0]}"  # type: ignore[index]
            for name in ("server_version", "jit", "work_mem", "max_parallel_workers_per_gather")
        ]
    assert 5700 <= eligible <= 6300 and searchable == eligible
    lines = [
        f"messages: {messages}",
        f"eligible typed messages: {eligible}",
        f"searchable windows: {searchable}",
        f"queries: {len(questions)}",
        f"server: {', '.join(settings)}",
    ]
    for version, times in _compare_versions(warehouse, questions, embedder, monkeypatch).items():
        lines += [f"{name}, {version}: {_percentiles(s)}" for name, s in times.items()]
    lines += _explain(warehouse, questions[0], embedder, out)
    lines.append(_query_embedding(questions, monkeypatch))
    with psycopg.connect(warehouse, autocommit=True) as conn:
        conn.execute(
            "UPDATE raw.sender_consent SET consent = 'no' "
            "WHERE thread_id = %s AND sender_name = 'Bench Sender 0'",
            (THREADS[0],),
        )
    _compare_versions(warehouse, questions, embedder, monkeypatch)
    lines.append("identical results, current and before D131: yes, also after a revocation")
    lines.append(f"plans: {out}")
    report = "\n".join(f"bench: {line}" for line in lines)
    (out / "summary.txt").write_text(report + "\n", encoding="utf-8")
    print("", report, sep="\n")
