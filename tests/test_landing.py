"""Branch-landing acceptance (§6 of branch-landing-decision.md).

Real Git objects and checkpoint patches; only the agent/runtime is faked.
"""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from herdsman import landing
from herdsman.checkpoint import GitCheckpointCollector
from herdsman.classes import (
    Checkpoint,
    Event,
    LandingBlocked,
    Plan,
    PlanCreated,
    PlanLanded,
    PlanProposed,
    RuntimeObserved,
)
from herdsman.daemon import Daemon, create_app
from herdsman.herdr import AgentLaunch, HerdrAdapter, HerdrOperationError, RuntimeInventory
from herdsman.store import EventStore
from tests.test_dag_run import spec
from tests.test_kitchen_api import request

AT = datetime(2026, 10, 2, tzinfo=UTC)
PLAN_ID = "plan_12345678abcdefabcdefabcdefabcdef"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def commit(root: Path, path: str, text: str) -> str:
    _ = (root / path).write_text(text)
    _ = git(root, "add", path)
    _ = git(root, "commit", "-qm", path)
    return git(root, "rev-parse", "HEAD")


def init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _ = git(root, "init", "-q", "-b", "uat")
    _ = git(root, "config", "user.email", "test@example.invalid")
    _ = git(root, "config", "user.name", "Test")
    _ = (root / ".gitignore").write_text(".herdsman/\n")
    _ = git(root, "add", ".gitignore")
    return commit(root, "tracked.txt", "base\n")


def configure_runtime(root: Path) -> None:
    directory = root / ".herdsman"
    directory.mkdir(exist_ok=True)
    _ = (directory / "luna.json").write_text(json.dumps({"binary": "luna-test"}))


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    root = tmp_path / "project"
    _ = init_repo(root)
    configure_runtime(root)
    store = EventStore(root / ".herdsman/events.db")
    try:
        yield Daemon(store, project_root=root)
    finally:
        store.close()


class Planner:
    def __init__(self, *nodes: str, title: str = "Build Branch Landing!", dag: bool = False):
        self.title: str = title
        self.nodes: tuple[str, ...] = nodes or ("a",)
        self.dag: bool = dag
        self.calls: list[str] = []

    def factory(self, *_args: object, **_kwargs: object) -> "Planner":
        return self

    def propose(self, brief: str) -> object:
        self.calls.append(brief)
        return {"title": self.title, "initiatives": [
            spec(node, writes=[f"{node}.txt", "tracked.txt"],
                 depends_on=["a"] if self.dag and node != "a" else []).model_dump(mode="json")
            for node in self.nodes
        ]}


class Runtime:
    """Fork real worktrees and record HEAD before the collector applies inputs."""

    def __init__(self, root: Path, *, file: str | None = None, text: str | None = None):
        self.root: Path = root
        self.file: str | None = file
        self.text: str | None = text
        self.bases: list[str | None] = []
        self.heads: list[str] = []
        self.path: Path | None = None
        self.node: str = ""
        self.inputs_seen: dict[str, str] = {}

    async def create_worktree(self, branch: str, *, base: str | None = None) -> str:
        self.bases.append(base)
        self.node = branch.split("/")[2]
        self.path = self.root.parent / branch.split("/")[-1]
        _ = git(self.root, "worktree", "add", "-q", "-b", branch, str(self.path), base or "HEAD")
        self.heads.append(git(self.path, "rev-parse", "HEAD"))
        return str(self.path)

    async def worktree_path(self, worktree_ref: str) -> Path:
        assert self.path is not None and str(self.path) == worktree_ref
        return self.path

    async def run(self, worktree_ref: str, launch: AgentLaunch) -> str:
        assert launch.prompt
        path = await self.worktree_path(worktree_ref)
        self.inputs_seen = {file.name: file.read_text() for file in path.glob("*.txt")}
        _ = (path / (self.file or f"{self.node}.txt")).write_text(self.text or f"{self.node}\n")
        return worktree_ref

    async def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str, *, rearm: bool = False,
    ) -> AsyncIterator[RuntimeObserved]:
        assert self.path is not None and str(self.path) == pane_ref and not rearm
        yield RuntimeObserved(
            plan_id=plan_id, at=AT, attempt_id=attempt_id, kind="agent_settled",
            detail={"agent_status": "idle"},
        )

    async def remove_worktree(self, worktree_ref: str) -> None:
        _ = git(self.root, "worktree", "remove", "--force", worktree_ref)

    async def inventory(self) -> RuntimeInventory:
        return RuntimeInventory((), ())

    async def aclose(self) -> None:
        pass


