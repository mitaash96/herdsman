"""T5: settle merges into the plan's integration ref; conflicts block every settle path.

Real EventStore, real repo, real linked worktrees; only the agent runtime is faked.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from herdsman.checkpoint import GitCheckpointCollector
from herdsman.classes import (
    Checkpoint,
    CheckpointApproved,
    CheckpointRejected,
    InitiativeSettled,
    IntegrationConflicted,
    Plan,
    PlanProposed,
)
from herdsman.daemon import Daemon, IntegrationConflictError
from tests.test_dag_run import spec
from tests.test_landing import (  # noqa: F401  (daemon is a fixture)
    PLAN_ID,
    Planner,
    Runtime,
    checkout,
    create,
    daemon,
    git,
    refs,
)

IREF = f"refs/herdsman/plans/{PLAN_ID}/integration"


class Graph(Planner):
    """Nodes with explicit deps; `gated` nodes require approval."""

    def __init__(self, deps: dict[str, list[str]], gated: tuple[str, ...] = ()) -> None:
        super().__init__(*deps)
        self.deps: dict[str, list[str]] = deps
        self.gated: tuple[str, ...] = gated

    @override
    def propose(self, brief: str) -> object:
        return {"title": "T5", "initiatives": [
            spec(n, writes=[f"{n}.txt", "tracked.txt"], depends_on=d).model_copy(
                update={"approval": "required" if n in self.gated else "automatic"}
            ).model_dump(mode="json")
            for n, d in self.deps.items()
        ]}


def collector(daemon: Daemon) -> GitCheckpointCollector:
    return GitCheckpointCollector(checks=("true",), project_root=daemon.project_root)


async def run(daemon: Daemon, node: str, *, file: str | None = None, text: str | None = None,
              settle: bool = True) -> Checkpoint:
    runtime = Runtime(daemon.project_root, file=file, text=text)
    if settle:
        cp = await daemon.run_and_settle(PLAN_ID, node, runtime=runtime, collector=collector(daemon))
    else:
        cp = await daemon.run_initiative(PLAN_ID, node, runtime=runtime, collector=collector(daemon))
    assert cp is not None
    return cp


def conflicts(daemon: Daemon) -> list[IntegrationConflicted]:
    return [e for e in daemon.store.read(PLAN_ID) if isinstance(e, IntegrationConflicted)]


async def conflicted(daemon: Daemon, *, gated: tuple[str, ...] = ()) -> Checkpoint:
    """a settles tracked.txt; b (sibling) edits it differently and cannot integrate."""
    _ = await create(daemon, planner=Graph({"a": [], "b": [], "c": ["b"]}, gated))
    _ = await run(daemon, "a", file="tracked.txt", text="a version\n")
    return await run(daemon, "b", file="tracked.txt", text="b version\n", settle=not gated)


def test_first_settle_creates_integration_ref_then_advances(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        _ = await create(daemon, planner=Graph({"a": [], "b": []}))
        before, index = checkout(root), (root / ".git/index").read_bytes()
        assert IREF not in refs(root)
        a = await run(daemon, "a")
        assert git(root, "rev-parse", IREF) == a.commit_sha
        assert daemon.plan(PLAN_ID).integration_sha == a.commit_sha
        b = await run(daemon, "b")  # disjoint roots: a real merge commit
        tip = git(root, "rev-parse", IREF)
        assert tip not in (a.commit_sha, b.commit_sha)
        assert git(root, "show", "-s", "--format=%P", tip).split() == [a.commit_sha, b.commit_sha]
        assert {"a.txt", "b.txt"} <= set(git(root, "ls-tree", "-r", "--name-only", tip).splitlines())
        assert daemon.plan(PLAN_ID).integration_sha == tip
        assert f"refs/heads/herdsman-land/{PLAN_ID}" not in refs(root)
        assert checkout(root) == before and (root / ".git/index").read_bytes() == index

    asyncio.run(scenario())


def test_chain_fast_forwards(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Graph({"a": [], "b": ["a"]}))
        _ = await run(daemon, "a")
        b = await run(daemon, "b")
        assert git(daemon.project_root, "rev-parse", IREF) == b.commit_sha

    asyncio.run(scenario())


def test_conflicting_siblings_block_settle_once(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        before = checkout(root)
        b = await conflicted(daemon)
        plan = daemon.plan(PLAN_ID)
        a_tip = plan.integration_sha
        assert plan.initiatives["b"].state == "running"  # policy returned quietly, no InitiativeFailed
        assert plan.initiatives["b"].integration_conflict is not None
        assert "c" not in plan.ready()
        for _ in range(2):
            with pytest.raises(IntegrationConflictError, match="integration conflict in tracked.txt") as exc:
                _ = daemon.settle_initiative(PLAN_ID, "b", b.id)
            assert exc.value.paths == ("tracked.txt",) and isinstance(exc.value, ValueError)
        event, = conflicts(daemon)
        assert event.integration_sha == a_tip
        assert git(root, "rev-parse", f"refs/herdsman/conflicts/{b.id}/{a_tip[:12] if a_tip else ''}") == event.conflict_sha
        assert daemon.plan(PLAN_ID).integration_sha == a_tip
        assert git(root, "rev-parse", IREF) == a_tip
        assert checkout(root) == before

    asyncio.run(scenario())


def test_conflict_maps_to_409(daemon: Daemon) -> None:
    from tests.test_kitchen_api import request
    from herdsman.daemon import create_app

    async def scenario() -> None:
        b = await conflicted(daemon)
        status, body = await request(create_app(daemon), "POST", f"/plans/{PLAN_ID}/initiatives/b/settle/{b.id}")
        assert status == 409 and "integration conflict in tracked.txt" in str(body)

    asyncio.run(scenario())


def test_ineligible_checkpoint_returns_its_error_and_records_no_conflict(daemon: Daemon) -> None:
    async def scenario() -> None:
        b = await conflicted(daemon, gated=("b",))
        with pytest.raises(ValueError, match="requires approval"):
            _ = daemon.settle_initiative(PLAN_ID, "b", b.id)
        _ = daemon.append(CheckpointRejected(plan_id=PLAN_ID, at=datetime.now(UTC), checkpoint_id=b.id))
        with pytest.raises(ValueError, match="was rejected"):
            _ = daemon.settle_initiative(PLAN_ID, "b", b.id)
        assert conflicts(daemon) == []

    asyncio.run(scenario())


def test_gated_approval_conflicts_but_preserves_approval_and_repeat_does_not_duplicate(
    daemon: Daemon,
) -> None:
    async def scenario() -> None:
        b = await conflicted(daemon, gated=("b",))
        for _ in range(2):
            with pytest.raises(IntegrationConflictError):
                _ = daemon.approve_checkpoint(PLAN_ID, b.id, action_id="act-1")
        assert len(conflicts(daemon)) == 1
        b_view = daemon.plan(PLAN_ID).initiatives["b"]
        assert b_view.checkpoint_decisions[b.id].state == "approved" and b_view.state == "running"
        assert sum(isinstance(e, CheckpointApproved) for e in daemon.store.read(PLAN_ID)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("gated", [(), ("b",)])
def test_recovery_reports_integration_conflict_and_isolates_items(
    daemon: Daemon, gated: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        b = await conflicted(daemon, gated=gated)
        if gated:
            with pytest.raises(IntegrationConflictError):
                _ = daemon.approve_checkpoint(PLAN_ID, b.id)
        report = await daemon.resume_plan(PLAN_ID, runtime=Runtime(daemon.project_root))
        assert report.outcomes == {"b": "integration-conflict"}
        plan = daemon.plan(PLAN_ID)
        assert plan.initiatives["b"].state == "running"
        if gated:
            assert plan.initiatives["b"].checkpoint_decisions[b.id].state == "approved"
        assert len(conflicts(daemon)) == 1

    asyncio.run(scenario())


def test_one_conflicted_stale_initiative_does_not_block_another(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Graph({"a": [], "b": [], "c": []}))
        _ = await run(daemon, "a", file="tracked.txt", text="a version\n")
        _ = await run(daemon, "b", file="tracked.txt", text="b version\n")  # conflicts quietly
        _ = await run(daemon, "c", settle=False)  # admitted: conflicted b is not a running writer
        report = await daemon.resume_plan(PLAN_ID, runtime=Runtime(daemon.project_root))
        assert report.outcomes == {"b": "integration-conflict", "c": "settled"}
        assert daemon.plan(PLAN_ID).initiatives["c"].state == "settled"

    asyncio.run(scenario())


def test_repeated_approve_action_after_crash_settles(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Graph({"a": []}, gated=("a",)))
        cp = await run(daemon, "a", settle=False)
        real = daemon._settle  # pyright: ignore[reportPrivateUsage]

        def crash(*_a: object) -> Plan:
            raise RuntimeError("daemon died")

        monkeypatch.setattr(daemon, "_settle", crash)
        with pytest.raises(RuntimeError):
            _ = daemon.approve_checkpoint(PLAN_ID, cp.id, action_id="act-1")
        assert daemon.plan(PLAN_ID).initiatives["a"].state == "running"
        monkeypatch.setattr(daemon, "_settle", real)
        plan = daemon.approve_checkpoint(PLAN_ID, cp.id, action_id="act-1")
        assert plan.initiatives["a"].state == "settled"
        assert plan.integration_sha == cp.commit_sha
        assert sum(isinstance(e, CheckpointApproved) for e in daemon.store.read(PLAN_ID)) == 1

    asyncio.run(scenario())


def test_conflicted_initiative_is_not_a_running_writer(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await conflicted(daemon)
        # c depends on b, so use a fresh sibling-free check through the admission rule.
        from herdsman.daemon import _contending_writers  # pyright: ignore[reportPrivateUsage]
        plan = daemon.plan(PLAN_ID)
        assert plan.initiatives["b"].state == "running"
        assert _contending_writers(plan, "c") == set()

    asyncio.run(scenario())


def test_unattended_retry_never_selects_a_conflicted_initiative(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await conflicted(daemon)
        plan = daemon.plan(PLAN_ID)
        assert not daemon._unattended_retryable(plan, plan.initiatives["b"])  # pyright: ignore[reportPrivateUsage]

    asyncio.run(scenario())


def test_orphaned_integration_ref_is_overwritten_and_tips_survive_gc(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        _ = await create(daemon, planner=Graph({"a": [], "b": [], "c": []}))
        a = await run(daemon, "a", file="tracked.txt", text="a version\n")
        # An advance whose append never happened: unrelated commit on the ref.
        orphan = git(root, "commit-tree", "-m", "orphan", "HEAD^{tree}")
        _ = git(root, "update-ref", IREF, orphan)
        b = await run(daemon, "b")
        tip = git(root, "rev-parse", IREF)
        assert tip != orphan
        assert tip == daemon.plan(PLAN_ID).integration_sha
        assert git(root, "merge-base", "--is-ancestor", a.commit_sha or "", tip) == ""
        # Now a conflict, then gc: every event-named object must survive.
        _ = await run(daemon, "c", file="tracked.txt", text="c version\n")
        _ = git(root, "gc", "-q", "--prune=now")
        for event in daemon.store.read(PLAN_ID):
            names: list[str | None] = []
            if isinstance(event, InitiativeSettled):
                names.append(event.integration_sha)
            if isinstance(event, IntegrationConflicted):
                names += [event.conflict_sha, event.integration_sha]
            for sha in names:
                assert sha is not None
                _ = git(root, "cat-file", "-e", f"{sha}^{{commit}}")
        for cp in (a, b):
            _ = git(root, "cat-file", "-e", f"{cp.commit_sha}^{{commit}}")
        assert len(conflicts(daemon)) == 1

    asyncio.run(scenario())


def test_legacy_plan_settles_without_git_integration(tmp_path: Path) -> None:
    from tests.test_daemon import StubCollector, StubRuntime, local_daemon
    from tests.test_dag_run import seed

    async def scenario() -> None:
        store, d = local_daemon(tmp_path)
        try:
            _ = seed(d, spec("a", writes=["a/"]))
            cp = await d.run_and_settle("p", "a", runtime=StubRuntime(), collector=StubCollector())
            assert cp is not None
            plan = d.plan("p")
            assert plan.initiatives["a"].state == "settled" and plan.integration_sha is None
            settled = [e for e in store.read("p") if isinstance(e, InitiativeSettled)]
            assert [e.integration_sha for e in settled] == [None]
        finally:
            store.close()

    asyncio.run(scenario())


def test_settled_node_cannot_be_retired_by_recalibration(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Graph({"a": []}))
        _ = await run(daemon, "a")
        events = daemon.store.read(PLAN_ID)
        other = spec("z", writes=["z.txt"])
        proposal = PlanProposed(plan_id=PLAN_ID, at=datetime.now(UTC), version=2, initiatives=[other])
        with pytest.raises(ValueError):
            _ = Plan.fold([*events, proposal])

    asyncio.run(scenario())
