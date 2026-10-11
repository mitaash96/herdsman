"""T5: settle merges into the plan's integration ref; conflicts block every settle path.

Real EventStore, real repo, real linked worktrees; only the agent runtime is faked.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from herdsman.checkpoint import GitCheckpointCollector
from herdsman.classes import (
    AttemptStarted,
    Checkpoint,
    CheckpointApproved,
    CheckpointRejected,
    InitiativeSettled,
    IntegrationConflicted,
    Plan,
    PlanProposed,
)
from herdsman.daemon import Daemon, IntegrationConflictError, create_app
from herdsman.herdr import HerdrAdapter
from herdsman.integration import conflict_pin
from tests.test_dag_run import spec
from tests.test_landing import (  # noqa: F401  (daemon is a fixture)
    PLAN_ID,
    Planner,
    Runtime,
    checkout,
    create,
    daemon,  # pyright: ignore[reportUnusedImport]
    git,
    refs,
)
from tests.test_kitchen_api import request

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


# --- T7: conflict resolution attempts ----------------------------------------


class Files(Runtime):
    """Writes several files, so a checkpoint can carry more than the conflict."""

    def __init__(self, root: Path, files: dict[str, str]) -> None:
        super().__init__(root)
        self.files: dict[str, str] = files
        self.inputs_seen: dict[str, str] = {}

    @override
    async def run(self, worktree_ref: str, launch: object) -> str:
        path = await self.worktree_path(worktree_ref)
        self.inputs_seen = {file.name: file.read_text() for file in path.glob("*.txt")}
        for name, text in self.files.items():
            _ = (path / name).write_text(text)
        return worktree_ref


MARKED = "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n"


async def resolve(daemon: Daemon, rt: Runtime, action_id: str | None = None) -> Checkpoint | None:
    return await daemon.resolve_conflict(
        PLAN_ID, "b", runtime=rt, collector=collector(daemon), action_id=action_id
    )


async def sibling_conflict(daemon: Daemon, *, gated: tuple[str, ...] = ()) -> tuple[Checkpoint, Checkpoint]:
    """a (tracked.txt + a.txt) settles; b (tracked.txt + b.txt) conflicts; c depends on b."""
    _ = await create(daemon, planner=Graph({"a": [], "b": [], "c": ["b"], "e": ["a"]}, gated))
    root = daemon.project_root
    a = await daemon.run_and_settle(
        PLAN_ID, "a", runtime=Files(root, {"tracked.txt": "a version\n", "a.txt": "a\n"}),
        collector=collector(daemon),
    )
    b = await daemon.run_and_settle(
        PLAN_ID, "b", runtime=Files(root, {"tracked.txt": "b version\n", "b.txt": "b\n"}),
        collector=collector(daemon),
    ) if not gated else await daemon.run_initiative(
        PLAN_ID, "b", runtime=Files(root, {"tracked.txt": "b version\n", "b.txt": "b\n"}),
        collector=collector(daemon),
    )
    assert a is not None and b is not None
    if gated:
        with pytest.raises(IntegrationConflictError):
            _ = daemon.approve_checkpoint(PLAN_ID, b.id)
    return a, b


def test_resolution_settles_as_original_checkpoint_isolated_from_siblings(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        a, b = await sibling_conflict(daemon)
        t = daemon.plan(PLAN_ID).integration_sha
        assert t is not None
        x = conflicts(daemon)[0].conflict_sha
        rt = Runtime(root, file="tracked.txt", text="merged\n")
        rr = await resolve(daemon, rt, "act-r")
        assert rr is not None
        # Created on the conflict commit itself, which holds the markers and b.txt.
        assert rt.bases == [conflict_pin(b.id, t)] and rt.heads == [x]
        assert "<<<<<<<" in rt.inputs_seen["tracked.txt"] and rt.inputs_seen["b.txt"] == "b\n"
        assert rr.base_sha == x and rr.resolves == b.id
        assert git(root, "show", "-s", "--format=%P", rr.commit_sha or "").split() == [x]
        plan = daemon.plan(PLAN_ID)
        view = plan.initiatives["b"]
        assert view.state == "settled" and view.settled_checkpoint_id == b.id
        assert view.integration_conflict is None
        settled = [e for e in daemon.store.read(PLAN_ID) if isinstance(e, InitiativeSettled) and e.initiative_id == "b"]
        assert [(e.checkpoint_id, e.resolution_checkpoint_id) for e in settled] == [(b.id, rr.id)]
        tip = git(root, "rev-parse", IREF)
        assert tip == plan.integration_sha == rr.commit_sha  # tip was still T: fast-forward
        for ancestor in (b.commit_sha, t, a.commit_sha):
            assert git(root, "merge-base", "--is-ancestor", ancestor or "", tip) == ""
        assert git(root, "show", f"{tip}:tracked.txt") == "merged"
        # Same action id again: the original result, no second attempt.
        assert (await resolve(daemon, rt, "act-r")) == view.latest_checkpoint
        assert sum(isinstance(e, AttemptStarted) and e.origin == "resolution" for e in daemon.store.read(PLAN_ID)) == 1
        # D gets b's own checkpoint (not the tip): no a.txt, and tracked.txt is b's.
        drt = Runtime(root)
        d = await daemon.run_and_settle(PLAN_ID, "c", runtime=drt, collector=collector(daemon))
        assert d is not None
        assert "a.txt" not in drt.inputs_seen and drt.inputs_seen["b.txt"] == "b\n"
        assert drt.inputs_seen["tracked.txt"] == "b version\n"
        assert daemon.plan(PLAN_ID).initiatives["c"].state == "settled"

    asyncio.run(scenario())


def test_leftover_markers_fail_the_check_and_the_ceiling_bounds_the_loop(daemon: Daemon) -> None:
    async def scenario() -> None:
        _, b = await sibling_conflict(daemon)
        tip = daemon.plan(PLAN_ID).integration_sha
        for _ in range(2):
            rr = await resolve(daemon, Runtime(daemon.project_root, file="tracked.txt", text=MARKED))
            assert rr is not None
            check, = [c for c in rr.checks if c.name == "resolution-markers"]
            assert not check.passed and "tracked.txt" in check.summary
            view = daemon.plan(PLAN_ID).initiatives["b"]
            assert view.state == "failed" and view.integration_conflict is not None
            with pytest.raises(ValueError):
                _ = daemon.settle_initiative(PLAN_ID, "b", rr.id)
        assert daemon.plan(PLAN_ID).integration_sha == tip
        with pytest.raises(ValueError, match="attempt ceiling"):
            _ = await resolve(daemon, Runtime(daemon.project_root, file="tracked.txt", text="ok\n"))
        assert len(conflicts(daemon)) == 1 and b.id

    asyncio.run(scenario())


def test_duplicate_concurrent_resolve_starts_one_attempt(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await sibling_conflict(daemon)
        results = await asyncio.gather(
            *(resolve(daemon, Runtime(daemon.project_root, file="tracked.txt", text="m\n")) for _ in range(2)),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ValueError) for r in results) == 1
        assert sum(isinstance(e, AttemptStarted) and e.origin == "resolution" for e in daemon.store.read(PLAN_ID)) == 1
        assert daemon.plan(PLAN_ID).initiatives["b"].state == "settled"

    asyncio.run(scenario())


def test_resolution_requires_a_conflict(daemon: Daemon) -> None:
    async def scenario() -> None:
        from herdsman.classes import resolution_admissible

        _ = await create(daemon, planner=Graph({"a": [], "b": []}))
        assert "no open integration conflict" in (resolution_admissible(daemon.plan(PLAN_ID), "b") or "")
        with pytest.raises(ValueError, match="no open integration conflict"):
            _ = await resolve(daemon, Runtime(daemon.project_root))

    asyncio.run(scenario())


def test_wrong_base_fails_the_attempt(daemon: Daemon) -> None:
    class Ignoring(Runtime):
        @override
        async def create_worktree(self, branch: str, *, base: str | None = None) -> str:
            return await super().create_worktree(branch, base=None)

    async def scenario() -> None:
        _ = await sibling_conflict(daemon)
        with pytest.raises(RuntimeError, match="wrong base"):
            _ = await resolve(daemon, Ignoring(daemon.project_root))
        assert daemon.plan(PLAN_ID).initiatives["b"].state == "failed"

    asyncio.run(scenario())


def test_gated_resolution_needs_its_own_approval(daemon: Daemon) -> None:
    async def scenario() -> None:
        _, b = await sibling_conflict(daemon, gated=("b",))
        rr = await resolve(daemon, Runtime(daemon.project_root, file="tracked.txt", text="m\n"))
        assert rr is not None
        assert daemon.plan(PLAN_ID).initiatives["b"].state == "running"
        with pytest.raises(ValueError, match="requires approval"):
            _ = daemon.settle_initiative(PLAN_ID, "b", rr.id)
        plan = daemon.approve_checkpoint(PLAN_ID, rr.id)
        view = plan.initiatives["b"]
        assert view.state == "settled" and view.settled_checkpoint_id == b.id
        assert plan.integration_sha == rr.commit_sha

    asyncio.run(scenario())


def test_tip_moved_during_resolution_merges_or_conflicts_afresh(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        _, b = await sibling_conflict(daemon)
        # Record the resolution without settling it, then move the tip with e.
        rr = await daemon.run_initiative(
            PLAN_ID, "b", runtime=Runtime(root, file="tracked.txt", text="merged\n"),
            collector=collector(daemon), origin="resolution",
        )
        assert rr is not None
        _ = await run(daemon, "e", file="tracked.txt", text="a version\ne line\n")
        moved = daemon.plan(PLAN_ID).integration_sha
        with pytest.raises(IntegrationConflictError):
            _ = daemon.settle_initiative(PLAN_ID, "b", rr.id)
        fresh = conflicts(daemon)[-1]
        assert len(conflicts(daemon)) == 2 and fresh.checkpoint_id == b.id and fresh.integration_sha == moved
        # The fresh conflict is resolved again and still settles as b's original.
        rr2 = await resolve(daemon, Runtime(root, file="tracked.txt", text="merged twice\n"))
        assert rr2 is not None
        plan = daemon.plan(PLAN_ID)
        assert plan.initiatives["b"].state == "settled" and plan.initiatives["b"].settled_checkpoint_id == b.id
        tip = plan.integration_sha or ""
        assert git(root, "show", f"{tip}:tracked.txt") == "merged twice"

    asyncio.run(scenario())


def test_tip_moved_with_disjoint_changes_is_a_normal_merge(daemon: Daemon) -> None:
    async def scenario() -> None:
        root = daemon.project_root
        _ = await sibling_conflict(daemon)
        rr = await daemon.run_initiative(
            PLAN_ID, "b", runtime=Runtime(root, file="tracked.txt", text="merged\n"),
            collector=collector(daemon), origin="resolution",
        )
        assert rr is not None
        _ = await run(daemon, "e")  # e.txt only
        moved = daemon.plan(PLAN_ID).integration_sha
        _ = daemon.settle_initiative(PLAN_ID, "b", rr.id)
        tip = daemon.plan(PLAN_ID).integration_sha or ""
        assert git(root, "show", "-s", "--format=%P", tip).split() == [moved, rr.commit_sha]
        assert {"e.txt", "b.txt"} <= set(git(root, "ls-tree", "-r", "--name-only", tip).splitlines())
        assert git(root, "show", f"{tip}:tracked.txt") == "merged"

    asyncio.run(scenario())


def test_cancel_and_discard_on_a_conflicted_initiative(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def interrupt_pane(_self: HerdrAdapter, pane_ref: str) -> None:
        del pane_ref  # cancel must not need the herdr binary

    monkeypatch.setattr(HerdrAdapter, "interrupt_pane", interrupt_pane)

    async def scenario() -> None:
        _, b = await sibling_conflict(daemon)
        plan = await daemon.discard_initiative(PLAN_ID, "b", b.attempt_id, runtime=Runtime(daemon.project_root))
        assert plan.initiatives["b"].integration_conflict is not None
        plan = await daemon.cancel_initiative(PLAN_ID, "b")
        assert plan.initiatives["b"].state == "cancelled" and plan.initiatives["b"].integration_conflict is None
        with pytest.raises(ValueError):
            _ = await resolve(daemon, Runtime(daemon.project_root))

    asyncio.run(scenario())


def test_salvage_input_lists_conflict_evidence(daemon: Daemon) -> None:
    async def scenario() -> None:
        _, b = await sibling_conflict(daemon)
        text = daemon._salvage_input(daemon.plan(PLAN_ID))  # pyright: ignore[reportPrivateUsage]
        assert f"integration-conflict b: tracked.txt checkpoint={b.id}" in text
        assert conflicts(daemon)[0].conflict_sha in text

    asyncio.run(scenario())


def test_resolve_route(daemon: Daemon, monkeypatch: pytest.MonkeyPatch) -> None:
    from herdsman.daemon import create_app
    from tests.test_kitchen_api import request

    async def scenario() -> None:
        app = create_app(daemon)
        _, b = await sibling_conflict(daemon)
        real = daemon.resolve_conflict

        async def wired(
            plan_id: str, initiative_id: str, *, timeout: float | None, by: str,
            action_id: str | None, unattended: bool,
        ) -> Checkpoint | None:
            return await real(
                plan_id, initiative_id,
                runtime=Runtime(daemon.project_root, file="tracked.txt", text="m\n"),
                collector=collector(daemon), timeout=timeout, by=by,
                action_id=action_id, unattended=unattended,
            )

        monkeypatch.setattr(daemon, "resolve_conflict", wired)
        for _ in range(2):  # the repeat is the original result
            status, body = await request(app, "POST", f"/plans/{PLAN_ID}/initiatives/b/resolve", {"action_id": "ui-1"})
            assert status == 200 and body["checkpoint"]["resolves"] == b.id  # pyright: ignore[reportIndexIssue]
        status, body = await request(app, "POST", f"/plans/{PLAN_ID}/initiatives/b/resolve", {"action_id": "ui-2"})
        assert status == 409 and "no open integration conflict" in str(body)

    asyncio.run(scenario())


# --- T9: live tier ------------------------------------------------------------
# Real herdr worktrees (forked from the pinned refs) and real git integration; the
# agent itself is a stub that writes files (no LLM), since the live harness has no
# daemon-level stub-agent launcher.


class LiveRuntime(Runtime):
    def __init__(self, root: Path, adapter: HerdrAdapter, created: list[str], **kw: str) -> None:
        super().__init__(root, **kw)
        self.adapter: HerdrAdapter = adapter
        self.created: list[str] = created
        self.node: str = ""
        self.path: Path | None = None

    @override
    async def create_worktree(self, branch: str, *, base: str | None = None) -> str:
        ref = await self.adapter.create_worktree(branch, base=base)
        self.created.append(ref)
        self.bases.append(base)
        self.node = branch.split("/")[2]
        self.path = await self.adapter.worktree_path(ref)
        self.heads.append(git(self.path, "rev-parse", "HEAD"))
        return ref

    @override
    async def worktree_path(self, worktree_ref: str) -> Path:
        assert self.path is not None
        return self.path

    @override
    async def run(self, worktree_ref: str, launch: object) -> str:
        _ = await super().run(worktree_ref, launch)  # pyright: ignore[reportArgumentType]
        return str(self.path)

    @override
    async def remove_worktree(self, worktree_ref: str) -> None:
        await self.adapter.remove_worktree(worktree_ref)


async def live(daemon: Daemon, scenario: Callable[[HerdrAdapter, list[str]], Awaitable[None]]) -> None:
    adapter = HerdrAdapter(project_root=daemon.project_root)
    created: list[str] = []
    try:
        await scenario(adapter, created)
    finally:
        for ref in created:
            try:
                await adapter.remove_worktree(ref)
            except Exception:  # already removed by the daemon
                pass
        await adapter.aclose()


@pytest.mark.skipif(os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1", reason="needs installed herdr")
def test_real_herdr_diamond_integrates_all_branches_and_lands(daemon: Daemon, herdr_workspaces: None) -> None:
    del herdr_workspaces
    root = daemon.project_root

    async def scenario(adapter: HerdrAdapter, created: list[str]) -> None:
        _ = await create(daemon, planner=Graph({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}))
        before = checkout(root)
        seen: dict[str, dict[str, str]] = {}
        for node in "abcd":
            rt = LiveRuntime(root, adapter, created)
            cp = await daemon.run_and_settle(PLAN_ID, node, runtime=rt, collector=collector(daemon))
            assert cp is not None
            seen[node] = rt.inputs_seen
        assert set(seen["d"]) == {"a.txt", "b.txt", "c.txt", "tracked.txt"}
        tip = git(root, "rev-parse", IREF)
        for node in "abcd":
            assert git(root, "show", f"{tip}:{node}.txt") == node
        status, _ = await request(create_app(daemon), "POST", f"/plans/{PLAN_ID}/land")
        assert status == 200
        for node in "abcd":
            assert git(root, "show", f"new-branch-a:{node}.txt") == node
        assert checkout(root) == before

    asyncio.run(asyncio.wait_for(live(daemon, scenario), 180))


@pytest.mark.skipif(os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1", reason="needs installed herdr")
def test_real_herdr_conflict_is_resolved_and_lands(daemon: Daemon, herdr_workspaces: None) -> None:
    del herdr_workspaces
    root = daemon.project_root

    async def scenario(adapter: HerdrAdapter, created: list[str]) -> None:
        _ = await create(daemon, planner=Graph({"a": [], "b": [], "c": ["b"]}))
        before = checkout(root)
        a = await daemon.run_and_settle(
            PLAN_ID, "a", runtime=LiveRuntime(root, adapter, created, file="tracked.txt", text="a version\n"),
            collector=collector(daemon))
        b = await daemon.run_and_settle(
            PLAN_ID, "b", runtime=LiveRuntime(root, adapter, created, file="tracked.txt", text="b version\n"),
            collector=collector(daemon))
        assert a is not None and b is not None
        assert len(conflicts(daemon)) == 1
        assert daemon.plan(PLAN_ID).initiatives["b"].integration_conflict is not None
        rr = await daemon.resolve_conflict(
            PLAN_ID, "b", runtime=LiveRuntime(root, adapter, created, file="tracked.txt", text="merged\n"),
            collector=collector(daemon))
        assert rr is not None and daemon.plan(PLAN_ID).initiatives["b"].state == "settled"
        c = await daemon.run_and_settle(PLAN_ID, "c", runtime=LiveRuntime(root, adapter, created),
                                        collector=collector(daemon))
        assert c is not None
        status, _ = await request(create_app(daemon), "POST", f"/plans/{PLAN_ID}/land")
        assert status == 200
        assert git(root, "show", "new-branch-a:tracked.txt") == "merged"
        assert checkout(root) == before

    asyncio.run(asyncio.wait_for(live(daemon, scenario), 180))


async def diamond(daemon: Daemon, *, with_u: bool) -> None:
    """s (and optionally u) settle; r conflicts on tracked.txt and is resolved; d needs s and r."""
    deps = {"u": [], "s": [], "r": [], "d": ["s", "r"]} if with_u else {"s": [], "r": [], "d": ["s", "r"]}
    _ = await create(daemon, planner=Graph(deps))
    root = daemon.project_root
    if with_u:
        _ = await daemon.run_and_settle(
            PLAN_ID, "u", runtime=Files(root, {"u.txt": "u\n"}), collector=collector(daemon)
        )
    _ = await daemon.run_and_settle(
        PLAN_ID, "s", runtime=Files(root, {"tracked.txt": "s version\n", "s.txt": "s\n"}),
        collector=collector(daemon),
    )
    _ = await daemon.run_and_settle(
        PLAN_ID, "r", runtime=Files(root, {"tracked.txt": "r version\n", "r.txt": "r\n"}),
        collector=collector(daemon),
    )
    rr = await daemon.resolve_conflict(
        PLAN_ID, "r", runtime=Runtime(root, file="tracked.txt", text="merged\n"),
        collector=collector(daemon),
    )
    assert rr is not None
    assert daemon.plan(PLAN_ID).initiatives["r"].settled_resolution_id == rr.id


def test_dependent_of_two_conflicted_siblings_reuses_the_resolution(daemon: Daemon) -> None:
    async def scenario() -> None:
        await diamond(daemon, with_u=False)
        rr = daemon.plan(PLAN_ID).initiatives["r"].latest_checkpoint
        assert rr is not None and rr.commit_sha is not None
        drt = Runtime(daemon.project_root)
        d = await daemon.run_and_settle(PLAN_ID, "d", runtime=drt, collector=collector(daemon))
        assert d is not None
        assert drt.inputs_seen["tracked.txt"] == "merged\n"
        assert drt.inputs_seen["s.txt"] == "s\n" and drt.inputs_seen["r.txt"] == "r\n"
        assert git(daemon.project_root, "merge-base", "--is-ancestor", rr.commit_sha, drt.heads[0]) == ""

    asyncio.run(scenario())


def test_resolution_carrying_an_outsider_is_not_reused(daemon: Daemon) -> None:
    async def scenario() -> None:
        await diamond(daemon, with_u=True)
        drt = Runtime(daemon.project_root)
        with pytest.raises(Exception) as err:
            _ = await daemon.run_and_settle(PLAN_ID, "d", runtime=drt, collector=collector(daemon))
        assert "tracked.txt" in str(err.value)
        assert daemon.plan(PLAN_ID).initiatives["d"].state != "settled"

    asyncio.run(scenario())
