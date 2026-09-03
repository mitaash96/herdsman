import asyncio
import json
import os
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing_extensions import override

import pytest

from herdsman.checkpoint import Completion, GitCheckpointCollector
from herdsman.classes import RuntimeObserved, Usage
from herdsman.daemon import Daemon
from herdsman.runtime import CHECKPOINT_MARKER, CHECKPOINT_PATTERN
from herdsman.store import EventStore


AT = datetime(2026, 8, 25, tzinfo=UTC)


class FakeRuntime:
    path: Path
    marker: bool

    def __init__(self, path: Path, *, marker: bool = True) -> None:
        self.path = path
        self.marker = marker
        self.calls: list[tuple[str, str]] = []

    async def create_worktree(self, branch: str) -> str:
        self.calls.append(("create", branch))
        return "opaque-worktree"

    async def worktree_path(self, worktree_ref: str) -> Path:
        assert worktree_ref == "opaque-worktree"
        return self.path

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        self.calls.append(("run", command))
        assert worktree_ref == "opaque-worktree"
        # The wait must be armed before launch, with the anchored marker: a
        # bare substring would match the prompt the shell echoes back.
        assert match == CHECKPOINT_PATTERN
        assert "TASK_PACKET=" in command
        assert "Plan" not in command
        return "opaque-pane"

    async def observe_events(self, plan_id: str, attempt_id: str, pane_ref: str):
        assert pane_ref == "opaque-pane"
        if self.marker:
            yield RuntimeObserved(
                plan_id=plan_id,
                at=AT,
                attempt_id=attempt_id,
                kind="pane_output_matched",
                detail={
                    "read": {
                        "text": (
                            'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'
                            '{"input_tokens":11,"output_tokens":7,"source":"harness"}}'
                        ),
                        "truncated": False,
                    }
                },
            )
        yield RuntimeObserved(
            plan_id=plan_id,
            at=AT,
            attempt_id=attempt_id,
            kind="pane_exited",
            detail={"pane_id": pane_ref},
        )

    async def aclose(self) -> None:
        self.calls.append(("aclose", ""))

    async def remove_worktree(self, worktree_ref: str) -> None:
        self.calls.append(("remove", worktree_ref))


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
    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        await asyncio.sleep(self.delay)
        return await super().run(worktree_ref, command, match=match)

    @override
    async def observe_events(self, plan_id: str, attempt_id: str, pane_ref: str):
        await asyncio.sleep(self.delay)
        async for event in super().observe_events(plan_id, attempt_id, pane_ref):
            yield event


class CancellableRuntime(FakeRuntime):
    observing: asyncio.Event
    release: asyncio.Event

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.observing = asyncio.Event()
        self.release = asyncio.Event()

    @override
    async def observe_events(self, plan_id: str, attempt_id: str, pane_ref: str):
        _ = self.observing.set()
        _ = await self.release.wait()
        async for event in super().observe_events(plan_id, attempt_id, pane_ref):
            yield event


class FakePlanner:
    async def propose(self, brief: str) -> object:
        return {
            "initiatives": [
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
        Completion(
            exit_code=0,
            usage=Usage(input_tokens=1, output_tokens=2, source="harness"),
        ),
        base_sha=base_sha,
    )

    assert set(checkpoint.changed_paths) == {"new.txt", "tracked.txt"}
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
        assert checkpoint.usage is not None
        assert checkpoint.usage.input_tokens == 11
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
            "runtime_observed",
            "checkpoint_recorded",
        ]

        attempt_id = daemon.store.load("plan_1").initiatives["init_1"].attempts[0].id
        with pytest.raises(ValueError, match="must be failed or settled"):
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


def test_pane_exit_without_completion_cannot_settle(tmp_path: Path) -> None:
    git_repo(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    runtime = FakeRuntime(tmp_path, marker=False)

    async def scenario() -> None:
        _ = await daemon.create_plan("brief", planner=FakePlanner(), plan_id="plan_1")
        _ = daemon.approve_plan("plan_1")
        with pytest.raises(RuntimeError, match="CHECKPOINT"):
            _ = await daemon.run_initiative("plan_1", "init_1", runtime=runtime)

    try:
        asyncio.run(scenario())
        plan = store.load("plan_1")
        assert plan.initiatives["init_1"].state == "failed"
        assert not any(event.type == "checkpoint_recorded" for event in store.read("plan_1"))
    finally:
        store.close()


@pytest.mark.skipif(
    os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1",
    reason="set HERDSMAN_TEST_REAL_HERDR=1 to exercise the installed herdr daemon",
)
@pytest.mark.usefixtures("herdr_workspaces")
def test_real_herdr_runs_the_golden_thread_end_to_end(tmp_path: Path) -> None:
    """E1 against the installed herdr: create -> approve -> run -> checkpoint -> settled.

    Every fake-based pass of this thread has gone green while the live path was
    broken -- twice (F1, N7).  So the only faked part here is the frontier
    planner, which needs a model call; the daemon builds its own `HerdrAdapter`,
    herdr makes a real worktree and pane, the real `executor_command` prompt is
    what runs, and the real `GitCheckpointCollector` reads the diff the stub
    executor leaves behind.
    """
    stub = tmp_path / "luna-stub"
    # Stands in for Luna: ignores its arguments, does the "work", then prints
    # the marker.  No `exit`, matching what executor_command now sends.
    payload = (
        '{"exit_code":0,"usage":'
        '{"input_tokens":11,"output_tokens":7,"source":"harness"}}'
    )
    marker_line = shlex.quote(f"{CHECKPOINT_MARKER} {payload}")
    _ = stub.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                "printf 'implemented\\n' > implemented.txt",
                f"printf '%s\\n' {marker_line}",
                "",
            )
        )
    )
    stub.chmod(0o755)
    git_repo(tmp_path, luna_binary=str(stub))
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    async def scenario() -> None:
        _ = await daemon.create_plan(
            "make one change", planner=FakePlanner(), plan_id="plan_1"
        )
        _ = daemon.approve_plan("plan_1")
        checkpoint = await daemon.run_initiative(
            "plan_1",
            "init_1",
            runtime=None,  # the daemon builds a real HerdrAdapter
            collector=GitCheckpointCollector(checks=("true",)),
            timeout=120.0,
        )
        assert checkpoint is not None
        # The stub wrote this in the pane's cwd; the collector found it in the
        # worktree, so the pane really ran where the checkpoint is read.
        assert checkpoint.changed_paths == ["implemented.txt"]
        assert checkpoint.usage is not None
        assert checkpoint.usage.input_tokens == 11
        assert checkpoint.usage.source == "harness"

        initiative = daemon.store.load("plan_1").initiatives["init_1"]
        assert initiative.state == "running"
        try:
            _ = daemon.settle_initiative("plan_1", "init_1", checkpoint.id)
            assert daemon.store.load("plan_1").initiatives["init_1"].state == "settled"
            assert [event.type for event in store.read("plan_1")] == [
                "plan_created",
                "plan_proposed",
                "plan_approved",
                "attempt_started",
                "attempt_provisioned",
                "attempt_provisioned",
                "runtime_observed",
                "checkpoint_recorded",
                "initiative_settled",
            ]
        finally:
            # Must run even when an assertion above fails, or the attempt
            # worktree outlives the test -- which is how w5E was orphaned.
            # Discard builds its own adapter, so this also proves a cold
            # adapter can resolve a recorded worktree ref.
            _ = await daemon.discard_initiative(
                "plan_1", "init_1", initiative.attempts[0].id
            )

    try:
        asyncio.run(scenario())
    finally:
        store.close()
