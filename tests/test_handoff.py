"""Physical artifact handoff: the bytes an upstream initiative produced.

A reference to a checkpoint is worthless if the consumer cannot read what it
names, so these exercise real git rather than a fake collector.
"""

import asyncio
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from herdsman.checkpoint import (
    CREDENTIAL_CHECK,
    credential_check,
    Completion,
    GitCheckpointCollector,
    apply_patches,
    write_patch,
)
from herdsman.integration import capture, checkpoint_pin, read_ref
from herdsman.classes import (
    Assignment,
    Attempt,
    AttemptStarted,
    Checkpoint,
    Contract,
    InitiativeFailed,
    InitiativeSettled,
    Usage,
    handoff_path,
    validate_checkpoint,
)
from herdsman.daemon import _snapshot_handoff  # pyright: ignore[reportPrivateUsage]
from herdsman.daemon import Daemon
from herdsman.graph import ancestor_patches
from herdsman.herdr import AgentLaunch
from herdsman.integration import input_pin
from tests.test_dag_run import spec as dag_spec
from tests.test_landing import (
    PLAN_ID,
    Planner,
    Runtime as LandingRuntime,
    create,
    daemon,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    git,
    settle,
)
from tests.test_graph import planned, spec

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


def worktree(main: Path, name: str) -> Path:
    """A linked worktree of `main`, shaped like herdr's."""
    path = main.parent / name
    _ = subprocess.run(
        ["git", "worktree", "add", "-q", "-b", name, str(path)], cwd=main, check=True
    )
    return path


def snap(work: Path, base: str) -> str:
    return capture(work, [base], at=AT, message="snap").commit


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
    write_patch(work, base, snap(work, base), patch, timeout=30)

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
    _ = repo(root)
    work = worktree(root, "producer")
    _ = (work / "created.txt").write_text("sentinel\n")

    collector = GitCheckpointCollector(checks=("true",), project_root=root)
    base = collector.capture_base(work, timeout=30)
    checkpoint = collector.collect(
        work, "att_1", Completion(), base_sha=base, timeout=60
    )

    assert checkpoint.exit_code is None and checkpoint.usage is None
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
    _ = repo(root)
    upstream = worktree(root, "upstream")
    _ = (upstream / "from_upstream.txt").write_text("first\n")
    collector = GitCheckpointCollector(checks=("true",), project_root=root)
    first = collector.collect(
        upstream,
        "att_1",
        Completion(),
        base_sha=collector.capture_base(upstream, timeout=30),
        timeout=60,
    )

    downstream = worktree(root, "downstream")
    assert first.patch_path is not None
    upstream_patch = root / first.patch_path
    base = collector.capture_base(downstream, inputs=[upstream_patch], timeout=30)
    _ = (downstream / "from_downstream.txt").write_text("second\n")
    second = collector.collect(
        downstream,
        "att_2",
        Completion(),
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
        attempt = _attempt(node, f".herdsman/artifacts/cp_{node}.patch")
        initiative.attempts.append(attempt)
        initiative.checkpoint_versions.append(attempt.checkpoint)  # pyright: ignore[reportArgumentType]
        initiative.settled_checkpoint_id = f"cp_{node}"

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


def test_handoff_patchcredential_check_reports_without_rewriting(tmp_path: Path) -> None:
    """A secret in a handoff patch is evidence for the gate, not a silent scrub.

    Rewriting the patch to hide the value would leave a diff that no longer
    applies, so the patch must survive byte-for-byte while the checkpoint
    carries a failed check a human sees before approving the handoff.
    """
    work = tmp_path / "work"
    work.mkdir()
    base = repo(work)
    leaked = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx"
    _ = (work / "config.env").write_text(leaked + "\n")
    patch = tmp_path / "leak.patch"
    write_patch(work, base, snap(work, base), patch, timeout=30)

    assert leaked in patch.read_text()  # the patch still applies
    finding = credential_check(patch)
    assert finding.name == CREDENTIAL_CHECK
    assert not finding.passed
    assert "review before approving" in finding.summary

    clean = tmp_path / "clean.patch"
    _ = clean.write_text("diff --git a/a.txt b/a.txt\n+hello\n")
    assert credential_check(clean).passed


def test_credential_check_ignores_attribute_references(tmp_path: Path) -> None:
    code = tmp_path / "code.patch"
    _ = code.write_text("+                 token_cap=request.token_cap, plan_id=request.plan_id,\n")
    assert credential_check(code).passed
    jwt = tmp_path / "jwt.patch"
    _ = jwt.write_text("+token=eyJhbGc.eyJzdWI.SflKxw\n")
    assert not credential_check(jwt).passed


def collect_in(root: Path, work: Path) -> Checkpoint:
    collector = GitCheckpointCollector(checks=("true",), project_root=root)
    base = collector.capture_base(work, timeout=30)
    return collector.collect(work, "att_1", Completion(), base_sha=base, timeout=60)


def test_checkpoint_commit_patch_and_paths_agree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)
    _ = (root / "gone.txt").write_text("x\n")
    _ = subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    _ = subprocess.run(["git", "commit", "-qm", "gone"], cwd=root, check=True)
    work = worktree(root, "w")
    (work / "gone.txt").unlink()
    _ = (work / "tracked.txt").write_text("edited\n")
    _ = (work / "new.txt").write_text("new\n")
    _ = (work / "b.bin").write_bytes(b"\x00\xff\x80")

    cp = collect_in(root, work)

    assert set(cp.changed_paths) == {"gone.txt", "tracked.txt", "new.txt", "b.bin"}
    assert cp.diff_lines == 4  # gone -1, tracked +1/-1, new +1; binary uncounted
    assert cp.patch_path is not None
    body = (root / cp.patch_path).read_text(errors="replace")
    assert all(name in body for name in cp.changed_paths)


