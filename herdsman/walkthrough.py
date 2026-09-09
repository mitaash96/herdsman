"""Diff walkthrough: group checkpoint changed paths into logical cohorts.

Pure projection over `checkpoint.changed_paths` output — no git, daemon, or
model calls. Cohorts come from a fixed path-prefix table (first match wins)
with a top-level-directory fallback, and are sorted by name, so the result is
stable regardless of input order. Lane 5 projects this over HTTP/CLI for
checkpoint review.
"""

from __future__ import annotations

from collections.abc import Iterable

from .classes import Model

ROOT_COHORT = "(root)"
"""Cohort name for paths with no directory component."""

# (path prefix, cohort name, what changing files there means for reviewers).
# First match wins; the fallback is the top-level directory with no behavior
# line, so unknown trees still group deterministically.
_COHORTS: tuple[tuple[str, str, str], ...] = (
    ("herdsman/", "daemon core", "daemon and CLI behavior changed"),
    ("tests/", "tests", "test coverage changed"),
    ("ui/", "overlay UI", "the Svelte overlay changed"),
    ("notes/", "notes", "project notes and documentation changed"),
    ("assets/", "agent assets", "agent prompts, skills, or roles changed"),
    (".github/", "CI", "CI workflows changed"),
)


class Cohort(Model):
    """One group of changed files with a mechanical summary."""

    name: str
    """Conceptual cohort name from the prefix table, the top-level directory
    for untabled trees, or `(root)` for repository-root files."""
    paths: list[str]
    """Sorted, deduplicated changed paths in this cohort."""
    summary: str
    """Mechanical count-and-scope line, e.g. `2 files changed; daemon and CLI
    behavior changed`."""


class Walkthrough(Model):
    """Deterministic cohort projection of a checkpoint's changed paths."""

    cohorts: list[Cohort]
    """Sorted by cohort name."""
    total_files: int
    """Deduplicated changed-path count across all cohorts."""


def _classify(path: str) -> tuple[str, str | None]:
    """The cohort a path belongs to, and its behavior line when tabled."""
    for prefix, name, behavior in _COHORTS:
        if path.startswith(prefix):
            return name, behavior
    head, _, rest = path.partition("/")
    if rest:
        return head, None
    return ROOT_COHORT, None


def walkthrough(paths: Iterable[str]) -> Walkthrough:
    """Group changed paths into deterministically ordered cohorts."""
    grouped: dict[str, set[str]] = {}
    behaviors: dict[str, str] = {}
    for raw in paths:
        path = raw.strip()
        if not path:
            continue
        name, behavior = _classify(path)
        if behavior is not None:
            behaviors[name] = behavior
        grouped.setdefault(name, set()).add(path)
    cohorts = [
        Cohort(
            name=name,
            paths=sorted(members),
            summary=_summary(name, len(members), behaviors.get(name)),
        )
        for name, members in sorted(grouped.items())
    ]
    return Walkthrough(
        cohorts=cohorts, total_files=sum(len(cohort.paths) for cohort in cohorts)
    )


def _summary(name: str, count: int, behavior: str | None) -> str:
    """The deterministic summary line for one cohort."""
    files = f"{count} {'file' if count == 1 else 'files'}"
    if name == ROOT_COHORT:
        return f"{files} at repository root"
    if behavior is not None:
        return f"{files} changed; {behavior}"
    return f"{files} under {name}/"
