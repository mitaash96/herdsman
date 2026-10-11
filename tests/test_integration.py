"""Plan-integration git plumbing against real git."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import herdsman.integration as gi
from herdsman.checkpoint import CheckpointError
from tests.test_handoff import repo

AT = datetime(2026, 10, 2, tzinfo=UTC)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def sibling(path: Path, base: str, name: str, text: str) -> str:
    """A commit on `base` that writes `text` to `name`, via a private capture."""
    _ = git(path, "checkout", "-q", "--detach", base)
    _ = (path / name).write_text(text)
    sha = gi.capture(path, [base], at=AT, message=name).commit
    _ = git(path, "checkout", "-q", "-f", "--detach", base)
    _ = git(path, "clean", "-fdq")
    return sha


def test_capture_takes_full_worktree_state_without_touching_index_or_head(tmp_path: Path) -> None:
    base = repo(tmp_path)
    _ = (tmp_path / "tracked.txt").write_text("edited\n")
    _ = (tmp_path / "new.txt").write_text("n\n")
    _ = git(tmp_path, "add", "tracked.txt")
    _ = (tmp_path / "tracked.txt").write_text("edited again\n")
    index_before = (tmp_path / ".git" / "index").read_bytes()
    cap = gi.capture(tmp_path, [base], at=AT, message="cp")
    assert git(tmp_path, "show", f"{cap.commit}:tracked.txt") == "edited again"
    assert git(tmp_path, "show", f"{cap.commit}:new.txt") == "n"
    assert (tmp_path / ".git" / "index").read_bytes() == index_before
    assert git(tmp_path, "rev-parse", "HEAD") == base


def test_capture_excludes_handoff_docs_even_without_ignore_rules(tmp_path: Path) -> None:
    base = repo(tmp_path)
    (tmp_path / ".herdsman" / "handoffs").mkdir(parents=True)
    _ = (tmp_path / ".herdsman" / "handoffs" / "a.md").write_text("doc\n")
    _ = (tmp_path / "new.txt").write_text("n\n")
    cap = gi.capture(tmp_path, [base], at=AT, message="cp")
    files = git(tmp_path, "ls-tree", "-r", "--name-only", cap.commit).split()
    assert ".herdsman/handoffs/a.md" not in files and "new.txt" in files


def test_capture_drops_a_committed_handoff_doc(tmp_path: Path) -> None:
    _ = repo(tmp_path)
    (tmp_path / ".herdsman" / "handoffs").mkdir(parents=True)
    _ = (tmp_path / ".herdsman" / "handoffs" / "a.md").write_text("doc\n")
    _ = git(tmp_path, "add", "-A")
    _ = git(tmp_path, "commit", "-qm", "doc")
    parent = git(tmp_path, "rev-parse", "HEAD")
    cap = gi.capture(tmp_path, [parent], at=AT, message="cp")
    files = git(tmp_path, "ls-tree", "-r", "--name-only", cap.commit).split()
    assert ".herdsman/handoffs/a.md" not in files and "tracked.txt" in files


def test_capture_keeps_tracked_ignored_files(tmp_path: Path) -> None:
    _ = repo(tmp_path)
    _ = (tmp_path / ".gitignore").write_text("kept.txt\nnew-ignored.txt\n")
    _ = (tmp_path / "kept.txt").write_text("k\n")
    _ = git(tmp_path, "add", "-f", ".gitignore", "kept.txt")
    _ = git(tmp_path, "commit", "-qm", "ignored but tracked")
    parent = git(tmp_path, "rev-parse", "HEAD")
    _ = (tmp_path / "new-ignored.txt").write_text("x\n")
    cap = gi.capture(tmp_path, [parent], at=AT, message="cp")
    files = git(tmp_path, "ls-tree", "-r", "--name-only", cap.commit).split()
    assert "kept.txt" in files and "new-ignored.txt" not in files


def test_capture_records_deletions_and_binary(tmp_path: Path) -> None:
    base = repo(tmp_path)
    (tmp_path / "tracked.txt").unlink()
    _ = (tmp_path / "b.bin").write_bytes(b"\0\1\2")
    cap = gi.capture(tmp_path, [base], at=AT, message="cp")
    assert git(tmp_path, "ls-tree", "-r", "--name-only", cap.commit).split() == ["b.bin"]


def test_capture_is_repeatable(tmp_path: Path) -> None:
    base = repo(tmp_path)
    _ = (tmp_path / "tracked.txt").write_text("x\n")
    assert gi.capture(tmp_path, [base], at=AT, message="cp") == gi.capture(
        tmp_path, [base], at=AT, message="cp"
    )


def test_merge_fast_forwards_either_way_and_merges_disjoint_siblings(tmp_path: Path) -> None:
    base = repo(tmp_path)
    a = sibling(tmp_path, base, "a.txt", "a\n")
    b = sibling(tmp_path, base, "b.txt", "b\n")
    assert gi.merge(tmp_path, base, a, at=AT, message="m") == a
    assert gi.merge(tmp_path, a, base, at=AT, message="m") == a
    merged = gi.merge(tmp_path, a, b, at=AT, message="m")
    assert git(tmp_path, "rev-list", "--parents", "-n1", merged).split()[1:] == [a, b]
    files = git(tmp_path, "ls-tree", "-r", "--name-only", merged).split()
    assert "a.txt" in files and "b.txt" in files


@pytest.mark.parametrize("name", ["tracked.txt", "x\nq", "tab\there", "ünï.txt"])
def test_conflict_paths_are_exact_bytes(tmp_path: Path, name: str) -> None:
    base = repo(tmp_path)
    a = sibling(tmp_path, base, name, "one\n")
    b = sibling(tmp_path, base, name, "two\n")
    with pytest.raises(gi.MergeConflict) as exc:
        _ = gi.merge(tmp_path, a, b, at=AT, message="m")
    assert exc.value.paths == (name,)
    blob = subprocess.run(
        ["git", "cat-file", "blob", f"{exc.value.tree}:{name}"],
        cwd=tmp_path, check=True, capture_output=True,
    ).stdout
    assert b"<<<<<<<" in blob


def test_merge_with_invalid_object_is_plumbing_error_not_conflict(tmp_path: Path) -> None:
    base = repo(tmp_path)
    with pytest.raises(CheckpointError) as exc:
        _ = gi.merge(tmp_path, base, "f" * 40, at=AT, message="m")
    assert not isinstance(exc.value, gi.MergeConflict)


def test_input_commit_is_a_deterministic_binary_merge_chain(tmp_path: Path) -> None:
    base = repo(tmp_path)
    a = sibling(tmp_path, base, "a.txt", "a\n")
    b = sibling(tmp_path, base, "b.txt", "b\n")
    c = sibling(tmp_path, base, "c.txt", "c\n")
    assert gi.input_commit(tmp_path, base, [], at=AT) == base
    assert gi.input_commit(tmp_path, base, [a], at=AT) == a
    out = gi.input_commit(tmp_path, base, [b, c], at=AT)
    assert git(tmp_path, "rev-list", "--parents", "-n1", out).split()[1:] == [b, c]
    assert gi.input_commit(tmp_path, base, [b, c], at=AT) == out


def test_advance_is_cas_and_never_follows_symbolic_refs(tmp_path: Path) -> None:
    base = repo(tmp_path)
    a = sibling(tmp_path, base, "a", "a")
    ref = gi.integration_ref("p1")
    gi.advance(tmp_path, ref, base, None)
    gi.advance(tmp_path, ref, a, base)
    with pytest.raises(gi.RefConflict):
        gi.advance(tmp_path, ref, base, base)
    with pytest.raises(gi.RefConflict):
        gi.advance(tmp_path, ref, base, None)
    sym = "refs/herdsman/sym"
    _ = git(tmp_path, "branch", "user", base)
    _ = git(tmp_path, "symbolic-ref", sym, "refs/heads/user")
    with pytest.raises(gi.RefConflict) as exc:
        gi.advance(tmp_path, sym, a, base)
    assert exc.value.symbolic and git(tmp_path, "rev-parse", "user") == base


def test_pin_is_create_only_and_idempotent(tmp_path: Path) -> None:
    base = repo(tmp_path)
    a = sibling(tmp_path, base, "a", "a")
    b = sibling(tmp_path, base, "b", "b")
    ref = gi.input_pin("att")
    gi.pin(tmp_path, ref, a)
    gi.pin(tmp_path, ref, a)
    assert gi.read_ref(tmp_path, ref) == a
    with pytest.raises(gi.RefConflict):
        gi.pin(tmp_path, ref, b)
    sym = "refs/herdsman/symlink"
    _ = git(tmp_path, "symbolic-ref", sym, ref)
    with pytest.raises(gi.RefConflict):
        gi.pin(tmp_path, sym, a)
    assert gi.read_ref(tmp_path, "refs/herdsman/absent") is None


def test_name_helpers() -> None:
    assert gi.base_pin("p") == "refs/herdsman/plans/p/base"
    assert gi.checkpoint_pin("c") == "refs/herdsman/checkpoints/c"
    assert gi.conflict_pin("c", "a" * 40) == f"refs/herdsman/conflicts/c/{'a' * 12}"


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/heads/herdsman-land/x"])
def test_refs_outside_owned_namespaces_are_refused(tmp_path: Path, ref: str) -> None:
    with pytest.raises(ValueError):
        gi.advance(tmp_path, ref, "f" * 40, None)


def test_empty_old_value_works_on_sha256_repos(tmp_path: Path) -> None:
    _ = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256"], cwd=tmp_path, check=True
    )
    _ = git(tmp_path, "config", "user.email", "t@example.invalid")
    _ = git(tmp_path, "config", "user.name", "T")
    _ = (tmp_path / "f").write_text("f\n")
    _ = git(tmp_path, "add", "-A")
    _ = git(tmp_path, "commit", "-qm", "base")
    head = git(tmp_path, "rev-parse", "HEAD")
    gi.advance(tmp_path, gi.base_pin("p"), head, None)
    assert gi.read_ref(tmp_path, gi.base_pin("p")) == head
