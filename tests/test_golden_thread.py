import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing_extensions import override

import pytest

from herdsman.checkpoint import Completion, GitCheckpointCollector
from herdsman.classes import RuntimeObserved
from herdsman.daemon import Daemon
from herdsman.herdr import AgentLaunch, RuntimeInventory
from herdsman.store import EventStore


AT = datetime(2026, 8, 25, tzinfo=UTC)


class FakeRuntime:
    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, str]] = []

    async def create_worktree(self, branch: str) -> str:
        self.calls.append(("create", branch))
        return "opaque-worktree"

    async def worktree_path(self, worktree_ref: str) -> Path:
        assert worktree_ref == "opaque-worktree"
        return self.path

    async def run(self, worktree_ref: str, launch: AgentLaunch) -> str:
        self.calls.append(("run", launch.prompt))
        assert worktree_ref == "opaque-worktree"
        assert " at " in launch.prompt
        assert "Plan" not in launch.prompt
        return "opaque-pane"

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        rearm: bool = False,
    ):
        assert pane_ref == "opaque-pane"
        assert not rearm
        yield RuntimeObserved(
            plan_id=plan_id,
            at=AT,
            attempt_id=attempt_id,
            kind="agent_settled",
            detail={"agent_status": "idle"},
        )

    async def aclose(self) -> None:
        self.calls.append(("aclose", ""))

    async def remove_worktree(self, worktree_ref: str) -> None:
        self.calls.append(("remove", worktree_ref))

    async def inventory(self) -> RuntimeInventory:
        return RuntimeInventory((), ())


class DelayedRuntime(FakeRuntime):
    delay: float

    def __init__(self, path: Path, delay: float) -> None:
        super().__init__(path)
        self.delay = delay

    @override
    async def create_worktree(self, branch: str) -> str:
        await asyncio.sleep(self.delay)
        return await super().create_worktree(branch)

    @override
    async def run(self, worktree_ref: str, launch: AgentLaunch) -> str:
        await asyncio.sleep(self.delay)
        return await super().run(worktree_ref, launch)

    @override
    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        rearm: bool = False,
    ):
        await asyncio.sleep(self.delay)
        async for event in super().observe_events(
            plan_id, attempt_id, pane_ref, rearm=rearm
        ):
            yield event


class CancellableRuntime(FakeRuntime):
    observing: asyncio.Event
    release: asyncio.Event

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.observing = asyncio.Event()
        self.release = asyncio.Event()

    @override
    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        rearm: bool = False,
    ):
        _ = self.observing.set()
        _ = await self.release.wait()
        async for event in super().observe_events(
            plan_id, attempt_id, pane_ref, rearm=rearm
        ):
            yield event


class FakePlanner:
    async def propose(self, brief: str) -> object:
        return {
            "title": "Test plan", "initiatives": [
                {
                    "id": "init_1",
                    "name": "one node",
                    "brief": brief,
                    "assignment": {"harness": "luna", "model": "cheap-1"},
                }
            ]
        }


def git_repo(path: Path, *, luna_binary: str = "luna-test") -> None:
    _ = subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    _ = subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    _ = subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    _ = (path / "tracked.txt").write_text("base\n")
    mapping = path / ".herdsman" / "luna.json"
    mapping.parent.mkdir()
    _ = mapping.write_text(json.dumps({"binary": luna_binary}) + "\n")
    _ = subprocess.run(["git", "add", "tracked.txt", str(mapping)], cwd=path, check=True)
    _ = subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)


def test_collector_records_untracked_and_deleted_paths(tmp_path: Path) -> None:
    git_repo(tmp_path)
    _ = (tmp_path / "new.txt").write_text("new\n")
    (tmp_path / "tracked.txt").unlink()
    collector = GitCheckpointCollector(checks=("true",))

    base_sha = collector.capture_base(tmp_path)
    checkpoint = collector.collect(
        tmp_path,
        "attempt_1",
        Completion(),
        base_sha=base_sha,
    )

    assert set(checkpoint.changed_paths) == {"new.txt", "tracked.txt"}
    assert checkpoint.diff_lines == 2
    assert checkpoint.base_sha == base_sha
    assert checkpoint.head_sha == base_sha
    assert checkpoint.checks[0].passed