async def create(
    daemon: Daemon, *, target: str | None = "new-branch-a", planner: Planner | None = None,
    plan_id: str = PLAN_ID,
) -> Plan:
    plan = await daemon.create_plan(
        "Implement landing", planner=planner or Planner(), plan_id=plan_id,
        start_branch="uat", target_branch=target,
    )
    _ = daemon.approve_plan(plan.id)
    return daemon.plan(plan.id)


async def settle(
    daemon: Daemon, node: str = "a", *, runtime: Runtime | None = None,
    plan_id: str = PLAN_ID,
) -> Checkpoint:
    checkpoint = await daemon.run_and_settle(
        plan_id, node, runtime=runtime or Runtime(daemon.project_root),
        collector=GitCheckpointCollector(checks=("true",), project_root=daemon.project_root),
    )
    assert checkpoint is not None
    assert daemon.plan(plan_id).initiatives[node].state == "settled"
    return checkpoint


def landed(daemon: Daemon, plan_id: str = PLAN_ID) -> list[PlanLanded]:
    return [event for event in daemon.store.read(plan_id) if isinstance(event, PlanLanded)]


def blocked(daemon: Daemon, reason: str) -> LandingBlocked:
    events = [event for event in daemon.store.read(PLAN_ID) if isinstance(event, LandingBlocked)]
    assert len(events) == 1
    assert events[0].reason == reason
    block = daemon.plan(PLAN_ID).landing_blocked
    assert block is not None and block.reason == reason
    assert not landed(daemon)
    return events[0]


def refs(root: Path) -> str:
    return git(root, "for-each-ref", "--sort=refname", "--format=%(refname) %(objectname)")


def checkout(root: Path) -> tuple[str, str, str, dict[str, bytes]]:
    """Logical index and tracked bytes, including staged/unstaged user edits."""
    return (
        git(root, "symbolic-ref", "HEAD"), git(root, "rev-parse", "HEAD"),
        git(root, "ls-files", "--stage"),
        {name: (root / name).read_bytes() for name in git(root, "ls-files").splitlines()},
    )


def assert_files(root: Path, sha: str, *nodes: str) -> None:
    for node in nodes:
        assert git(root, "show", f"{sha}:{node}.txt") == node


