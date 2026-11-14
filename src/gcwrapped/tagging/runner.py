"""The tagging run (D89): prune, skip what is already tagged, tag the rest, commit in batches.

Safe to stop and rerun: rows are committed every BATCH_SIZE tagged messages and on the way out of
any error or Ctrl-C, and a rerun skips every message that already has a row for this model and
prompt version. A message the model could not tag (a failure) gets no row, so a rerun retries it.

Pruning runs first: a tag whose message is no longer local_ok (consent revoked, D88) is deleted
for every model and prompt version, so revoking consent removes derived data, not just hides it.
"""

import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import psycopg

from gcwrapped.labeling.source import EligibleIndex, Message, load_eligible
from gcwrapped.tagging.prompt import PROMPT_VERSION, messages_for
from gcwrapped.tagging.provider import Tag, TagFailure, TagProvider

BATCH_SIZE = 25

PRUNE_SQL = """
DELETE FROM enrich.message_tags AS t
WHERE NOT EXISTS (
    SELECT 1 FROM analytics.fct_message_eligibility AS e
    WHERE e.message_key = t.message_key AND e.local_ok
)
"""
TAGGED_SQL = (
    "SELECT message_key FROM enrich.message_tags WHERE model_id = %s AND prompt_version = %s"
)
INSERT_SQL = (
    "INSERT INTO enrich.message_tags "
    "(message_key, topic, sentiment, model_id, prompt_version, run_id) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
)


@dataclass
class Timings:
    """Seconds per message, for messages that were attempted (tagged or failed)."""

    seconds: list[float] = field(default_factory=list)

    def percentile(self, percent: int) -> float | None:
        """Nearest-rank percentile (1-100). None when nothing was timed."""
        if not self.seconds:
            return None
        ordered = sorted(self.seconds)
        rank = max(1, (percent * len(ordered) + 99) // 100)  # ceil(percent * n / 100), integers
        return ordered[rank - 1]


@dataclass
class RunResult:
    model_id: str
    prompt_version: str
    eligible: int = 0
    already_tagged: int = 0
    tagged: int = 0
    failures: int = 0
    pruned: int = 0
    seconds: float = 0.0
    timings: Timings = field(default_factory=Timings)
    topics: Counter[str] = field(default_factory=Counter)
    sentiments: Counter[str] = field(default_factory=Counter)

    def count(self, tag: Tag) -> None:
        self.tagged += 1
        self.topics[tag.topic] += 1
        self.sentiments[tag.sentiment] += 1

    @property
    def messages_per_second(self) -> float:
        attempted = self.tagged + self.failures
        return attempted / self.seconds if self.seconds > 0 else 0.0


def tag_message(provider: TagProvider, index: EligibleIndex, message: Message) -> Tag:
    system, user = messages_for(index, message)
    return provider.tag(system, user)


def run_tagging(
    url: str,
    provider: TagProvider,
    *,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    prompt_version: str = PROMPT_VERSION,
    clock: Callable[[], float] = time.perf_counter,
) -> RunResult:
    """Tag every local-eligible message not yet tagged for (provider.model_id, prompt_version).

    `limit` caps how many messages are attempted this run. A ProviderUnavailable from the provider
    propagates after the pending batch is committed.
    """
    started = clock()
    run_id = uuid.uuid4().hex
    result = RunResult(model_id=provider.model_id, prompt_version=prompt_version)
    index = load_eligible(url)
    result.eligible = len(index)
    with psycopg.connect(url) as conn:
        with conn.transaction():
            result.pruned = conn.execute(PRUNE_SQL).rowcount
        done = {str(r[0]) for r in conn.execute(TAGGED_SQL, (provider.model_id, prompt_version))}
        conn.commit()
        wanted = [mid for ids in index.ids_by_thread().values() for mid in ids]
        result.already_tagged = sum(1 for mid in wanted if mid in done)
        todo = [mid for mid in wanted if mid not in done]
        if limit is not None:
            todo = todo[: max(limit, 0)]
        pending: list[Sequence[str]] = []

        def flush() -> None:
            if pending:
                with conn.transaction():
                    conn.cursor().executemany(INSERT_SQL, pending)
                pending.clear()

        try:
            for message_id in todo:
                began = clock()
                try:
                    tag = tag_message(provider, index, index.get(message_id))
                except TagFailure:
                    result.failures += 1
                else:
                    result.count(tag)
                    pending.append(
                        (
                            message_id,
                            tag.topic,
                            tag.sentiment,
                            provider.model_id,
                            prompt_version,
                            run_id,
                        )
                    )
                finally:
                    result.timings.seconds.append(clock() - began)
                if len(pending) >= batch_size:
                    flush()
        finally:
            flush()  # also on ProviderUnavailable and Ctrl-C: what is tagged stays tagged
    result.seconds = clock() - started
    return result
