"""Diff walkthrough: group checkpoint changed paths into logical cohorts.

Pure projection over `checkpoint.changed_paths` output — no git, daemon, or
model calls. Grouping is path-based only (top-level directory), cohorts and
paths are sorted, so the result is stable regardless of input order. Lane 5
projects this over HTTP/CLI for checkpoint review.
"""

from __future__ import annotations

from collections.abc import Iterable

from .classes import Model

ROOT_COHORT = "(root)"
"""Cohort name for paths with no directory component."""


class Cohort(Model):
    """One group of changed files with a mechanical summary."""

    name: str
    """Top-level directory, or `(root)` for repository-root files."""
    paths: list[str]
    """Sorted, deduplicated changed paths in this cohort."""
    summary: str
    """Mechanical count-and-scope line, e.g. `3 files under herdsman/`."""


class Walkthrough(Model):
    """Deterministic cohort projection of a checkpoint's changed paths."""

    cohorts: list[Cohort]
    """Sorted by cohort name."""
    total_files: int
    """Deduplicated changed-path count across all cohorts."""


def _cohort_name(path: str) -> str:
    head, _, _ = path.partition("/")
    return head if _ else ROOT_COHORT


def walkthrough(paths: Iterable[str]) -> Walkthrough:
    """Group changed paths into deterministically ordered cohorts."""
    grouped: dict[str, set[str]] = {}
    for raw in paths:
        path = raw.strip()
        if not path:
            continue
        grouped.setdefault(_cohort_name(path), set()).add(path)
    cohorts = [
        Cohort(
            name=name,
            paths=sorted(members),
            summary=(
                f"{len(members)} {'file' if len(members) == 1 else 'files'} "
                f"{'at repository root' if name == ROOT_COHORT else f'under {name}/'}"
            ),
        )
        for name, members in sorted(grouped.items())
    ]
    return Walkthrough(
        cohorts=cohorts, total_files=sum(len(cohort.paths) for cohort in cohorts)
    )