def test_a_pin_precedes_created_event_and_multi_sink_landing_preserves_uat(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = daemon.project_root
    u0 = git(root, "rev-parse", "HEAD")
    original_append = daemon.append

    def append(event: Event) -> Event:
        if isinstance(event, PlanCreated):
            assert event.start_sha == u0
            assert git(root, "rev-parse", f"refs/herdsman/plans/{event.plan_id}/base") == u0
        return original_append(event)

    monkeypatch.setattr(daemon, "append", append)

    class MovingPlanner(Planner):
        @override
        def propose(self, brief: str) -> object:
            # The start must be frozen even while the planner is running.
            assert git(root, "rev-parse", f"refs/herdsman/plans/{PLAN_ID}/base") == u0
            _ = commit(root, "owner.txt", "owner\n")
            return super().propose(brief)

    async def scenario() -> None:
        planner = MovingPlanner("a", "b", "c", dag=True)
        monkeypatch.setattr(daemon, "_frontier_planner", planner.factory)
        status, body = await request(create_app(daemon), "POST", "/plans", {
            "brief": "Implement landing", "plan_id": PLAN_ID,
            "start_branch": "uat", "target_branch": "new-branch-a",
        })
        assert status == 200 and len(planner.calls) == 1
        plan = Plan.model_validate(body)
        _ = daemon.approve_plan(plan.id)
        assert plan.base_sha == u0 and plan.start_branch == "uat" and plan.lands
        assert plan.target_branch == "new-branch-a" and not plan.target_derived
        created = next(e for e in daemon.store.read(PLAN_ID) if isinstance(e, PlanCreated))
        assert created.start_sha == u0
        u1 = git(root, "rev-parse", "HEAD")
        assert u1 != u0
        before = checkout(root)
        index = (root / ".git/index").read_bytes()
        checkpoints: list[str] = []
        for node in ("a", "b", "c"):
            runtime = Runtime(root)
            checkpoints.append((await settle(daemon, node, runtime=runtime)).id)
            assert runtime.bases == [f"refs/herdsman/plans/{PLAN_ID}/base"]
            assert runtime.heads == [u0]
            if node != "a":
                assert runtime.inputs_seen["a.txt"] == "a\n"
            if node != "c":
                assert not landed(daemon)
                assert "refs/heads/new-branch-a" not in refs(root)
        event, = landed(daemon)
        assert (event.mode, event.previous_sha, event.checkpoint_ids) == ("created", None, checkpoints)
        assert event.commit_sha is not None
        assert git(root, "rev-parse", "new-branch-a") == event.commit_sha
        assert git(root, "show", "-s", "--format=%P", event.commit_sha) == u0
        assert_files(root, event.commit_sha, "a", "b", "c")
        assert checkout(root) == before
        assert (root / ".git/index").read_bytes() == index
        assert git(root, "rev-parse", "uat") == u1
        assert git(root, "rev-parse", f"refs/herdsman/plans/{PLAN_ID}/base") == u0

    asyncio.run(scenario())


def test_b1_clean_checked_out_target_fast_forwards(daemon: Daemon) -> None:
    async def scenario() -> None:
        plan = await create(daemon, target="uat")
        checkpoint = await settle(daemon)
        event, = landed(daemon)
        assert (event.mode, event.previous_sha, event.checkpoint_ids) == ("fast_forward", plan.base_sha, [checkpoint.id])
        assert event.commit_sha is not None
        root = daemon.project_root
        assert git(root, "rev-parse", "uat") == git(root, "rev-parse", "HEAD") == event.commit_sha
        assert git(root, "show", "-s", "--format=%P", event.commit_sha) == plan.base_sha
        assert (root / "a.txt").read_text() == "a\n"
        assert git(root, "status", "--porcelain") == ""

    asyncio.run(scenario())


def test_b2_disjoint_owner_commit_merges_then_fast_forwards_root(daemon: Daemon) -> None:
    async def scenario() -> None:
        plan = await create(daemon, target="uat")
        root = daemon.project_root
        u1 = commit(root, "owner.txt", "owner\n")
        _ = await settle(daemon)
        event, = landed(daemon)
        assert event.mode == "merge" and event.previous_sha == u1
        assert event.commit_sha is not None
        parents = git(root, "show", "-s", "--format=%P", event.commit_sha).split()
        assert len(parents) == 2 and parents[0] == u1
        assert git(root, "show", "-s", "--format=%P", parents[1]) == plan.base_sha
        assert_files(root, parents[1], "a")
        _ = git(root, "merge-base", "--is-ancestor", u1, event.commit_sha)
        assert git(root, "rev-parse", "HEAD") == event.commit_sha
        assert (root / "owner.txt").read_text() == "owner\n"
        assert (root / "a.txt").read_text() == "a\n"
        assert git(root, "status", "--porcelain") == ""

    asyncio.run(scenario())


@pytest.mark.parametrize("staged", [False, True])
def test_b3_dirty_target_is_untouched_and_http_retry_clears_attention(
    daemon: Daemon, staged: bool,
) -> None:
    async def scenario() -> None:
        _ = await create(daemon, target="uat")
        root = daemon.project_root
        _ = (root / "tracked.txt").write_text("owner edit\n")
        if staged:
            _ = git(root, "add", "tracked.txt")
        before = checkout(root)
        _ = await settle(daemon)
        event = blocked(daemon, "dirty_checkout")
        assert f"refs/herdsman/plans/{PLAN_ID}/landed" not in refs(root)
        assert event.detail == "commit or stash your changes on uat, then retry landing"
        assert checkout(root) == before
        assert not (root / "a.txt").exists()
        assert any(item.kind == "landing_blocked" and item.plan_id == PLAN_ID for item in daemon.fleet().attention)
        app = create_app(daemon)
        status, _ = await request(app, "POST", f"/plans/{PLAN_ID}/land")
        assert status == 409
        _ = blocked(daemon, "dirty_checkout")  # Identical block is deduplicated.
        assert checkout(root) == before
        u1 = commit(root, "tracked.txt", "owner edit\n")
        status, body = await request(app, "POST", f"/plans/{PLAN_ID}/land")
        assert status == 200 and body["landing_blocked"] is None
        event_landed, = landed(daemon)
        assert event_landed.mode == "merge" and event_landed.previous_sha == u1
        assert event_landed.commit_sha == git(root, "rev-parse", "HEAD")
        assert (root / "tracked.txt").read_text() == "owner edit\n"
        assert (root / "a.txt").read_text() == "a\n"
        assert not any(item.kind == "landing_blocked" for item in daemon.fleet().attention)
        assert git(root, "status", "--porcelain") == ""

    asyncio.run(scenario())


def test_b4_overlapping_owner_commit_blocks_without_writing_refs(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, target="uat")
        root = daemon.project_root
        _ = commit(root, "tracked.txt", "owner version\n")
        checkpoint = await daemon.run_initiative(
            PLAN_ID, "a", runtime=Runtime(root, file="tracked.txt", text="agent version\n"),
            collector=GitCheckpointCollector(checks=("true",), project_root=root),
        )
        assert checkpoint is not None
        before_refs, before_checkout = refs(root), checkout(root)
        _ = daemon.settle_initiative(PLAN_ID, "a", checkpoint.id)
        event = blocked(daemon, "conflict")
        assert event.paths == ["tracked.txt"]
        assert refs(root) == before_refs and checkout(root) == before_checkout
        assert daemon.plan(PLAN_ID).initiatives["a"].state == "settled"

    asyncio.run(scenario())


def test_c_default_target_slug_and_recorded_plan_collision(daemon: Daemon) -> None:
    async def scenario() -> None:
        first = await daemon.create_plan("work", planner=Planner(), plan_id=PLAN_ID)
        second = await daemon.create_plan("work", planner=Planner(), plan_id="plan_87654321abcdef")
        assert first.start_branch == "uat" and first.base_sha == git(daemon.project_root, "rev-parse", "uat")
        assert first.target_branch == "herdsman-land/build-branch-landing" and first.target_derived
        assert second.target_branch == "herdsman-land/build-branch-landing-87654321" and second.target_derived
        assert "refs/heads/herdsman-land/" not in refs(daemon.project_root)

    asyncio.run(scenario())


@pytest.mark.parametrize("title,slug", [("!!!", "12345678"), ("A" * 60, "a" * 48)])
def test_c_existing_ref_collision_and_slug_boundaries(daemon: Daemon, title: str, slug: str) -> None:
    _ = git(daemon.project_root, "branch", f"herdsman-land/{slug}")
    plan = asyncio.run(create(daemon, target=None, planner=Planner(title=title)))
    assert plan.target_branch == f"herdsman-land/{slug}-12345678" and plan.target_derived


@pytest.mark.parametrize("fields,message", [
    ({"target_branch": "master"}, "protected"),
    ({"target_branch": "main"}, "protected"),
    ({"target_branch": "herdsman/x"}, "attempt namespace"),
    ({"target_branch": "bad..name"}, "valid short branch name"),
    ({"start_branch": "unknown"}, "start branch unknown does not exist"),
    ({}, "project root is detached; name a start branch"),
])
def test_c_invalid_choices_return_400_before_planner(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch, fields: dict[str, str], message: str,
) -> None:
    if not fields:
        _ = git(daemon.project_root, "checkout", "--detach", "-q")
    planner = Planner()
    monkeypatch.setattr(daemon, "_frontier_planner", planner.factory)
    before = refs(daemon.project_root)
    status, body = asyncio.run(request(create_app(daemon), "POST", "/plans", {"brief": "work", **fields}))
    assert status == 400 and message in str(body["detail"])
    assert planner.calls == [] and daemon.store.plans() == []
    assert refs(daemon.project_root) == before


@pytest.mark.parametrize("fields", [{"start_branch": "uat"}, {"target_branch": "result"}, {"start_branch": "uat", "target_branch": "result"}])
def test_c_non_git_branch_choices_are_400_before_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fields: dict[str, str],
) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    configure_runtime(root)
    store = EventStore(root / ".herdsman/events.db")
    try:
        daemon = Daemon(store, project_root=root)
        planner = Planner()
        monkeypatch.setattr(daemon, "_frontier_planner", planner.factory)
        status, body = asyncio.run(request(create_app(daemon), "POST", "/plans", {"brief": "work", **fields}))
        assert status == 400 and body["detail"] == "branch choice needs a git repository"
        assert planner.calls == [] and store.plans() == []
    finally:
        store.close()


