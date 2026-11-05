"""Landing directory: a folder holding one subfolder per export root (see the Airflow DAG)."""

from collections import Counter
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from gcwrapped.parsers import instagram_format as fmt

SHAPES = ("<name>_<digits>", "<digits>", "other")


def find_export_roots(landing: Path) -> list[Path]:
    """Immediate subfolders of `landing` that have an inbox folder, sorted.

    A subfolder without one (a half-copied export, a stray file tree) is skipped rather than
    passed to the parser, so it can't fail the run. The landing folder itself is not a root.
    Looks no deeper than the inbox folder itself: thread folders are not listed here.
    """
    if not landing.is_dir():
        return []
    return sorted(d for d in landing.iterdir() if d.is_dir() and (d / fmt.MESSAGES_DIR).is_dir())


@dataclass(frozen=True)
class RootReport:
    shapes: Counter[str]
    allowlisted_found: frozenset[str]
    to_ingest: frozenset[str]


@dataclass(frozen=True)
class LandingReport:
    """Consent check of a landing directory. Holds counts and numeric IDs, never folder names."""

    roots: list[RootReport]
    configured: int

    @property
    def thread_folders(self) -> int:
        """Folders with a thread-ID shape, summed over roots (one in two roots counts twice)."""
        return sum(n for r in self.roots for shape, n in r.shapes.items() if shape != "other")

    @property
    def found(self) -> frozenset[str]:
        return frozenset().union(*(r.allowlisted_found for r in self.roots))

    @property
    def to_ingest(self) -> frozenset[str]:
        return frozenset().union(*(r.to_ingest for r in self.roots))

    @property
    def not_found(self) -> int:
        return self.configured - len(self.found)

    def lines(self) -> list[str]:
        """The report as `label: number` lines. Every line is a fixed label plus integers."""
        out = [f"export roots: {len(self.roots)}"]
        for number, root in enumerate(self.roots, start=1):
            out += [f"root {number} folders {shape}: {root.shapes[shape]}" for shape in SHAPES]
            out.append(f"root {number} allowlisted IDs found: {len(root.allowlisted_found)}")
            out.append(f"root {number} allowlisted threads to ingest: {len(root.to_ingest)}")
        total_shapes: Counter[str] = sum((r.shapes for r in self.roots), Counter())
        out += [f"total folders {shape}: {total_shapes[shape]}" for shape in SHAPES]
        out += [
            f"allowlisted IDs configured: {self.configured}",
            f"allowlisted IDs found: {len(self.found)}",
            f"allowlisted IDs not found: {self.not_found}",
            f"allowlisted threads to ingest: {len(self.to_ingest)}",
        ]
        return out


def check_landing(landing: Path, allowlist: AbstractSet[str]) -> LandingReport:
    """Count thread folders by shape and allowlisted threads per export root, by folder name."""
    roots = []
    for root in find_export_roots(landing):
        selection = fmt.select_threads(root, allowlist)
        roots.append(
            RootReport(
                shapes=selection.shapes,
                allowlisted_found=selection.allowlisted_found,
                to_ingest=frozenset(fmt.thread_id(d) for d in selection.threads),
            )
        )
    return LandingReport(roots, configured=len(allowlist))
