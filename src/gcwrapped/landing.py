"""Landing directory: a folder holding one subfolder per export root (see the Airflow DAG)."""

from pathlib import Path

from gcwrapped.parsers import instagram_format as fmt


def find_export_roots(landing: Path) -> list[Path]:
    """Immediate subfolders of `landing` that hold at least one message thread, sorted.

    A subfolder with no threads (a half-copied export, a stray file tree) is skipped rather than
    passed to the parser, so it can't fail the run. The landing folder itself is not a root.
    """
    if not landing.is_dir():
        return []
    return sorted(d for d in landing.iterdir() if d.is_dir() and fmt.find_thread_dirs(d))