def test_collect_does_not_write_the_real_index(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)
    work = worktree(root, "w")
    index = Path(
        subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"],
            cwd=work, check=True, capture_output=True, text=True,
        ).stdout.strip()
    )
    _ = (work / "new.txt").write_text("new\n")
    before = index.read_bytes()

    _ = collect_in(root, work)

    assert index.read_bytes() == before


def test_checkpoint_commit_is_pinned(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)
    work = worktree(root, "w")
    _ = (work / "new.txt").write_text("new\n")

    cp = collect_in(root, work)

    assert cp.commit_sha is not None
    assert read_ref(root, checkpoint_pin(cp.id)) == cp.commit_sha


def test_artifacts_and_handoffs_are_not_in_the_capture(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)  # no ignore rules
    work = worktree(root, "w")
    doc = work / ".herdsman" / "handoffs" / "a.md"
    doc.parent.mkdir(parents=True)
    _ = doc.write_text("notes\n")
    _ = (work / "new.txt").write_text("new\n")

    cp = collect_in(root, work)

    assert cp.commit_sha is not None
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", cp.commit_sha],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.split()
    assert "new.txt" in tree
    assert not any(name.startswith(".herdsman/") for name in tree)
    assert cp.changed_paths == ["new.txt"]


def test_handoff_snapshot_is_recorded_and_validates(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)
    work = worktree(root, "w")
    doc = work / handoff_path("a")
    doc.parent.mkdir(parents=True)
    _ = doc.write_bytes(b"# handoff\n")
    _ = (work / "new.txt").write_text("new\n")
    contract = Contract(id="h", handoff=True)
    node = spec("a", writes=["new.txt"]).model_copy(update={"contract": contract})
    plan = planned(node)
    cp = collect_in(root, work)
    assert handoff_path("a") not in cp.changed_paths

    snapped = _snapshot_handoff(root, plan, "a", cp, work)

    assert snapped.handoff_artifact == f".herdsman/artifacts/{cp.id}.handoff.md"
    assert snapped.handoff_artifact is not None
    assert (root / snapped.handoff_artifact).read_bytes() == b"# handoff\n"
    assert handoff_path("a") in snapped.changed_paths
    violations = validate_checkpoint(node, snapped, contract)
    assert not [v for v in violations if v.code == "missing-artifact"]
    assert snapped.commit_sha is not None
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", snapped.commit_sha],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    assert "handoffs" not in tree
    assert snapped.patch_path is not None
    assert "handoff" not in (root / snapped.patch_path).read_text()


