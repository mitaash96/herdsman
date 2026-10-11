"""Git plumbing for plan integration: owned refs, private-index capture, merge-tree.

Never checks out, resets or writes a real index; ref writes stay under
`refs/herdsman/` and are no-deref compare-and-swap.
"""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .checkpoint import CheckpointError

_OWNED = ("refs/herdsman/",)


class MergeConflict(CheckpointError):
    paths: tuple[str, ...]
    tree: str  # conflicted tree OID, markers included

    def __init__(self, paths: tuple[str, ...], tree: str) -> None:
        super().__init__(f"merge conflict in {', '.join(paths)}")
        self.paths = paths
        self.tree = tree


class RefConflict(CheckpointError):
    ref: str
    actual: str | None
    symbolic: bool

    def __init__(self, ref: str, actual: str | None, *, symbolic: bool) -> None:
        super().__init__(f"ref {ref} is {'symbolic' if symbolic else actual or 'absent'}")
        self.ref = ref
        self.actual = actual
        self.symbolic = symbolic


@dataclass(frozen=True)
class Capture:
    commit: str
    tree: str


def base_pin(plan_id: str) -> str:
    return f"refs/herdsman/plans/{plan_id}/base"


def integration_ref(plan_id: str) -> str:
    return f"refs/herdsman/plans/{plan_id}/integration"


def input_pin(attempt_id: str) -> str:
    return f"refs/herdsman/inputs/{attempt_id}"


def checkpoint_pin(checkpoint_id: str) -> str:
    return f"refs/herdsman/checkpoints/{checkpoint_id}"


def conflict_pin(checkpoint_id: str, tip: str) -> str:
    return f"refs/herdsman/conflicts/{checkpoint_id}/{tip[:12]}"


def _owned(ref: str) -> str:
    if not ref.startswith(_OWNED) or ".." in ref:
        raise ValueError(f"{ref} is outside Herdsman-owned ref namespaces")
    return ref


def _env(at: datetime) -> dict[str, str]:
    stamp = at.isoformat()
    return {
        "GIT_AUTHOR_NAME": "herdsman",
        "GIT_AUTHOR_EMAIL": "herdsman@localhost",
        "GIT_COMMITTER_NAME": "herdsman",
        "GIT_COMMITTER_EMAIL": "herdsman@localhost",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }


def _run(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    ok: Sequence[int] = (0,),
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            env={**os.environ, **(env or {})},
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"git {' '.join(args)} timed out") from exc
    except OSError as exc:
        raise CheckpointError(f"git {' '.join(args)} failed: {exc}") from exc
    if result.returncode not in ok:
        err = result.stderr.decode(errors="replace").strip()
        raise CheckpointError(f"git {' '.join(args)} failed ({result.returncode}): {err}")
    return result


def _git(root: Path, *args: str, env: dict[str, str] | None = None, timeout: float | None = None) -> str:
    return _run(root, *args, env=env, timeout=timeout).stdout.decode().strip()


def commit(
    root: Path,
    tree: str,
    parents: Sequence[str],
    *,
    at: datetime,
    message: str,
    timeout: float | None = None,
) -> str:
    args = ["commit-tree", tree, "-m", message]
    for parent in parents:
        args += ["-p", parent]
    return _git(root, *args, env=_env(at), timeout=timeout)


def capture(
    worktree: Path,
    parents: Sequence[str],
    *,
    at: datetime,
    message: str,
    exclude: Sequence[str] = (".herdsman/handoffs",),
    timeout: float | None = None,
) -> Capture:
    """Snapshot the worktree through a private index; the real one is never written.

    The index is seeded from the real one (keeps tracked-but-ignored files), then
    `add -A`, then the excluded paths are dropped from the private index.
    """
    real = Path(
        _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index", timeout=timeout)
    )
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        if real.exists():
            _ = shutil.copyfile(real, index)
        else:
            _ = _git(worktree, "read-tree", parents[0], env=env, timeout=timeout)
        _ = _git(worktree, "add", "-A", env=env, timeout=timeout)
        for path in exclude:
            _ = _git(
                worktree, "rm", "-r", "--cached", "--ignore-unmatch", "--quiet", "--", path,
                env=env, timeout=timeout,
            )
        tree = _git(worktree, "write-tree", env=env, timeout=timeout)
    return Capture(commit(worktree, tree, parents, at=at, message=message, timeout=timeout), tree)


def _is_ancestor(root: Path, a: str, b: str, timeout: float | None) -> bool:
    return _run(root, "merge-base", "--is-ancestor", a, b, ok=(0, 1), timeout=timeout).returncode == 0


def merge(
    root: Path,
    ours: str,
    theirs: str,
    *,
    at: datetime,
    message: str,
    timeout: float | None = None,
) -> str:
    if _is_ancestor(root, theirs, ours, timeout):
        return ours
    if _is_ancestor(root, ours, theirs, timeout):
        return theirs
    out = _run(
        root, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", ours, theirs,
        ok=(0, 1), timeout=timeout,
    )
    fields = out.stdout.split(b"\0")
    tree = fields[0].decode()
    if out.returncode == 1:
        paths = tuple(
            dict.fromkeys(f.decode("utf-8", "surrogateescape") for f in fields[1:] if f)
        )
        raise MergeConflict(paths, tree)
    return commit(root, tree, [ours, theirs], at=at, message=message, timeout=timeout)


def input_commit(
    root: Path,
    base: str,
    parents: Sequence[str],
    *,
    at: datetime,
    timeout: float | None = None,
) -> str:
    """Deterministic binary merge chain of the ancestors' commits; base when none."""
    if not parents:
        return base
    acc = parents[0]
    for parent in parents[1:]:
        acc = merge(root, acc, parent, at=at, message="herdsman: input", timeout=timeout)
    return acc


def read_ref(root: Path, ref: str, *, timeout: float | None = None) -> str | None:
    out = _run(
        root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok=(0, 1), timeout=timeout
    )
    return out.stdout.decode().strip() or None


def _refuse_symbolic(root: Path, ref: str) -> None:
    if _run(root, "symbolic-ref", "-q", ref, ok=(0, 1, 128)).returncode == 0:
        raise RefConflict(ref, read_ref(root, ref), symbolic=True)


def advance(root: Path, ref: str, new: str, old: str | None) -> None:
    """--no-deref CAS; empty old means must-not-exist."""
    _ = _owned(ref)
    _refuse_symbolic(root, ref)
    out = _run(root, "update-ref", "--no-deref", ref, new, old or "", ok=(0, 128))
    if out.returncode:
        actual = read_ref(root, ref)
        if actual == old:  # not a CAS miss: lock or permission failure
            raise CheckpointError(f"update-ref {ref} failed: {out.stderr.decode().strip()}")
        raise RefConflict(ref, actual, symbolic=False)


def pin(root: Path, ref: str, sha: str) -> None:
    """Create-only; an identical existing pin is a no-op."""
    _ = _owned(ref)
    _refuse_symbolic(root, ref)
    current = read_ref(root, ref)
    if current == sha:
        return
    if current is not None:
        raise RefConflict(ref, current, symbolic=False)
    advance(root, ref, sha, None)
