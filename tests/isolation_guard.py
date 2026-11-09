"""Test-isolation guard: the test session must not be configured to reach the real stack.

.claude/settings.json deny rules restrict the assistant, not the test code. This is the check that
the suite itself starts against synthetic settings only (see the "fixed session rules" decision).

It inspects configuration *values* and resolved paths only. A path under the private root is never
opened: `effective_config` checks ENV_FILE before it reads anything. Error messages name the
variable and the rule, never the value.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

REAL_PORT = "5433"
REAL_DB_NAME = "gcw_real"
# The schemes libpq (psycopg) accepts in a connection URI; config.py and db.py pass the value to it.
URL_SCHEMES = ("postgresql", "postgres")
REAL_COMPOSE_PROJECT = "gc-wrapped-real"
PRIVATE_DIR_NAME = "gcw-private"

PORT_VARS = ("POSTGRES_PORT", "POSTGRES_HOST_PORT")
URL_VARS = ("DATABASE_URL", "DASHBOARD_DATABASE_URL")
# ENV_FILE is also a data path, but it resolves against the working directory (as config.py does).
PATH_VARS = ("GCW_LANDING_HOST_DIR", "GCW_LANDING_DIR", "GCW_LOG_DIR", "GCW_CONSENT_LEDGER")
GUARDED_VARS = (
    *PORT_VARS,
    *URL_VARS,
    "GCW_DB_NAME",
    "COMPOSE_PROJECT_NAME",
    "ENV_FILE",
    *PATH_VARS,
)
DEFAULT_ENV_FILE = ".env"
DEFAULT_LANDING_HOST_DIR = "./data/landing"


class IsolationError(Exception):
    """The test configuration can reach the real stack. Names the rule, never a value."""


def _resolve(value: str, base: Path) -> Path:
    """Expand ~, anchor relative paths at `base`, and resolve symlinks without opening anything."""
    expanded = Path(os.path.expanduser(value))
    return Path(os.path.realpath(expanded if expanded.is_absolute() else base / expanded))


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines only (the plain format decisions.md requires of env files)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def effective_config(environ: Mapping[str, str], *, cwd: Path, home: Path) -> dict[str, str]:
    """Guarded settings as the session sees them: the env file's values, overridden by environ.

    This mirrors config.py (process environment wins over the file named by ENV_FILE, default
    `.env`). The env file's location is checked first, and a file under the private root is never
    read.
    """
    named = environ.get("ENV_FILE") or DEFAULT_ENV_FILE
    env_path = _resolve(named, cwd)
    check_not_private(env_path, home, "ENV_FILE")
    from_file = _read_env_file(env_path) if env_path.is_file() else {}
    config = {k: v for k, v in from_file.items() if k in GUARDED_VARS}
    config.update({k: environ[k] for k in GUARDED_VARS if k in environ})
    config["ENV_FILE"] = named
    return config


def check_not_private(path: Path, home: Path, var: str) -> None:
    private = Path(os.path.realpath(home / PRIVATE_DIR_NAME))
    if _is_under(path, private):
        raise IsolationError(f"{var} resolves under the private real-data directory")


def check_database_url(var: str, url: str) -> None:
    """Fail closed: accept only postgresql:// or postgres:// URLs that avoid the real stack.

    Rejects the real port or database in the authority, the percent-decoded path, or the query
    string (libpq lets `?port=` and `?dbname=` override them). Anything else, including keyword
    DSNs and other schemes, is unparsable. Messages name the variable only, never the value.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in URL_SCHEMES:
            raise ValueError
        port = parts.port  # raises ValueError on a non-numeric or out-of-range port
        database = unquote(parts.path).lstrip("/")
        query = parse_qs(parts.query, keep_blank_values=True)
    except ValueError:
        raise IsolationError(f"{var} cannot be parsed") from None
    if port is not None and str(port) == REAL_PORT:
        raise IsolationError(f"{var} uses the real stack's Postgres port")
    if database == REAL_DB_NAME:
        raise IsolationError(f"{var} names the real database")
    if REAL_PORT in (v.strip() for v in query.get("port", [])):
        raise IsolationError(f"{var} query string uses the real stack's Postgres port")
    if REAL_DB_NAME in (v.strip() for v in query.get("dbname", [])):
        raise IsolationError(f"{var} query string names the real database")


def check_isolation(config: Mapping[str, str], *, repo_root: Path, home: Path, cwd: Path) -> None:
    """Raise IsolationError if `config` targets the real stack. `config` holds GUARDED_VARS."""
    for var in PORT_VARS:
        if config.get(var, "").strip() == REAL_PORT:
            raise IsolationError(f"{var} is the real stack's Postgres port")
    for var in URL_VARS:
        if config.get(var, "").strip():
            check_database_url(var, config[var].strip())
    if config.get("GCW_DB_NAME", "").strip() == REAL_DB_NAME:
        raise IsolationError("GCW_DB_NAME is the real database")
    if config.get("COMPOSE_PROJECT_NAME", "").strip() == REAL_COMPOSE_PROJECT:
        raise IsolationError("COMPOSE_PROJECT_NAME is the real compose project")

    check_not_private(_resolve(config.get("ENV_FILE") or DEFAULT_ENV_FILE, cwd), home, "ENV_FILE")
    for var in PATH_VARS:
        if config.get(var):
            check_not_private(_resolve(config[var], repo_root), home, var)

    landing = _resolve(config.get("GCW_LANDING_HOST_DIR") or DEFAULT_LANDING_HOST_DIR, repo_root)
    if not _is_under(landing, Path(os.path.realpath(repo_root))):
        raise IsolationError("GCW_LANDING_HOST_DIR resolves outside the repo")

    # The consent ledger (D69): a session may only be configured with a synthetic ledger in the
    # repo. Unset is fine; the real ledger lives under the private root (rejected above) or at
    # least outside the repo (rejected here).
    ledger = config.get("GCW_CONSENT_LEDGER", "").strip()
    if ledger and not _is_under(_resolve(ledger, repo_root), Path(os.path.realpath(repo_root))):
        raise IsolationError("GCW_CONSENT_LEDGER resolves outside the repo")


def check_session(
    environ: Mapping[str, str], *, cwd: Path, repo_root: Path, home: Path | None = None
) -> None:
    """The session-start check: build the effective configuration, then run `check_isolation`."""
    home = home or Path(os.path.expanduser("~"))
    config = effective_config(environ, cwd=cwd, home=home)
    check_isolation(config, repo_root=repo_root, home=home, cwd=cwd)
