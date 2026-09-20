"""Exercise installed-wheel DAG admission without claiming a live harness receipt.

The workflow gives this program two executable project-local harness stubs.  The
runtime below is intentionally an in-process transport: it makes scheduling
deterministic on a fresh CI host while the daemon's real scheduler owns
admission, dependency release, and settlement.  It emits the adapter-required
``source=harness`` marker shape but does not inspect or report those synthetic
zero-token values as a metered receipt.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from herdsman.checkpoint import Completion
from herdsman.classes import (
    Assignment,
    Checkpoint,
    CheckResult,
    InitiativeSpec,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
    Usage,
)
from herdsman.daemon import Daemon
from herdsman.herdr import RuntimeInventory
from herdsman.kitchen import Kitchen
from herdsman.runtime import CHECKPOINT_PATTERN
from herdsman.store import EventStore


class Ledger:
    def __init__(self) -> None:
        self.live: set[str] = set()
        self.max_live = 0
        self.started: list[str] = []
        self.finished: list[str] = []
        self.commands: dict[str, list[str]] = {}
        self.output: dict[str, str] = {}
        self.live_processes: set[str] = set()
        self.max_processes = 0
        self.producers_ready = asyncio.Event()

    def start(self, initiative_id: str) -> None:
        self.live.add(initiative_id)
        self.started.append(initiative_id)
        self.max_live = max(self.max_live, len(self.live))
        if {"producer-a", "producer-b"} <= set(self.started):
            self.producers_ready.set()

    def finish(self, initiative_id: str) -> None:
        self.live.discard(initiative_id)
        self.finished.append(initiative_id)


class SchedulerSmokeRuntime:
    """A transport seam that makes concurrent scheduler behavior observable."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.initiative_id = ""
        self.process: asyncio.subprocess.Process | None = None

    async def create_worktree(self, branch: str) -> str:
        self.initiative_id = branch.split("/")[2]
        self.ledger.start(self.initiative_id)
        return f"smoke-worktree-{uuid4().hex}"

    async def worktree_path(self, worktree_ref: str) -> Path:
        assert worktree_ref.startswith("smoke-worktree-")
        return Path.cwd()

    async def run(self, worktree_ref: str, command: str, *, match: str | None = None) -> str:
        assert worktree_ref.startswith("smoke-worktree-")
        assert match == CHECKPOINT_PATTERN
        argv = shlex.split(command)
        expected = "ci-consumer" if self.initiative_id == "consumer" else "ci-producer"
        assert argv[0] == expected, argv
        self.ledger.commands[self.initiative_id] = argv
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=Path.cwd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.ledger.live_processes.add(self.initiative_id)
        self.ledger.max_processes = max(
            self.ledger.max_processes, len(self.ledger.live_processes)
        )
        return f"smoke-pane-{self.initiative_id}"

    async def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str, *, match: str | None = None
    ) -> AsyncIterator[RuntimeObserved]:
        assert pane_ref == f"smoke-pane-{self.initiative_id}"
        assert match is None
        assert self.process is not None
        stdout, stderr = await asyncio.wait_for(self.process.communicate(), timeout=5)
        assert self.process.returncode == 0, stderr.decode("utf-8", errors="replace")
        output = stdout.decode("utf-8", errors="replace")
        self.ledger.live_processes.discard(self.initiative_id)
        assert "HERDSMAN_CHECKPOINT " in output, output
        self.ledger.output[self.initiative_id] = output
        yield RuntimeObserved(
            plan_id=plan_id,
            at=datetime.now(UTC),
            attempt_id=attempt_id,
            kind="pane_output_matched",
            detail={
                "read": {
                    "text": output
                }
            },
        )

    async def remove_worktree(self, worktree_ref: str) -> None:
        assert worktree_ref.startswith("smoke-worktree-")

    async def aclose(self) -> None:
        self.ledger.finish(self.initiative_id)

    async def inventory(self) -> RuntimeInventory:
        return RuntimeInventory((), ())


