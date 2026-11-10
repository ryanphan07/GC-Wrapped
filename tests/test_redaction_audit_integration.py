"""`gcw redaction-audit` against gcw_test with SENTINELNAME senders and SENTINELTEXT bodies."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from gcwrapped import redaction_audit
from gcwrapped.metrics import UnexpectedDatabaseError
from gcwrapped.redaction_audit import Steps

pytestmark = pytest.mark.integration

T1, T2 = "111", "222"
A = "SENTINELNAMEA Priya"
B = "SENTINELNAMEB, José"
REACTOR = "SENTINELNAMEC Gwen"  # reacts but never sends


def _seed(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        run = conn.execute(
            "INSERT INTO raw.load_runs (status, roots_count) VALUES ('succeeded', 1) "
            "RETURNING load_id"
        ).fetchone()
        assert run is not None
        reactions = json.dumps([{"actor": REACTOR, "reaction": "x"}])
        rows = [
            ("k1", T1, A, "SENTINELTEXT hi jose, call +1 415 867 5309", "[]"),
            ("k2", T1, B, "SENTINELTEXT ok priya, gwen's coming", reactions),
            ("k3", T1, A, None, "[]"),  # media only
            ("k4", T2, B, "SENTINELTEXT plain words only", "[]"),
        ]
        for key, thread, sender, content, react in rows:
            conn.execute(
                "INSERT INTO raw.messages (message_key, platform, thread_id, sender_name, "
                "sent_at, content, media_count, source_file, load_id, reactions) "
                "VALUES (%s, 'instagram', %s, %s, now(), %s, 0, 'f.json', %s, %s::jsonb)",
                (key, thread, sender, content, run[0], react),
            )


def _run(url: str) -> Steps:
    return Steps(
        docker=lambda: None, audit=lambda set_step: redaction_audit.audit_database(url, set_step)
    )


def test_audit_counts_and_leaks_nothing(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(migrated_db)

    assert redaction_audit.run(_run(migrated_db)) == 0

    captured = capsys.readouterr()
    out = captured.out
    lines = out.splitlines()
    assert "target database: gcw_test" in lines
    assert "messages scanned: 4" in lines
    assert "messages with text: 3" in lines
    assert "messages changed: 2" in lines  # the third text has only SENTINELTEXT
    assert "placeholders phone: 1" in lines
    assert "placeholders person: 3" in lines  # jose, priya, gwen (a reactor, in the directory)
    assert "member-name tokens remaining: 0" in lines
    assert lines[-1] == "redaction-audit: ok"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_audit_writes_nothing(migrated_db: str) -> None:
    _seed(migrated_db)
    with psycopg.connect(migrated_db) as conn:
        before = conn.execute("SELECT count(*), max(loaded_at) FROM raw.messages").fetchone()
    redaction_audit.run(_run(migrated_db))
    with psycopg.connect(migrated_db) as conn:
        after = conn.execute("SELECT count(*), max(loaded_at) FROM raw.messages").fetchone()
        content = conn.execute("SELECT count(*) FROM raw.messages WHERE content LIKE '%<%'")
    assert before == after
    assert content.fetchone() == (0,)


def test_unknown_database_name_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    url = "postgresql://u:p@localhost:1/SENTINELDB"
    with pytest.raises(UnexpectedDatabaseError):
        redaction_audit.audit_database(url, lambda step: None)
    steps = Steps(docker=lambda: None, audit=lambda s: redaction_audit.audit_database(url, s))
    assert redaction_audit.run(steps) == 1
    captured = capsys.readouterr()
    assert captured.out == "redaction-audit: failed at database (UnexpectedDatabaseError)\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_database_error_prints_type_name_only(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DROP TABLE raw.messages CASCADE")
    assert redaction_audit.run(_run(migrated_db)) == 1
    captured = capsys.readouterr()
    assert captured.out == "redaction-audit: failed at directory (UndefinedTable)\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


REPO = Path(__file__).resolve().parents[1]


def test_real_command_in_a_subprocess_leaks_nothing_on_either_stream(
    migrated_db: str, tmp_path: Path
) -> None:
    """`gcw redaction-audit` as a user runs it: real model, real database (gcw_test), two streams.

    Only the external binary is faked: a `docker` that succeeds, so the db-start step does not
    touch compose. The database comes from the environment clean_db set (GCW_DB_NAME,
    DATABASE_URL), which wins over .env, so this cannot reach gcw_dev or gcw_real.
    """
    _seed(migrated_db)
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "ENV_FILE"}
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    assert env["GCW_DB_NAME"] == "gcw_test"

    result = subprocess.run(
        [sys.executable, "-m", "gcwrapped.cli", "redaction-audit"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    shapes = redaction_audit.LINE_SHAPES
    assert len(lines) == len(shapes) + 1
    for line, shape in zip(lines[:-1], shapes, strict=True):
        assert re.fullmatch(shape, line), shape
    assert lines[-1] == "redaction-audit: ok"
    assert "target database: gcw_test" in lines
    assert "sentinel" not in result.stdout.lower()
    assert "sentinel" not in result.stderr.lower()
