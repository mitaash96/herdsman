"""Mechanical checkpoint collection for one supervised attempt."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .classes import CheckpointError, CheckResult, Checkpoint, Usage
from .integration import capture, checkpoint_pin, pin
from .redact import contains_credential
from .store import atomic_write_bytes


@dataclass(frozen=True)
class Completion:
    """A settled TUI has no process exit or measured usage yet."""

    exit_code: int | None = None
    usage: Usage | None = None


CREDENTIAL_CHECK = "handoff-credentials"

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


def diff_lines(
    path: Path,
    base_sha: str,
    commit_sha: str,
    *,
    timeout: float | None = None,
) -> int:
    """Count added and removed lines in `base_sha..commit_sha`."""
    total = 0
    for line in _git(
        path, "diff", "--numstat", f"{base_sha}..{commit_sha}", timeout=timeout
    ).splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or fields[0] == "-" or fields[1] == "-":
            continue  # binary files have no line count
        try:
            total += int(fields[0]) + int(fields[1])
        except ValueError:
            raise CheckpointError(f"malformed git numstat record {line!r}") from None
    return total


def changed_paths(
    path: Path,
    base_sha: str,
    commit_sha: str,
    *,
    timeout: float | None = None,
) -> list[str]:
    """Return paths changed in `base_sha..commit_sha`; renames give both paths."""
    raw = _git_bytes(
        path, "diff", "--name-status", "-z", f"{base_sha}..{commit_sha}", timeout=timeout
    )
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        width = 2 if fields[index][0] in "RC" else 1
        chunk = fields[index + 1 : index + 1 + width]
        if len(chunk) != width or not all(chunk):
            raise CheckpointError(f"malformed git name-status record {fields[index]!r}")
        paths.extend(chunk)
        index += 1 + width
    return paths


def write_patch(
    path: Path,
    base_sha: str,
    commit_sha: str,
    destination: Path,
    *,
    timeout: float | None = None,
) -> None:
    """Write `base_sha..commit_sha` as one patch; `--binary` keeps non-text intact."""
    diff = _git_bytes(
        path, "diff", "--binary", f"{base_sha}..{commit_sha}", timeout=timeout
    )
    # `git apply` rejects a patch with no trailing newline.
    if diff and not diff.endswith(b"\n"):
        diff += b"\n"
    atomic_write_bytes(destination, diff)


def credential_check(patch: Path) -> CheckResult:
    """Report credential-shaped content in a handoff patch, never rewrite it.

    A patch edited to hide a secret no longer applies, so this is evidence on
    the checkpoint a human already reviews before the handoff proceeds, not a
    silent scrub and not a hard block on a fixture that merely looks like a key.
    """
    try:
        body = patch.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(name=CREDENTIAL_CHECK, passed=False, summary=f"patch unreadable: {exc}")
    if contains_credential(body):
        return CheckResult(
            name=CREDENTIAL_CHECK,
            passed=False,
            summary="handoff patch contains credential-shaped content; review before approving",
        )
    return CheckResult(name=CREDENTIAL_CHECK, passed=True, summary="no credential-shaped content")


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
        "--allow-empty",
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

    def diagnose(
        self,
        path: Path,
        attempt_id: str,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> str | None:
        """Preserve the attempt's raw diff as one repair diagnostic.

        Written on a failure before checkpoint collection, so the work the
        agent did before dying survives any later cleanup and `salvage` has a
        stable pointer. Returns the artifact path relative to the project
        root, or None when no project root is configured.
        """
        if self.project_root is None:
            return None
        cap = capture(
            path,
            [base_sha],
            at=datetime.now(UTC),
            message=f"herdsman: diagnostic {attempt_id}",
            timeout=timeout,
        )
        relative = Path(".herdsman") / "artifacts" / f"{attempt_id}.diag.patch"
        write_patch(
            path,
            base_sha,
            cap.commit,
            self.project_root / relative,
            timeout=timeout,
        )
        return str(relative)

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

        checkpoint_id = f"cp_{uuid4().hex}"
        cap = capture(
            path,
            [base_sha],
            at=datetime.now(UTC),
            message=f"herdsman: checkpoint {checkpoint_id}",
            timeout=_remaining(deadline),
        )
        if self.project_root is not None:
            pin(self.project_root, checkpoint_pin(checkpoint_id), cap.commit)
        head_sha = git_head(path, timeout=_remaining(deadline))
        touched = changed_paths(path, base_sha, cap.commit, timeout=_remaining(deadline))
        lines = diff_lines(path, base_sha, cap.commit, timeout=_remaining(deadline))
        patch_path: str | None = None
        if self.project_root is not None:
            relative = Path(".herdsman") / "artifacts" / f"{checkpoint_id}.patch"
            write_patch(
                path,
                base_sha,
                cap.commit,
                self.project_root / relative,
                timeout=_remaining(deadline),
            )
            patch_path = str(relative)
            results = [*results, credential_check(self.project_root / relative)]
        return Checkpoint(
            id=checkpoint_id,
            attempt_id=attempt_id,
            changed_paths=touched,
            diff_lines=lines,
            base_sha=base_sha,
            head_sha=head_sha,
            checks=results,
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=patch_path,
            commit_sha=cap.commit if self.project_root is not None else None,
        )


__all__ = [
    "CheckpointError",
    "Completion",
    "GitCheckpointCollector",
    "apply_patches",
    "changed_paths",
    "diff_lines",
    "git_head",
    "CREDENTIAL_CHECK",
    "credential_check",
    "write_patch",
]
