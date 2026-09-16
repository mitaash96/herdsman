"""Read-only harness discovery over the declared Kitchen adapters.

For every adapter declared in the project Kitchen this lane resolves its
executable deterministically and runs one bounded ``--version`` probe -- the
only generic, read-only version/health seam. It never launches an agent task,
never opens a PTY, never scans PATH for un-declared harnesses, and never writes
harness or global configuration.

Absence, failure, and timeout are explicit states, never inferred healthy:
a missing executable keeps ``health="unknown"`` with a precise detail, and a
failed or timed-out probe is ``unhealthy`` because the probe is itself the
health check. Model rows are *not* discovered: a generic adapter has no
read-only model-listing seam (listing would mean launching the harness), so
`DiscoveryResult.models` is always empty and the canonical/local declarations
in `Kitchen` remain authoritative (`Kitchen.catalog` merges).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .classes import FrozenModel
from .kitchen import HarnessFacts, Kitchen, ModelEntry

__all__ = [
    "DiscoveryResult",
    "ProbeResult",
    "Runner",
    "discover",
    "subprocess_runner",
]

_VERSION_FLAG = "--version"
_DETAIL_LIMIT = 200


class ProbeResult(FrozenModel):
    """One observed process outcome. ``returncode`` is ``None`` when the
    process never completed (timeout or spawn failure); ``error`` carries the
    spawn-failure message."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""


Runner = Callable[[Sequence[str], float], ProbeResult]
"""The injectable process execution seam. Real harnesses are never invoked in
tests; callers substitute a stub here."""


class DiscoveryResult(FrozenModel):
    """One discovery pass: facts per declared adapter, in declaration order."""

    facts: list[HarnessFacts]
    models: list[ModelEntry] = []
    """Always empty: no generic read-only model-discovery seam exists."""


def subprocess_runner(argv: Sequence[str], timeout: float) -> ProbeResult:
    """The default Runner: one bounded, captured, non-checking subprocess."""

    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(returncode=None, timed_out=True)
    except OSError as exc:
        return ProbeResult(returncode=None, error=str(exc))
    return ProbeResult(
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
    )


def discover(
    kitchen: Kitchen,
    *,
    project_root: str | os.PathLike[str] = ".",
    runner: Runner | None = None,
    timeout: float = 10.0,
) -> DiscoveryResult:
    """Probe every declared adapter and return observed `HarnessFacts`.

    Executables resolve deterministically: a name without a path separator goes
    through ``PATH`` lookup; a path-like element is used as-is when absolute and
    against `project_root` when relative. Probes run concurrently but results
    keep declaration order. Nothing is written anywhere.
    """
    run = runner if runner is not None else subprocess_runner
    root = Path(project_root).expanduser().resolve()
    entries: list[tuple[str, str | None, str]] = [
        (adapter.name, *_resolve_executable(adapter.argv[0], root))
        for adapter in kitchen.adapters
    ]

    def probe_entry(entry: tuple[str, str | None, str]) -> HarnessFacts:
        name, executable, detail = entry
        return _probe(name, executable, detail, run, timeout)

    with ThreadPoolExecutor(max_workers=max(len(entries), 1)) as pool:
        facts = list(pool.map(probe_entry, entries))
    return DiscoveryResult(facts=facts)


def _resolve_executable(argv0: str, project_root: Path) -> tuple[str | None, str]:
    """Return ``(executable, detail)``; ``executable`` is ``None`` when absent."""
    if os.sep in argv0 or (os.altsep is not None and os.altsep in argv0):
        path = Path(argv0)
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            return None, f"executable {argv0!r} not found at {path}"
        if not os.access(path, os.X_OK):
            return None, f"{path} exists but is not executable"
        return str(path.resolve()), ""
    found = shutil.which(argv0)
    if found is None:
        return None, f"executable {argv0!r} not found on PATH"
    return found, ""


def _probe(
    harness: str,
    executable: str | None,
    resolution_detail: str,
    run: Runner,
    timeout: float,
) -> HarnessFacts:
    if executable is None:
        return HarnessFacts(
            harness=harness, executable=None, version=None,
            health="unknown", detail=resolution_detail,
        )
    result = run([executable, _VERSION_FLAG], timeout)
    if result.timed_out:
        return HarnessFacts(
            harness=harness, executable=executable, version=None,
            health="unhealthy",
            detail=f"{_VERSION_FLAG} probe timed out after {timeout:g}s",
        )
    if result.error:
        return HarnessFacts(
            harness=harness, executable=executable, version=None,
            health="unhealthy",
            detail=f"{_VERSION_FLAG} probe failed: {result.error}",
        )
    if result.returncode != 0:
        diagnostic = _first_line(result.stderr) or _first_line(result.stdout)
        suffix = f": {diagnostic}" if diagnostic else ""
        return HarnessFacts(
            harness=harness, executable=executable, version=None,
            health="unhealthy",
            detail=f"{_VERSION_FLAG} probe exited {result.returncode}{suffix}",
        )
    version = _first_line(result.stdout)
    return HarnessFacts(
        harness=harness, executable=executable, version=version,
        health="healthy",
        detail="" if version else "no version output on stdout",
    )


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_DETAIL_LIMIT]
    return None
