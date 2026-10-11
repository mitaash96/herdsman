"""Final-plan landing. Git plumbing uses a private index, never the user's index."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .classes import LandingBlock, LandingMode, LandingReason


class LandingError(ValueError):
    def __init__(self, reason: LandingReason, detail: str, paths: list[str] | None = None):
        super().__init__(detail)
        self.block: LandingBlock = LandingBlock(reason=reason, detail=detail, paths=paths or [])


class CasMiss(LandingError):
    """The caller may recompose against the new tip once."""


def _git(root: Path, *args: str, env: dict[str, str] | None = None,
         input: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=root, env=env, input=input,
        text=True, capture_output=True, timeout=30,
    )
    if check and result.returncode:
        raise LandingError("git_error", result.stderr.strip() or result.stdout.strip())
    return result


def is_git(root: Path) -> bool:
    return _git(root, "rev-parse", "--git-dir", check=False).returncode == 0


def tip(root: Path, ref: str) -> str | None:
    if ref.startswith("refs/herdsman/"):
        _check_symbolic(root, ref)
    result = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    if result.returncode not in (0, 1):
        raise LandingError("git_error", result.stderr.strip())
    return result.stdout.strip() or None


def resolve_start(root: Path, name: str | None = None) -> tuple[str, str]:
    if name is None:
        name = _git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
        if not name:
            raise ValueError("project root is detached; name a start branch")
    if not name or _git(root, "check-ref-format", "refs/heads/" + name, check=False).returncode:
        raise ValueError(f"start branch {name} does not exist")
    sha = tip(root, "refs/heads/" + name)
    if sha is None:
        raise ValueError(f"start branch {name} does not exist")
    return name, sha


def protected_branches(root: Path) -> set[str]:
    protected = {"master", "main"}
    remote = _git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", check=False).stdout.strip()
    if remote.startswith("refs/remotes/origin/"):
        protected.add(remote.removeprefix("refs/remotes/origin/"))
    return protected


def validate_target(root: Path, name: str) -> None:
    result = _git(root, "check-ref-format", "--branch", name, check=False)
    # --branch expands @{-1}; accept only literal short branch names.
    if result.returncode or result.stdout.strip() != name or name.startswith("refs/"):
        raise ValueError(f"target branch {name} is not a valid short branch name")
    if name.startswith("herdsman/"):
        raise ValueError("target branch must not use the herdsman/ attempt namespace")
    if name in protected_branches(root):
        raise ValueError(f"target branch {name} is protected")


def derive_target(root: Path, title: str, plan_id: str, targets: Sequence[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or plan_id[5:13]
    name = "herdsman-land/" + slug
    if name in targets or _git(root, "show-ref", "--verify", "--quiet", "refs/heads/" + name, check=False).returncode == 0:
        name += "-" + plan_id[5:13]
    validate_target(root, name)
    return name


def _check_symbolic(root: Path, ref: str) -> None:
    if _git(root, "symbolic-ref", "-q", ref, check=False).returncode == 0:
        raise LandingError("symbolic_target", f"ref {ref} is symbolic; refusing to follow it")


def pin(root: Path, ref: str, sha: str, *, previous: str | None = None) -> None:
    _check_symbolic(root, ref)
    _ = _git(root, "update-ref", "--no-deref", ref, sha, previous or "")


def _worktrees(root: Path) -> list[tuple[Path, str]]:
    records = _git(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0\0")
    trees: list[tuple[Path, str]] = []
    for record in records:
        fields = record.split("\0")
        path = next((field[9:] for field in fields if field.startswith("worktree ")), None)
        branch = next((field[7:] for field in fields if field.startswith("branch ")), None)
        if path is not None and branch is not None:
            trees.append((Path(path).resolve(), branch))
    return trees


def list_branches(root: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "git": False, "current": None, "detached": False, "branches": [],
        "protected": [], "default_target_prefix": "herdsman-land/",
    }
    if not is_git(root):
        return result
    current = _git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip() or None
    protected = protected_branches(root)
    checked = {branch for _, branch in _worktrees(root)}
    refs = _git(root, "for-each-ref", "--sort=refname", "--format=%(refname) %(objectname)", "refs/heads/")
    branches: list[dict[str, object]] = []
    for line in refs.stdout.splitlines():
        ref, sha = line.split(" ", 1)
        name = ref.removeprefix("refs/heads/")
        if not name.startswith("herdsman/"):
            branches.append({"name": name, "sha": sha, "checked_out": ref in checked, "protected": name in protected})
    result.update(git=True, current=current, detached=current is None,
                  branches=branches, protected=sorted(protected))
    return result


def tree_of(root: Path, sha: str) -> str:
    return _git(root, "rev-parse", f"{sha}^{{tree}}").stdout.strip()


@dataclass(frozen=True)
class LandingCommit:
    sha: str | None
    previous_sha: str | None
    mode: LandingMode


def commit_message(root: Path, sha: str) -> str:
    return _git(root, "show", "-s", "--format=%B", sha).stdout


def refuse_symbolic_target(root: Path, target: str) -> None:
    _check_symbolic(root, "refs/heads/" + target)


def _commit(root: Path, tree: str, parents: Sequence[str], message: str) -> str:
    env = dict(os.environ)
    for role in ("AUTHOR", "COMMITTER"):
        identity = _git(root, "var", f"GIT_{role}_IDENT", check=False)
        if identity.returncode:
            env[f"GIT_{role}_NAME"] = "herdsman"
            env[f"GIT_{role}_EMAIL"] = "herdsman@localhost"
    args = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    return _git(root, *args, input=message, env=env).stdout.strip()


def land_commit(root: Path, tree: str, base_sha: str, owned_tip: str,
                current: str | None, message: str) -> LandingCommit:
    if current is not None and tree == _git(root, "rev-parse", current + "^{tree}").stdout.strip():
        return LandingCommit(None, current, "empty")
    if current is None:
        return LandingCommit(_commit(root, tree, [base_sha], message), None, "created")
    if current == owned_tip:
        return LandingCommit(_commit(root, tree, [current], message), current, "fast_forward")
    proposed = _commit(root, tree, [base_sha], message)
    merged = _git(root, "merge-tree", "--write-tree", current, proposed, check=False)
    if merged.returncode == 1:
        # merge-tree lists unmerged index records before its human-readable messages.
        paths = sorted({line.split("\t", 1)[1] for line in merged.stdout.split("\n\n", 1)[0].splitlines() if "\t" in line})
        raise LandingError("conflict", merged.stdout.strip(), paths)
    if merged.returncode:
        raise LandingError("git_error", merged.stderr.strip())
    merged_tree = merged.stdout.splitlines()[0]
    return LandingCommit(_commit(root, merged_tree, [current, proposed], message), current, "merge")


def write_target(root: Path, target: str, commit: LandingCommit, landed_ref: str) -> None:
    ref = "refs/heads/" + target
    _check_symbolic(root, ref)
    if commit.sha is None:
        return
    checked = [path for path, branch in _worktrees(root) if branch == ref]
    if any(path != root.resolve() for path in checked):
        raise LandingError("checked_out_elsewhere", f"target {target} is checked out in another worktree")
    detail = f"commit or stash your changes on {target}, then retry landing"
    if checked:
        if (_git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
                or tip(root, "HEAD") != commit.previous_sha):
            raise LandingError("dirty_checkout", detail)
    pin(root, landed_ref, commit.sha, previous=tip(root, landed_ref))
    if checked:
        result = _git(root, "merge", "--ff-only", commit.sha, check=False)
        if result.returncode:
            raise LandingError("dirty_checkout", detail + "\n" + result.stderr.strip())
    else:
        result = _git(root, "update-ref", "--no-deref", ref, commit.sha, commit.previous_sha or "", check=False)
        if result.returncode:
            raise CasMiss("git_error", result.stderr.strip())