def test_create_approve_run_checkpoint_then_explicit_settle(tmp_path: Path) -> None:
    git_repo(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    runtime = FakeRuntime(tmp_path)

    async def scenario() -> None:
        plan = await daemon.create_plan(
            "make one change", planner=FakePlanner(), plan_id="plan_1"
        )
        assert plan.approval == "pending"
        with pytest.raises(PermissionError):
            _ = await daemon.run_initiative("plan_1", "init_1", runtime=runtime)
        assert not runtime.calls

        _ = daemon.approve_plan("plan_1")
        checkpoint = await daemon.run_initiative(
            "plan_1",
            "init_1",
            runtime=runtime,
            collector=GitCheckpointCollector(checks=("true",)),
        )
        assert checkpoint is not None
        assert checkpoint.usage is None
        assert checkpoint.base_sha == checkpoint.head_sha
        # `run_initiative` is the primitive: it records evidence and judges
        # none of it. `run_and_settle` is what applies the settlement policy.
        assert daemon.store.load("plan_1").initiatives["init_1"].state == "running"
        assert [event.type for event in store.read("plan_1")] == [
            "plan_created",
            "plan_proposed",
            "plan_approved",
            "attempt_started",
            "attempt_provisioned",  # the worktree, persisted before anything can fail
            "attempt_provisioned",  # the pane, once herdr has launched it
            "runtime_observed",
            "checkpoint_recorded",
        ]

        attempt_id = daemon.store.load("plan_1").initiatives["init_1"].attempts[0].id
        with pytest.raises(ValueError, match="must be failed, cancelled, or settled"):
            _ = await daemon.discard_initiative(
                "plan_1", "init_1", attempt_id, runtime=runtime
            )
        assert [call[0] for call in runtime.calls] == ["create", "run", "aclose"]

        _ = daemon.settle_initiative("plan_1", "init_1", checkpoint.id)
        assert daemon.store.load("plan_1").initiatives["init_1"].state == "settled"
        _ = await daemon.discard_initiative(
            "plan_1", "init_1", attempt_id, runtime=runtime
        )
        assert daemon.store.load("plan_1").initiatives["init_1"].state == "settled"
        assert [call[0] for call in runtime.calls] == ["create", "run", "aclose", "remove"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_run_timeout_covers_runtime_and_checkpoint_checks(tmp_path: Path) -> None:
    git_repo(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    runtime = DelayedRuntime(tmp_path, delay=0.03)

    async def scenario() -> None:
        _ = await daemon.create_plan("brief", planner=FakePlanner(), plan_id="plan_1")
        _ = daemon.approve_plan("plan_1")
        with pytest.raises(RuntimeError, match="timed out"):
            _ = await daemon.run_initiative(
                "plan_1",
                "init_1",
                runtime=runtime,
                collector=GitCheckpointCollector(checks=("sleep 1",)),
                timeout=0.12,
            )

    try:
        asyncio.run(scenario())
        assert [call[0] for call in runtime.calls] == ["create", "run", "aclose"]
        assert [event.type for event in store.read("plan_1")][-1] == "initiative_failed"
    finally:
        store.close()


def test_cancellation_fails_started_attempt_and_preserves_worktree(tmp_path: Path) -> None:
    git_repo(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    runtime = CancellableRuntime(tmp_path)

    async def scenario() -> None:
        _ = await daemon.create_plan("brief", planner=FakePlanner(), plan_id="plan_1")
        _ = daemon.approve_plan("plan_1")
        task = asyncio.create_task(
            daemon.run_initiative(
                "plan_1",
                "init_1",
                runtime=runtime,
                collector=GitCheckpointCollector(checks=("true",)),
            )
        )
        _ = await runtime.observing.wait()
        _ = task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert [event.type for event in store.read("plan_1")][-1] == "initiative_failed"
        assert store.load("plan_1").initiatives["init_1"].state == "failed"
        assert [call[0] for call in runtime.calls] == ["create", "run", "aclose"]
    finally:
        store.close()