def test_c_non_git_without_choices_creates_legacy_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    configure_runtime(root)
    store = EventStore(root / ".herdsman/events.db")
    try:
        daemon = Daemon(store, project_root=root)
        planner = Planner()
        monkeypatch.setattr(daemon, "_frontier_planner", planner.factory)
        status, body = asyncio.run(request(create_app(daemon), "POST", "/plans", {"brief": "work"}))
        assert status == 200 and len(planner.calls) == 1
        plan = Plan.model_validate(body)
        assert not plan.lands and plan.base_sha is None and plan.target_branch is None
        assert not landed(daemon, plan.id)
        status, branches = asyncio.run(request(create_app(daemon), "GET", "/branches"))
        assert status == 200
        assert branches == {"git": False, "current": None, "detached": False, "branches": [], "protected": [], "default_target_prefix": "herdsman-land/"}
    finally:
        store.close()


def test_d_legacy_stream_replays_without_base_or_landing(daemon: Daemon) -> None:
    _ = daemon.append(PlanCreated.model_validate({"type": "plan_created", "plan_id": PLAN_ID, "at": AT.isoformat(), "brief": "legacy", "planner": None}))
    _ = daemon.append(PlanProposed(plan_id=PLAN_ID, at=AT, version=1, title="Legacy", initiatives=[spec("a", writes=["a.txt"])]))
    _ = daemon.approve_plan(PLAN_ID)
    replayed = Plan.fold(daemon.store.read(PLAN_ID))
    assert not replayed.lands and replayed.base_sha is None and replayed.target_branch is None
    runtime = Runtime(daemon.project_root)
    root_before = checkout(daemon.project_root)
    _ = asyncio.run(settle(daemon, runtime=runtime))
    assert runtime.bases == [None]
    assert not landed(daemon)
    assert f"refs/herdsman/plans/{PLAN_ID}/" not in refs(daemon.project_root)
    assert checkout(daemon.project_root) == root_before
    status, _ = asyncio.run(request(create_app(daemon), "POST", f"/plans/{PLAN_ID}/land"))
    assert status == 400


