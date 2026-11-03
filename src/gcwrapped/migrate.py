"""Plain-SQL migration runner (see decisions.md, "Raw tables via plain SQL migrations").

Files named `NNN_description.sql` in `migrations/` are applied in order. Each runs in its own
transaction together with its `schema_migrations` row, so a file is either fully applied and
recorded, or not applied at all. Re-running applies nothing.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_FILENAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
_LOCK_ID = 7_243_001  # arbitrary constant: serialises concurrent `gcw migrate` runs


class MigrationError(Exception):
    """The migrations directory is malformed. Messages name files only."""


@dataclass(frozen=True)
class Migration:
    version: str  # filename without extension, e.g. "001_raw_schema"
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """List migration files in apply order. Rejects bad names and duplicate numbers."""
    if not directory.is_dir():
        raise MigrationError("migrations directory not found")
    found: list[Migration] = []
    numbers: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration filename: {path.name} (want NNN_name.sql)")
        if match.group(1) in numbers:
            raise MigrationError(f"duplicate migration number {match.group(1)}")
        numbers.add(match.group(1))
        found.append(Migration(version=path.stem, path=path))
    return found


def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply unapplied migrations in order. Returns the versions applied by this call."""
    migrations = discover(directory)
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Session-level lock, released when the connection closes.
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in migrations:
            if migration.version in done:
                continue
            sql = migration.path.read_text(encoding="utf-8")  # checked-in file, not user input
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (migration.version,)
                )
            applied_now.append(migration.version)
            logger.info("migration applied version=%s", migration.version)
    return applied_now
