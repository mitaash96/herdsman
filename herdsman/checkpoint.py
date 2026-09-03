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


def _git_bytes(path: Path, *args: str, timeout: float | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"git {' '.join(args)} timed out") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckpointError(f"git {' '.join(args)} failed: {exc}") from exc
    return result.stdout


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


def write_patch(
    path: Path,
    base_sha: str,
    destination: Path,
    *,
    timeout: float | None = None,
) -> None:
    """Capture everything this attempt changed since `base_sha` as one patch.

    `git diff` alone would miss files the executor created, so intent-to-add
    stages their existence first; `--binary` keeps non-text artifacts intact.
    The diff is taken against the working tree, so it covers committed and
    uncommitted work identically -- the executor is not required to commit.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    _ = _git(path, "add", "-A", "-N", timeout=_remaining(deadline))
    diff = _git_bytes(
        path, "diff", "--binary", base_sha, timeout=_remaining(deadline)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # `git apply` rejects a patch with no trailing newline.
    if diff and not diff.endswith(b"\n"):
        diff += b"\n"
    _ = destination.write_bytes(diff)


def apply_patches(
    path: Path, patches: Sequence[Path], *, timeout: float | None = None
) -> None:
    """Apply upstream patches into a fresh worktree and commit them.

    Committing is what keeps the next checkpoint honest: the attempt's own
    diff is then measured against a base that already contains its inputs, so
    a checkpoint's patch carries that initiative's work and nothing else.  The
    commit lands on the attempt's own throwaway branch -- herdr already made
    one per worktree -- never on a branch the user works from.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    applied = False
    for patch in patches:
        if not patch.exists():
            raise CheckpointError(f"input patch {patch} is missing")
        if not patch.stat().st_size:
            continue  # a producer that changed nothing has nothing to hand over
        _ = _git(path, "apply", "--whitespace=nowarn", str(patch), timeout=_remaining(deadline))
        applied = True
    if not applied:
        return
    _ = _git(path, "add", "-A", timeout=_remaining(deadline))
    _ = _git(
        path,
        "-c",
        "user.name=herdsman",
        "-c",
        "user.email=herdsman@localhost",
        "commit",
        "-qm",
        "herdsman: initiative inputs",
        timeout=_remaining(deadline),
    )


@dataclass
class GitCheckpointCollector:
    """Run deterministic checks and create a mechanical evidence manifest."""

    checks: Sequence[str] = _DEFAULT_CHECKS
    project_root: Path | None = None
    """Where `.herdsman/artifacts` lives. Without it, no patch is materialized."""

    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str:
        """Apply this attempt's inputs, then report the commit it starts from."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        apply_patches(path, inputs, timeout=_remaining(deadline))
        return git_head(path, timeout=_remaining(deadline))

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
        # Collected before the patch is written: intent-to-add would otherwise
        # change what `git status` reports for an untracked file.
        touched = changed_paths(
            path,
            base_sha=base_sha,
            head_sha=head_sha,
            timeout=_remaining(deadline),
        )
        checkpoint_id = f"cp_{uuid4().hex}"
        patch_path: str | None = None
        if self.project_root is not None:
            relative = Path(".herdsman") / "artifacts" / f"{checkpoint_id}.patch"
            write_patch(
                path,
                base_sha,
                self.project_root / relative,
                timeout=_remaining(deadline),
            )
            patch_path = str(relative)
        return Checkpoint(
            id=checkpoint_id,
            attempt_id=attempt_id,
            changed_paths=touched,
            base_sha=base_sha,
            head_sha=head_sha,
            checks=results,
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=patch_path,
        )


__all__ = [
    "CheckpointError",
    "Completion",
    "GitCheckpointCollector",
    "apply_patches",
    "changed_paths",
    "git_head",
    "write_patch",
]