class DaemonCrash(BaseException):
    """Bypass landing's ordinary exception handling, like process death."""


def test_d_crash_after_target_write_reconciles_without_second_write(
    daemon: Daemon, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []
    original_write = landing.write_target
    original_append = daemon.append

    def write(root: Path, target: str, result: landing.LandingCommit, pin: str) -> None:
        original_write(root, target, result, pin)
        assert result.sha is not None
        writes.append(result.sha)

    def append(event: Event) -> Event:
        if isinstance(event, PlanLanded):
            raise DaemonCrash()
        return original_append(event)

    monkeypatch.setattr(landing, "write_target", write)
    monkeypatch.setattr(daemon, "append", append)

    async def scenario() -> None:
        _ = await create(daemon)
        with pytest.raises(DaemonCrash):
            _ = await settle(daemon)
        assert len(writes) == 1 and not landed(daemon)
        root = daemon.project_root
        assert git(root, "rev-parse", "new-branch-a") == writes[0]
        assert git(root, "rev-parse", f"refs/herdsman/plans/{PLAN_ID}/landed") == writes[0]
        before = refs(root), checkout(root)
        daemon.store.close()
        restarted_store = EventStore(root / ".herdsman/events.db")
        try:
            restarted = Daemon(restarted_store, project_root=root)
            await restarted.reconcile_stale()
            event, = landed(restarted)
            assert event.mode == "created" and event.previous_sha is None and event.commit_sha == writes[0]
            checkpoint = restarted.plan(PLAN_ID).initiatives["a"].attempts[-1].checkpoint
            assert checkpoint is not None and event.checkpoint_ids == [checkpoint.id]
            assert restarted.plan(PLAN_ID).landing_blocked is None
            assert len(writes) == 1 and (refs(root), checkout(root)) == before
            await restarted.reconcile_stale()
            assert len(landed(restarted)) == 1 and len(writes) == 1
        finally:
            restarted_store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["symbolic_target", "checked_out_elsewhere"])