def test_credential_check_scans_the_derived_patch(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _ = repo(root)
    work = worktree(root, "w")
    _ = (work / "config.env").write_text("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx\n")

    cp = collect_in(root, work)

    assert any(c.name == CREDENTIAL_CHECK and not c.passed for c in cp.checks)


# --- T4: pinned ancestors-only input commit ---------------------------------


class _EdgePlanner(Planner):
    def __init__(self, edges: dict[str, list[str]]) -> None:
        super().__init__(*edges)
        self.edges: dict[str, list[str]] = edges

    @override
    def propose(self, brief: str) -> object:
        return {"title": "Inputs", "initiatives": [
            dag_spec(node, writes=[f"{node}.txt"], depends_on=deps).model_dump(mode="json")
            for node, deps in self.edges.items()
        ]}


class _DocRuntime(LandingRuntime):
    """Also writes the node's handoff doc, as an agent would."""

    handoff_seen: dict[str, str] = {}

    @override
    async def run(self, worktree_ref: str, launch: AgentLaunch) -> str:
        pane = await super().run(worktree_ref, launch)
        directory = Path(worktree_ref) / ".herdsman/handoffs"
        self.handoff_seen = {f.name: f.read_text() for f in directory.glob("*.md")}
        doc = Path(worktree_ref) / handoff_path(self.node)
        doc.parent.mkdir(parents=True, exist_ok=True)
        _ = doc.write_text(f"doc {self.node}\n")
        return pane


def _commit_of(daemon: Daemon, node: str) -> str:
    cp = daemon.plan(PLAN_ID).initiatives[node].settled_checkpoint()
    assert cp is not None and cp.commit_sha is not None
    return cp.commit_sha


async def _edges(daemon: Daemon, edges: dict[str, list[str]]) -> None:
    planner = _EdgePlanner(edges)
    daemon._frontier_planner = planner.factory  # pyright: ignore[reportPrivateUsage]
    _ = await create(daemon, planner=planner, target=None)


def test_chain_child_starts_on_pinned_parent_commit_with_handoff_doc(daemon: Daemon) -> None:
    async def scenario() -> None:
        await _edges(daemon, {"a": [], "b": ["a"]})
        _ = await settle(daemon, "a", runtime=_DocRuntime(daemon.project_root))
        runtime = _DocRuntime(daemon.project_root)
        _ = await settle(daemon, "b", runtime=runtime)
        started = next(
            e for e in daemon.store.read(PLAN_ID)
            if isinstance(e, AttemptStarted) and e.initiative_id == "b"
        )
        assert runtime.bases == [input_pin(started.attempt_id)]
        assert git(daemon.project_root, "rev-parse", runtime.bases[0] or "") == _commit_of(daemon, "a")
        assert started.input_checkpoints == [daemon.plan(PLAN_ID).initiatives["a"].settled_checkpoint_id]
        assert runtime.handoff_seen == {"a.md": "doc a\n"}
        # The ancestor's doc reached the child outside git: not in b's capture.
        cp = daemon.plan(PLAN_ID).initiatives["b"].settled_checkpoint()
        assert cp is not None and not any(p.startswith(".herdsman/handoffs/a") for p in cp.changed_paths)
        tree = git(daemon.project_root, "ls-tree", "-r", "--name-only", _commit_of(daemon, "b"))
        assert "a.md" not in tree

    asyncio.run(scenario())


def test_diamond_input_merges_ancestors_in_order_and_omits_unrelated_root(daemon: Daemon) -> None:
    async def scenario() -> None:
        await _edges(daemon, {"a": [], "b": ["a"], "c": ["a"], "s": [], "d": ["b", "c"]})
        for node in ("a", "s", "b", "c"):
            _ = await settle(daemon, node)
        runtime = LandingRuntime(daemon.project_root)
        _ = await settle(daemon, "d", runtime=runtime)
        root = daemon.project_root
        head = git(root, "rev-parse", runtime.bases[0] or "")
        # a fast-forwards into b; c joins as the second parent.
        assert git(root, "show", "-s", "--format=%P", head).split() == [
            _commit_of(daemon, "b"), _commit_of(daemon, "c"),
        ]
        files = git(root, "ls-tree", "-r", "--name-only", head).splitlines()
        assert {"a.txt", "b.txt", "c.txt"} <= set(files) and "s.txt" not in files

    asyncio.run(scenario())


def test_ancestor_conflict_fails_typed_without_a_worktree(daemon: Daemon) -> None:
    async def scenario() -> None:
        await _edges(daemon, {"b": [], "c": [], "d": ["b", "c"]})
        _ = await settle(daemon, "b", runtime=LandingRuntime(daemon.project_root, file="x.txt", text="b\n"))
        # Settling c normally conflicts at integration; bypass it to reach the input guard.
        cp = await daemon.run_initiative(
            PLAN_ID, "c", runtime=LandingRuntime(daemon.project_root, file="x.txt", text="c\n"),
            collector=GitCheckpointCollector(checks=("true",), project_root=daemon.project_root),
        )
        assert cp is not None
        _ = daemon.append(InitiativeSettled(
            plan_id=PLAN_ID, at=datetime.now(UTC), initiative_id="c", checkpoint_id=cp.id,
        ))
        runtime = LandingRuntime(daemon.project_root)
        with pytest.raises(Exception, match="x.txt"):
            _ = await daemon.run_initiative(
                PLAN_ID, "d", runtime=runtime,
                collector=GitCheckpointCollector(checks=("true",), project_root=daemon.project_root),
            )
        assert runtime.bases == []
        d = daemon.plan(PLAN_ID).initiatives["d"]
        assert d.state == "failed" and d.attempts[-1].ended_at is not None
        failed = [e for e in daemon.store.read(PLAN_ID) if isinstance(e, InitiativeFailed)]
        assert "x.txt" in failed[-1].reason

    asyncio.run(scenario())


def test_timeout_while_building_the_input_fails_typed(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        await _edges(daemon, {"a": []})
        def slow_build(*_a: object) -> None:
            time.sleep(0.5)

        monkeypatch.setattr("herdsman.daemon._build_input", slow_build)
        runtime = LandingRuntime(daemon.project_root)
        with pytest.raises(RuntimeError, match="timed out"):
            _ = await daemon.run_initiative(
                PLAN_ID, "a", runtime=runtime, timeout=0.05,
                collector=GitCheckpointCollector(checks=("true",), project_root=daemon.project_root),
            )
        assert runtime.bases == []
        assert daemon.plan(PLAN_ID).initiatives["a"].state == "failed"

    asyncio.run(scenario())
