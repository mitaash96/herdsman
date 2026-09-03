"""Physical artifact handoff: the bytes an upstream initiative produced.

A reference to a checkpoint is worthless if the consumer cannot read what it
names, so these exercise real git rather than a fake collector.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from herdsman.checkpoint import (
    Completion,
    GitCheckpointCollector,
    apply_patches,
    write_patch,
)
from herdsman.classes import Assignment, Attempt, Checkpoint, Usage
from herdsman.graph import ancestor_patches
from tests.test_dag_run import spec
from tests.test_graph import planned

AT = datetime(2026, 9, 2, tzinfo=UTC)
USAGE = Usage(input_tokens=900, output_tokens=100, source="harness")


def repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _ = subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    _ = subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True
    )
    _ = subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    _ = (path / "tracked.txt").write_text("base\n")
    _ = subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    _ = subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_a_patch_carries_new_modified_and_deleted_files(tmp_path: Path) -> None:
    work = tmp_path / "producer"
    base = repo(work)
    deleted = work / "deleted.txt"
    _ = deleted.write_text("remove me\n")
    _ = subprocess.run(["git", "add", "deleted.txt"], cwd=work, check=True)
    _ = subprocess.run(
        ["git", "commit", "-qm", "add deletion fixture"], cwd=work, check=True
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    deleted.unlink()
    _ = (work / "created.txt").write_text("sentinel\n")  # untracked
    _ = (work / "tracked.txt").write_bytes(b"edited   \n")  # trailing spaces
    binary = b"\x89PNG\r\n\x1a\n\x00\xff\x80"
    _ = (work / "binary.bin").write_bytes(binary)
    patch = tmp_path / "out.patch"
    write_patch(work, base, patch, timeout=30)

    # A plain `git diff` would have missed the untracked file entirely.
    body = patch.read_text()
    assert "created.txt" in body
    assert "sentinel" in body
    assert "tracked.txt" in body
    assert "deleted.txt" in body
    assert "binary.bin" in body

    consumer = tmp_path / "consumer"
    _ = repo(consumer)
    _ = (consumer / "deleted.txt").write_text("remove me\n")
    _ = subprocess.run(["git", "add", "deleted.txt"], cwd=consumer, check=True)
    _ = subprocess.run(
        ["git", "commit", "-qm", "add deletion fixture"], cwd=consumer, check=True
    )
    assert not (consumer / "created.txt").exists()
    apply_patches(consumer, [patch], timeout=30)
    assert (consumer / "created.txt").read_text() == "sentinel\n"
    assert (consumer / "tracked.txt").read_bytes() == b"edited   \n"
    assert not (consumer / "deleted.txt").exists()
    assert (consumer / "binary.bin").read_bytes() == binary
    # Applied inputs are committed, so the consumer's own diff starts clean.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=consumer, capture_output=True, text=True
    ).stdout
    assert status == ""


def test_a_collected_checkpoint_records_a_patch_a_consumer_can_apply(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    work = tmp_path / "producer"
    root.mkdir()
    _ = repo(work)
    _ = (work / "created.txt").write_text("sentinel\n")

    collector = GitCheckpointCollector(checks=("true",), project_root=root)
    base = collector.capture_base(work, timeout=30)
    checkpoint = collector.collect(
        work, "att_1", Completion(exit_code=0, usage=USAGE), base_sha=base, timeout=60
    )

    recorded = checkpoint.patch_path
    assert recorded == f".herdsman/artifacts/{checkpoint.id}.patch"
    assert recorded is not None
    patch = root / recorded
    assert patch.exists()

    consumer = tmp_path / "consumer"
    _ = repo(consumer)
    downstream = GitCheckpointCollector(checks=("true",), project_root=root)
    _ = downstream.capture_base(consumer, inputs=[patch], timeout=30)
    assert (consumer / "created.txt").read_text() == "sentinel\n"


def test_a_consumers_own_patch_excludes_the_inputs_it_started_from(
    tmp_path: Path,
) -> None:
    """Each checkpoint's patch is that initiative's delta and nothing more.

    Otherwise a diamond would apply its shared ancestor's work twice.
    """
    root = tmp_path / "root"
    root.mkdir()
    upstream = tmp_path / "upstream"
    _ = repo(upstream)
    _ = (upstream / "from_upstream.txt").write_text("first\n")
    collector = GitCheckpointCollector(checks=("true",), project_root=root)
    first = collector.collect(
        upstream,
        "att_1",
        Completion(exit_code=0, usage=USAGE),
        base_sha=collector.capture_base(upstream, timeout=30),
        timeout=60,
    )

    downstream = tmp_path / "downstream"
    _ = repo(downstream)
    assert first.patch_path is not None
    upstream_patch = root / first.patch_path
    base = collector.capture_base(downstream, inputs=[upstream_patch], timeout=30)
    _ = (downstream / "from_downstream.txt").write_text("second\n")
    second = collector.collect(
        downstream,
        "att_2",
        Completion(exit_code=0, usage=USAGE),
        base_sha=base,
        timeout=60,
    )

    assert second.changed_paths == ["from_downstream.txt"]
    assert second.patch_path is not None
    body = (root / str(second.patch_path)).read_text()
    assert "from_downstream.txt" in body
    assert "from_upstream.txt" not in body


def test_a_missing_or_unappliable_input_is_a_typed_failure(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _ = repo(work)
    with pytest.raises(Exception, match="missing"):
        apply_patches(work, [tmp_path / "absent.patch"], timeout=30)

    broken = tmp_path / "broken.patch"
    _ = broken.write_text("this is not a patch\n")
    with pytest.raises(Exception, match="git apply"):
        apply_patches(work, [broken], timeout=30)


def test_ancestor_patches_are_transitive_deduplicated_and_ordered() -> None:
    """A diamond: d must rebuild a's work once, before b's and c's."""
    plan = planned(
        spec("a", writes=["a/"]),
        spec("b", depends_on=["a"], writes=["b/"]),
        spec("c", depends_on=["a"], writes=["c/"]),
        spec("d", depends_on=["b", "c"], writes=["d/"]),
    )
    for node in ("a", "b", "c"):
        initiative = plan.initiatives[node]
        initiative.attempts.append(
            _attempt(node, f".herdsman/artifacts/cp_{node}.patch")
        )

    patches = ancestor_patches(plan, "d")
    assert patches == [
        ".herdsman/artifacts/cp_a.patch",
        ".herdsman/artifacts/cp_b.patch",
        ".herdsman/artifacts/cp_c.patch",
    ]
    # a appears once, not once per branch that reaches it.
    assert len(patches) == len(set(patches))
    assert ancestor_patches(plan, "a") == []


def _attempt(node: str, patch_path: str) -> Attempt:
    return Attempt(
        id=f"att_{node}",
        initiative_id=node,
        assignment=Assignment(harness="luna", model="cheap-1"),
        started_at=AT,
        checkpoint=Checkpoint(
            id=f"cp_{node}",
            attempt_id=f"att_{node}",
            usage=USAGE,
            exit_code=0,
            patch_path=patch_path,
        ),
    )