def test_d_unsafe_target_blocks_without_mutating_checkout_or_refs(daemon: Daemon, kind: str) -> None:
    async def scenario() -> None:
        _ = await create(daemon)
        root = daemon.project_root
        if kind == "symbolic_target":
            _ = git(root, "symbolic-ref", "refs/heads/new-branch-a", "refs/heads/uat")
        else:
            _ = git(root, "worktree", "add", "-q", "-b", "new-branch-a", str(root.parent / "owners-worktree"))
        checkpoint = await daemon.run_initiative(
            PLAN_ID, "a", runtime=Runtime(root),
            collector=GitCheckpointCollector(checks=("true",), project_root=root),
        )
        assert checkpoint is not None
        before = refs(root), checkout(root)
        _ = daemon.settle_initiative(PLAN_ID, "a", checkpoint.id)
        _ = blocked(daemon, kind)
        assert (refs(root), checkout(root)) == before
        if kind == "symbolic_target":
            assert git(root, "symbolic-ref", "refs/heads/new-branch-a") == "refs/heads/uat"
        else:
            assert checkout(root.parent / "owners-worktree")[1] == git(root, "rev-parse", "uat")
            assert not (root.parent / "owners-worktree/a.txt").exists()

    asyncio.run(scenario())


def test_d_all_cancelled_never_lands(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Planner("a", "b"))
        before = refs(daemon.project_root), checkout(daemon.project_root)
        _ = await daemon.cancel_initiative(PLAN_ID, "a")
        _ = await daemon.cancel_initiative(PLAN_ID, "b")
        await daemon.reconcile_stale()
        assert all(node.state == "cancelled" for node in daemon.plan(PLAN_ID).initiatives.values())
        assert not landed(daemon) and daemon.plan(PLAN_ID).landing_blocked is None
        assert (refs(daemon.project_root), checkout(daemon.project_root)) == before

    asyncio.run(scenario())


def test_d_cancelling_last_sink_lands_only_settled_checkpoints(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon, planner=Planner("a", "b", "c", dag=True))
        a, b = await settle(daemon, "a"), await settle(daemon, "b")
        assert not landed(daemon)
        _ = await daemon.cancel_initiative(PLAN_ID, "c")
        event, = landed(daemon)
        assert event.checkpoint_ids == [a.id, b.id] and event.commit_sha is not None
        assert_files(daemon.project_root, event.commit_sha, "a", "b")
        assert "c.txt" not in git(daemon.project_root, "ls-tree", "-r", "--name-only", event.commit_sha).splitlines()

    asyncio.run(scenario())


