"""Mechanical checkpoint collection for one supervised attempt."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .classes import CheckResult, Checkpoint, Usage


class CheckpointError(RuntimeError):
    """Git or check evidence could not be collected."""


@dataclass(frozen=True)
class Completion:
    """The one machine-readable completion fact emitted by an agent."""

    exit_code: int
    usage: Usage


_DEFAULT_CHECKS = ("uv run pytest -q",)


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("checkpoint collection timed out")
    return remaining


def _git(path: Path, *args: str, timeout: float | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"git {' '.join(args)} timed out") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckpointError(f"git {' '.join(args)} failed: {exc}") from exc
    return result.stdout.strip()


def git_head(path: Path, *, timeout: float | None = None) -> str:
    """Return the current commit, or fail rather than inventing a SHA."""
    head = _git(path, "rev-parse", "HEAD", timeout=timeout)
    if not head:
        raise CheckpointError("git rev-parse HEAD returned no commit")
    return head


def changed_paths(
    path: Path,
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    timeout: float | None = None,
) -> list[str]:
    """Return worktree changes and committed changes since the attempt base."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=path,
            check=True,
            capture_output=True,
            timeout=_remaining(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("git status timed out") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckpointError(f"git status failed: {exc}") from exc

    raw = result.stdout
    records = raw.decode("utf-8", errors="surrogateescape").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise CheckpointError(f"malformed git status record {record!r}")
        status, path_value = record[:2], record[3:]
        paths.append(path_value)
        if "R" in status or "C" in status:
            if index >= len(records) or not records[index]:
                raise CheckpointError("git rename record has no destination")
            paths.append(records[index])
            index += 1
    if base_sha is not None and head_sha is not None and base_sha != head_sha:
        for path_value in _git(
            path,
            "diff",
            "--name-only",
            f"{base_sha}..{head_sha}",
            timeout=_remaining(deadline),
        ).splitlines():
            if path_value and path_value not in paths:
                paths.append(path_value)
    return paths


@dataclass
class GitCheckpointCollector:
    """Run deterministic checks and create a mechanical evidence manifest."""

    checks: Sequence[str] = _DEFAULT_CHECKS

    def capture_base(self, path: Path, *, timeout: float | None = None) -> str:
        return git_head(path, timeout=timeout)

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        deadline = time.monotonic() + timeout if timeout is not None else None
        results: list[CheckResult] = []
        for command in self.checks:
            try:
                completed = subprocess.run(
                    command,
                    cwd=path,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=_remaining(deadline),
                )
                summary = (completed.stdout or completed.stderr or "").strip()
                results.append(
                    CheckResult(
                        name=command,
                        passed=completed.returncode == 0,
                        summary=summary[-1000:],
                    )
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"check {command!r} timed out") from exc
            except OSError as exc:
                results.append(CheckResult(name=command, passed=False, summary=str(exc)))

        head_sha = git_head(path, timeout=_remaining(deadline))
        return Checkpoint(
            id=f"cp_{uuid4().hex}",
            attempt_id=attempt_id,
            changed_paths=changed_paths(
                path,
                base_sha=base_sha,
                head_sha=head_sha,
                timeout=_remaining(deadline),
            ),
            base_sha=base_sha,
            head_sha=head_sha,
            checks=results,
            exit_code=completion.exit_code,
            usage=completion.usage,
        )


__all__ = [
    "CheckpointError",
    "Completion",
    "GitCheckpointCollector",
    "changed_paths",
    "git_head",
]