class SchedulerSmokeCollector:
    """Mechanical-shaped collection with no git or live-metering dependency."""

    def capture_base(
        self, path: Path, *, inputs: Sequence[Path] = (), timeout: float | None = None
    ) -> str:
        assert path.exists()
        assert all(input_path.exists() for input_path in inputs), inputs
        assert timeout is None or timeout > 0
        return "smoke-base"

    def diagnose(
        self, path: Path, attempt_id: str, *, base_sha: str, timeout: float | None = None
    ) -> None:
        del path, attempt_id, base_sha, timeout
        return None

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        assert path.exists()
        assert timeout is None or timeout > 0
        patch_path = f".herdsman/artifacts/{attempt_id}.patch"
        artifact = path / patch_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("CI scheduler smoke handoff\n", encoding="utf-8")
        return Checkpoint(
            id=f"smoke-checkpoint-{uuid4().hex}",
            attempt_id=attempt_id,
            changed_paths=[],
            base_sha=base_sha,
            head_sha="smoke-head",
            checks=[CheckResult(name="smoke", passed=True)],
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=patch_path,
        )


def spec(
    node_id: str,
    harness: str,
    *,
    depends_on: list[str] | None = None,
    approval: str = "automatic",
) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=f"fresh-machine smoke {node_id}",
        assignment=Assignment(harness=harness, model="stub"),
        routes=Routes(),
        depends_on=depends_on or [],
        approval=approval,
    )


async def main() -> None:
    kitchen = Kitchen.load(Path.cwd())
    configured = {adapter.name for adapter in kitchen.adapters}
    assert configured == {"ci-producer", "ci-consumer"}, configured
    assert {(model.harness, model.model) for model in kitchen.models} == {
        ("ci-producer", "stub"),
        ("ci-consumer", "stub"),
    }

    store = EventStore(Path(".herdsman/events.db"))
    ledger = Ledger()
    try:
        daemon = Daemon(store, project_root=Path.cwd())
        plan_id = "fresh-machine-multi-harness"
        for event in (
            PlanCreated(plan_id=plan_id, at=datetime.now(UTC), brief="scheduler smoke"),
            PlanProposed(
                plan_id=plan_id,
                at=datetime.now(UTC),
                version=1,
                initiatives=[
                    spec("producer-a", "ci-producer", approval="required"),
                    spec("producer-b", "ci-producer", approval="required"),
                    spec("consumer", "ci-consumer", depends_on=["producer-a", "producer-b"]),
                ],
            ),
            PlanApproved(plan_id=plan_id, at=datetime.now(UTC), version=1),
        ):
            daemon.append(event)

        plan = await daemon.run_plan(
            plan_id,
            runtime_factory=lambda: SchedulerSmokeRuntime(ledger),
            collector=SchedulerSmokeCollector(),
            checks=("true",),
            timeout=10,
        )
        assert {node_id: node.state for node_id, node in plan.initiatives.items()} == {
            "producer-a": "running",
            "producer-b": "running",
            "consumer": "pending",
        }
        assert ledger.max_live >= 2, ledger.max_live
        assert ledger.max_processes >= 2, ledger.max_processes
        assert ledger.started == ["producer-a", "producer-b"], ledger.started
        assert set(ledger.commands) == {"producer-a", "producer-b"}, ledger.commands
        # The consumer has both checkpoints available, but remains blocked until
        # their explicit approvals settle the required-review producers.
        checkpoint_a = plan.initiatives["producer-a"].attempts[-1].checkpoint
        assert checkpoint_a is not None
        plan = daemon.approve_checkpoint(plan_id, checkpoint_a.id, reason="CI smoke")
        assert plan.initiatives["consumer"].state == "pending"
        assert ledger.started == ["producer-a", "producer-b"], ledger.started
        checkpoint_b = plan.initiatives["producer-b"].attempts[-1].checkpoint
        assert checkpoint_b is not None
        plan = daemon.approve_checkpoint(plan_id, checkpoint_b.id, reason="CI smoke")

        plan = await daemon.run_plan(
            plan_id,
            runtime_factory=lambda: SchedulerSmokeRuntime(ledger),
            collector=SchedulerSmokeCollector(),
            checks=("true",),
            timeout=10,
        )
        assert all(node.state == "settled" for node in plan.initiatives.values()), {
            node_id: node.state for node_id, node in plan.initiatives.items()
        }
        assert ledger.started[:2] == ["producer-a", "producer-b"], ledger.started
        assert ledger.started[-1] == "consumer", ledger.started
        assert {"producer-a", "producer-b"} <= set(ledger.finished[:2]), ledger.finished
        assert ledger.commands["consumer"][0] == "ci-consumer"
        assert "merged" in ledger.output["consumer"]
        assignments = {node_id: node.attempts[-1].assignment.harness for node_id, node in plan.initiatives.items()}
        assert assignments == {
            "producer-a": "ci-producer",
            "producer-b": "ci-producer",
            "consumer": "ci-consumer",
        }, assignments
    finally:
        store.close()


asyncio.run(main())