def test_d_recalibration_adds_second_fast_forward_landing_on_fixed_target(daemon: Daemon) -> None:
    async def scenario() -> None:
        _ = await create(daemon)
        a = await settle(daemon)
        first, = landed(daemon)
        assert first.commit_sha is not None
        _ = await daemon.recalibrate(PLAN_ID, planner=Planner("d", title="Changed planner title"))
        assert daemon.plan(PLAN_ID).target_branch == "new-branch-a"
        assert len(landed(daemon)) == 1
        _ = daemon.approve_plan(PLAN_ID)
        d = await settle(daemon, "d")
        first, second = landed(daemon)
        assert second.mode == "fast_forward" and second.previous_sha == first.commit_sha
        assert second.checkpoint_ids == [a.id, d.id] and second.commit_sha is not None
        assert git(daemon.project_root, "show", "-s", "--format=%P", second.commit_sha) == first.commit_sha
        assert_files(daemon.project_root, second.commit_sha, "a", "d")
        before = refs(daemon.project_root), checkout(daemon.project_root)
        status, _ = await request(create_app(daemon), "POST", f"/plans/{PLAN_ID}/land")
        assert status == 200 and len(landed(daemon)) == 2
        assert (refs(daemon.project_root), checkout(daemon.project_root)) == before

    asyncio.run(scenario())


def test_d_get_branches_shape_protection_and_attempt_filter(daemon: Daemon) -> None:
    root = daemon.project_root
    sha = git(root, "rev-parse", "HEAD")
    _ = git(root, "branch", "master")
    _ = git(root, "branch", "herdsman/hidden")
    _ = git(root, "branch", "herdsman-land/visible")
    before = refs(root), checkout(root)
    status, body = asyncio.run(request(create_app(daemon), "GET", "/branches"))
    assert status == 200
    assert body == {
        "git": True, "current": "uat", "detached": False,
        "branches": [
            {"name": "herdsman-land/visible", "sha": sha, "checked_out": False, "protected": False},
            {"name": "master", "sha": sha, "checked_out": False, "protected": True},
            {"name": "uat", "sha": sha, "checked_out": True, "protected": False},
        ],
        "protected": ["main", "master"], "default_target_prefix": "herdsman-land/",
    }
    assert (refs(root), checkout(root)) == before


@pytest.mark.real_herdr
@pytest.mark.skipif(os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1", reason="needs installed herdr")
def test_real_herdr_base_forks_from_non_head_pin(tmp_path: Path, herdr_workspaces: None) -> None:
    del herdr_workspaces  # The shared fixture cleans up only test-created workspaces.
    root = tmp_path / "live-project"
    u0 = init_repo(root)
    ref = f"refs/herdsman/plans/{PLAN_ID}/base"
    _ = git(root, "update-ref", "--no-deref", ref, u0, "")
    u1 = commit(root, "tracked.txt", "root moved\n")
    assert u0 != u1
    before = refs(root), checkout(root)

    async def scenario() -> None:
        adapter = HerdrAdapter(project_root=root)
        worktree: str | None = None
        try:
            try:
                worktree = await adapter.create_worktree("landing-base-live", base=ref)
            except HerdrOperationError:
                # §7: prove raw SHA once, but do not silently accept a broken ref contract.
                worktree = await adapter.create_worktree("landing-base-live", base=u0)
                pytest.fail("herdr rejects ref-form base; raw SHA works, daemon fallback required")
            path = await adapter.worktree_path(worktree)
            assert git(path, "rev-parse", "HEAD") == u0
            assert (path / "tracked.txt").read_text() == "base\n"
            assert checkout(root) == before[1]
            assert git(root, "rev-parse", ref) == u0
        finally:
            try:
                if worktree is not None:
                    await adapter.remove_worktree(worktree)
            finally:
                await adapter.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 60))
