"""Concurrent DAG execution: overlap, waiting, reservation, and serialization.

Git collection is exercised in `test_golden_thread`; these fakes keep the
subject here on scheduling.
"""

import asyncio
import json
import os
import shlex
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from typing_extensions import override

from herdsman.checkpoint import Completion
from herdsman.classes import (
    Assignment,
    AttemptProvisioned,
    AttemptStarted,
    CheckpointRecorded,
    CheckResult,
    Checkpoint,
    Event,
    InitiativeSpec,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
    Usage,
)
from herdsman.daemon import Daemon
from herdsman.runtime import CHECKPOINT_MARKER, CHECKPOINT_PATTERN
from herdsman.store import EventStore
from tests.test_golden_thread import git_repo

AT = datetime(2026, 9, 2, tzinfo=UTC)


def spec(
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    writes: list[str] | None = None,
) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=f"implement {node_id}",
        assignment=Assignment(harness="luna", model="cheap-1"),
        routes=Routes(writes=writes or []),
        depends_on=depends_on or [],
    )


class Ledger:
    """Shared record of what the fake runtimes did, and when, across a plan."""

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.live: set[str] = set()
        self.overlap: int = 0
        self.order: list[str] = []
        self.commands: dict[str, str] = {}
        self.gate: asyncio.Event | None = gate

    def enter(self, initiative_id: str) -> None:
        self.live.add(initiative_id)
        self.order.append(f"start:{initiative_id}")
        self.overlap = max(self.overlap, len(self.live))

    def leave(self, initiative_id: str) -> None:
        self.live.discard(initiative_id)
        self.order.append(f"end:{initiative_id}")


class FakeRuntime:
    """One per initiative, exactly as the real adapter is used."""

    ledger: Ledger
    exit_code: int
    initiative_id: str

    def __init__(self, ledger: Ledger, *, exit_code: int = 0) -> None:
        self.ledger = ledger
        self.exit_code = exit_code
        self.initiative_id = ""

    async def create_worktree(self, branch: str) -> str:
        # herdsman/{plan}/{initiative}/{attempt}
        self.initiative_id = branch.split("/")[2]
        self.ledger.enter(self.initiative_id)
        return f"worktree-{uuid4().hex}"

    async def worktree_path(self, worktree_ref: str) -> Path:
        assert worktree_ref.startswith("worktree-")
        return Path(".")

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        assert worktree_ref.startswith("worktree-")
        assert match == CHECKPOINT_PATTERN
        self.ledger.commands[self.initiative_id] = command
        return f"pane-{self.initiative_id}"

    async def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str
    ) -> AsyncIterator[RuntimeObserved]:
        assert pane_ref == f"pane-{self.initiative_id}"
        if self.ledger.gate is not None:
            # Hold every agent open until the test releases them, so genuine
            # overlap is the only way this completes.
            _ = await asyncio.wait_for(self.ledger.gate.wait(), timeout=5)
        else:
            await asyncio.sleep(0)
        yield RuntimeObserved(
            plan_id=plan_id,
            at=AT,
            attempt_id=attempt_id,
            kind="pane_output_matched",
            detail={
                "read": {
                    "text": "HERDSMAN_CHECKPOINT "
                    + json.dumps(
                        {
                            "exit_code": self.exit_code,
                            "usage": {
                                "input_tokens": 900,
                                "output_tokens": 100,
                                "source": "harness",
                            },
                        }
                    )
                }
            },
        )

    async def remove_worktree(self, worktree_ref: str) -> None:
        assert worktree_ref.startswith("worktree-")

    async def aclose(self) -> None:
        self.ledger.leave(self.initiative_id)


class FakeCollector:
    """Deterministic evidence; the settlement policy is what is under test."""

    passed: bool
    applied: list[str]

    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.applied = []

    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str:
        assert path.exists()
        assert timeout is None or timeout > 0
        self.applied.extend(str(patch) for patch in inputs)
        return "base-sha"

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        # The daemon must hand collection a live share of the run deadline.
        assert path.exists()
        assert timeout is None or timeout > 0
        return Checkpoint(
            id=f"cp_{uuid4().hex}",
            attempt_id=attempt_id,
            changed_paths=["src/touched.py"],
            base_sha=base_sha,
            head_sha="head-sha",
            checks=[CheckResult(name="true", passed=self.passed)],
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=f".herdsman/artifacts/{attempt_id}.patch",
        )


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    """A daemon on an empty project-local store, with Luna explicitly mapped."""
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir(parents=True)
    _ = mapping.write_text(json.dumps({"binary": "luna-test"}))
    store = EventStore(tmp_path / ".herdsman" / "events.db")
    try:
        yield Daemon(store, project_root=tmp_path)
    finally:
        store.close()


def seed(daemon: Daemon, *specs: InitiativeSpec) -> Daemon:
    events: list[Event] = [
        PlanCreated(plan_id="p", at=AT, brief="brief", planner=None),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=list(specs)),
        PlanApproved(plan_id="p", at=AT, version=1),
    ]
    for event in events:
        _ = daemon.append(event)
    return daemon


def test_two_independent_initiatives_execute_concurrently(daemon: Daemon) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        ledger = Ledger(gate=gate)
        _ = seed(daemon, spec("a", writes=["a/"]), spec("b", writes=["b/"]))

        async def release() -> None:
            # Only both agents being live at once can satisfy this.
            while len(ledger.live) < 2:
                await asyncio.sleep(0)
            gate.set()

        releaser = asyncio.create_task(release())
        plan = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(),
        )
        await releaser

        assert ledger.overlap == 2
        assert plan.initiatives["a"].state == "settled"
        assert plan.initiatives["b"].state == "settled"

    asyncio.run(scenario())


def test_a_downstream_initiative_waits_for_its_dependencies(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(
            daemon,
            spec("a", writes=["a/"]),
            spec("b", writes=["b/"]),
            spec("c", depends_on=["a", "b"], writes=["c/"]),
        )
        plan = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(),
        )
        assert all(
            initiative.state == "settled" for initiative in plan.initiatives.values()
        )
        # c cannot start until both producers have finished.
        started_c = ledger.order.index("start:c")
        assert ledger.order.index("end:a") < started_c
        assert ledger.order.index("end:b") < started_c

    asyncio.run(scenario())


def test_a_downstream_initiative_receives_only_upstream_checkpoint_references(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(
            daemon,
            spec("upstream", writes=["a/"]),
            spec("downstream", depends_on=["upstream"], writes=["c/"]),
        )
        plan = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(),
        )
        upstream_checkpoint = plan.initiatives["upstream"].attempts[0].checkpoint
        assert upstream_checkpoint is not None

        command = ledger.commands["downstream"]
        assert upstream_checkpoint.id in command
        assert "head-sha" in command
        assert "src/touched.py" in command
        # The executor gets a reference, not the plan and not a sibling brief.
        assert "implement upstream" not in command
        assert "depends_on" not in command
        # Sprint 14's slot exists and is empty.
        assert '"memory":[]' in command

        assert ledger.commands["upstream"].count('"inputs":[]') == 1

    asyncio.run(scenario())


def test_overlapping_writers_never_run_at_the_same_time(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(
            daemon,
            # No dependency between them, but both write the same subtree.
            spec("a", writes=["herdsman/"]),
            spec("b", writes=["herdsman/daemon.py"]),
        )
        plan = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(),
        )
        assert ledger.overlap == 1
        assert all(
            initiative.state == "settled" for initiative in plan.initiatives.values()
        )

    asyncio.run(scenario())


def test_a_concurrent_run_of_one_initiative_cannot_start_a_second_agent(daemon: Daemon) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        ledger = Ledger(gate=gate)
        _ = seed(daemon, spec("a", writes=["a/"]))

        async def attempt() -> object:
            return await daemon.run_initiative(
                "p",
                "a",
                runtime=FakeRuntime(ledger),
                collector=FakeCollector(),
            )

        first = asyncio.create_task(attempt())
        second = asyncio.create_task(attempt())
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

        # One reservation wins; the loser is refused before it provisions.
        assert sum(isinstance(result, Checkpoint) for result in results) == 1
        assert sum(isinstance(result, Exception) for result in results) == 1
        assert len(daemon.plan("p").initiatives["a"].attempts) == 1
        assert ledger.order.count("start:a") == 1

    asyncio.run(scenario())


def test_a_failed_check_does_not_settle_or_release_downstream(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(
            daemon,
            spec("a", writes=["a/"]),
            spec("b", depends_on=["a"], writes=["b/"]),
        )
        plan = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(passed=False),
        )
        assert plan.initiatives["a"].state == "failed"
        assert plan.initiatives["b"].state == "pending"
        assert "start:b" not in ledger.order
        # The evidence is kept; only the DAG stopped.
        assert plan.initiatives["a"].attempts[0].checkpoint is not None

    asyncio.run(scenario())


def test_overhead_ratio_measures_injected_context_against_harness_usage(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]))
        _ = await daemon.run_plan(
            "p",
            runtime_factory=lambda: FakeRuntime(ledger),
            collector=FakeCollector(),
        )
        measured = daemon.overhead("p")
        assert measured.productive_tokens == 1000
        assert measured.orchestration_tokens > 0
        assert measured.ratio == measured.orchestration_tokens / 1000
        assert measured.ratio is not None
        assert measured.within_target is (measured.ratio <= 0.20)
        assert measured.within_target is True

        graph = daemon.graph("p")
        assert graph.overhead.ratio == measured.ratio
        assert graph.max_concurrency == 1

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1",
    reason="set HERDSMAN_TEST_REAL_HERDR=1 to exercise the installed herdr daemon",
)
@pytest.mark.usefixtures("herdr_workspaces")
def test_real_herdr_runs_two_initiatives_concurrently_then_a_consumer(
    tmp_path: Path,
) -> None:
    """The Sprint 2 exit criteria against the installed herdr.

    Every fake-based pass of Sprint 1's thread went green while the live path
    was broken, twice.  Nothing is faked here: herdr opens a worktree and pane
    per initiative, and overlap is read back off the persisted event stream
    rather than off a test-local counter.
    """
    stub = tmp_path / "luna-stub"
    payload = (
        '{"exit_code":0,"usage":'
        '{"input_tokens":900,"output_tokens":100,"source":"harness"}}'
    )
    # `a` and `b` each produce a sentinel; `c` consumes both and only reports
    # success if it can actually read them, so a handoff that moves no bytes
    # fails this test rather than passing on metadata alone.
    _ = stub.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                'case "$*" in',
                '  *"implement a"*) printf \'from-a\\n\' > a.txt ;;',
                '  *"implement b"*) printf \'from-b\\n\' > b.txt ;;',
                '  *"implement c"*)',
                '    [ "$(cat a.txt)" = "from-a" ] || exit 3',
                '    [ "$(cat b.txt)" = "from-b" ] || exit 4',
                '    printf \'merged\\n\' > c.txt ;;',
                'esac',
                # Long enough that a serial scheduler could not overlap them.
                "sleep 3",
                f"printf '%s\\n' {shlex.quote(f'{CHECKPOINT_MARKER} {payload}')}",
                "",
            )
        )
    )
    stub.chmod(0o755)
    git_repo(tmp_path, luna_binary=str(stub))
    store = EventStore(tmp_path / ".herdsman" / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    for event in (
        PlanCreated(plan_id="p", at=AT, brief="two independent changes", planner=None),
        PlanProposed(
            plan_id="p",
            at=AT,
            version=1,
            initiatives=[
                spec("a", writes=["a/"]),
                spec("b", writes=["b/"]),
                spec("c", depends_on=["a", "b"], writes=["c/"]),
            ],
        ),
        PlanApproved(plan_id="p", at=AT, version=1),
    ):
        _ = daemon.append(event)

    async def scenario() -> None:
        try:
            plan = await daemon.run_plan("p", checks=("true",), timeout=180.0)
            assert all(
                initiative.state == "settled"
                for initiative in plan.initiatives.values()
            ), {i: n.state for i, n in plan.initiatives.items()}

            # c could only have written this by reading a's and b's output.
            consumer = plan.initiatives["c"].attempts[-1].checkpoint
            assert consumer is not None
            assert consumer.exit_code == 0
            assert consumer.changed_paths == ["c.txt"], consumer.changed_paths

            # Overlap is measured on pane launches, not on reservations:
            # `AttemptStarted` is deliberately appended before provisioning, so
            # comparing those timestamps would pass even on a serial run.
            events = store.read("p")
            attempts = {
                event.attempt_id: event.initiative_id
                for event in events
                if isinstance(event, AttemptStarted)
            }
            launched = [
                attempts[event.attempt_id]
                for event in events
                if isinstance(event, AttemptProvisioned) and event.pane_ref is not None
            ]
            first_checkpoint = next(
                index
                for index, event in enumerate(events)
                if isinstance(event, CheckpointRecorded)
            )
            launches_before_first_finish = [
                attempts[event.attempt_id]
                for event in events[:first_checkpoint]
                if isinstance(event, AttemptProvisioned) and event.pane_ref is not None
            ]
            # Both panes were live before either produced a checkpoint.
            assert sorted(launches_before_first_finish) == ["a", "b"]
            assert launched[-1] == "c"

            measured = daemon.overhead("p")
            assert measured.ratio is not None
            assert measured.within_target is True, measured
        finally:
            for initiative_id, initiative in daemon.plan("p").initiatives.items():
                for attempt in initiative.attempts:
                    if attempt.worktree_ref is not None:
                        _ = await daemon.discard_initiative(
                            "p", initiative_id, attempt.id
                        )

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_a_direct_run_is_refused_while_a_conflicting_writer_is_running(
    daemon: Daemon,
) -> None:
    """Scope admission is the daemon's rule, not one scheduler's bookkeeping."""

    async def scenario() -> None:
        gate = asyncio.Event()
        ledger = Ledger(gate=gate)
        _ = seed(
            daemon,
            spec("a", writes=["herdsman/"]),
            spec("b", writes=["herdsman/daemon.py"]),
        )
        held = asyncio.create_task(
            daemon.run_initiative(
                "p", "a", runtime=FakeRuntime(ledger), collector=FakeCollector()
            )
        )
        while "a" not in ledger.live:
            await asyncio.sleep(0)

        # `b` is ready and nothing about `a` is in this caller's bookkeeping.
        assert "b" in daemon.plan("p").ready()
        with pytest.raises(ValueError, match="writes where running a writes"):
            _ = await daemon.run_initiative(
                "p", "b", runtime=FakeRuntime(ledger), collector=FakeCollector()
            )
        assert ledger.order.count("start:b") == 0
        # Refused before reserving, so `b` is untouched and still runnable later.
        assert daemon.plan("p").initiatives["b"].attempts == []

        gate.set()
        _ = await held

    asyncio.run(scenario())


def test_a_single_initiative_run_settles_on_clean_evidence(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]), spec("b", depends_on=["a"]))
        checkpoint = await daemon.run_and_settle(
            "p", "a", runtime=FakeRuntime(ledger), collector=FakeCollector()
        )
        assert checkpoint is not None
        # Identical evidence settles identically whichever path produced it.
        assert daemon.plan("p").initiatives["a"].state == "settled"
        assert daemon.plan("p").ready() == ["b"]

    asyncio.run(scenario())


def test_an_operator_can_settle_evidence_the_policy_refused(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]), spec("b", depends_on=["a"]))
        checkpoint = await daemon.run_and_settle(
            "p", "a", runtime=FakeRuntime(ledger), collector=FakeCollector(passed=False)
        )
        assert checkpoint is not None
        assert daemon.plan("p").initiatives["a"].state == "failed"
        assert daemon.plan("p").ready() == []

        # The evidence was retained, so the operator can accept it by hand and
        # release the dependent.
        plan = daemon.settle_initiative("p", "a", checkpoint.id)
        assert plan.initiatives["a"].state == "settled"
        assert plan.ready() == ["b"]

    asyncio.run(scenario())


def test_the_scheduler_rejects_non_positive_limits_instead_of_hanging(
    daemon: Daemon,
) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]))
        factory = lambda: FakeRuntime(ledger)  # noqa: E731
        with pytest.raises(ValueError, match="timeout must be positive"):
            _ = await daemon.run_plan(
                "p", runtime_factory=factory, collector=FakeCollector(), timeout=0
            )
        with pytest.raises(ValueError, match="max_concurrent must be positive"):
            _ = await daemon.run_plan(
                "p",
                runtime_factory=factory,
                collector=FakeCollector(),
                max_concurrent=0,
            )
        with pytest.raises(ValueError, match="max_concurrent must be positive"):
            _ = await daemon.run_plan(
                "p",
                runtime_factory=factory,
                collector=FakeCollector(),
                max_concurrent=-1,
            )
        assert daemon.plan("p").initiatives["a"].state == "pending"

    asyncio.run(scenario())


class RefusingDaemon(Daemon):
    """A daemon whose runs fail before reserving an attempt.

    That is the shape the scheduler has to survive: the initiative stays
    `pending` and therefore still ready, so a naive loop reschedules the same
    doomed run forever. Real causes are pre-reservation refusals -- a rejected
    argument, a denied scope admission.
    """

    attempted: list[str]

    def __init__(self, daemon: Daemon) -> None:
        super().__init__(daemon.store, project_root=daemon.project_root)
        self.attempted = []

    @override
    async def run_initiative(
        self, plan_id: str, initiative_id: str, **kwargs: object
    ) -> Checkpoint | None:
        self.attempted.append(initiative_id)
        if len(self.attempted) > 20:
            raise AssertionError("scheduler is looping on a doomed run")
        raise ValueError("refused before reserving an attempt")


def test_a_run_that_fails_without_reserving_is_not_rescheduled_forever(
    daemon: Daemon,
) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]), spec("b", writes=["b/"]))
        refusing = RefusingDaemon(daemon)
        plan = await asyncio.wait_for(
            refusing.run_plan(
                "p",
                runtime_factory=lambda: FakeRuntime(ledger),
                collector=FakeCollector(),
            ),
            timeout=5,
        )
        # Each initiative is attempted once, then set aside.
        assert sorted(refusing.attempted) == ["a", "b"]
        assert all(node.state == "pending" for node in plan.initiatives.values())

    asyncio.run(scenario())


def test_planner_usage_counts_as_productive_work(daemon: Daemon) -> None:
    async def scenario() -> None:
        ledger = Ledger()
        _ = seed(daemon, spec("a", writes=["a/"]))
        _ = await daemon.run_plan(
            "p", runtime_factory=lambda: FakeRuntime(ledger), collector=FakeCollector()
        )
        executor_only = daemon.overhead("p")
        assert executor_only.productive_tokens == 1000

        # A plan whose planner reported usage counts it in the denominator.
        _ = daemon.append(
            PlanProposed(
                plan_id="p",
                at=AT,
                version=2,
                initiatives=[spec("a", writes=["a/"])],
                usage=Usage(input_tokens=400, output_tokens=100, source="harness"),
            )
        )
        assert daemon.overhead("p").productive_tokens == 1500

    asyncio.run(scenario())


def test_a_failure_after_worktree_creation_still_leaves_it_discardable(
    daemon: Daemon,
) -> None:
    """A worktree whose reference was never persisted could never be released."""

    class FailsAfterWorktree(FakeRuntime):
        removed: list[str]

        def __init__(self, ledger: Ledger) -> None:
            super().__init__(ledger)
            self.removed = []

        @override
        async def worktree_path(self, worktree_ref: str) -> Path:
            raise RuntimeError("herdr lost the checkout path")

        @override
        async def remove_worktree(self, worktree_ref: str) -> None:
            self.removed.append(worktree_ref)

    async def scenario() -> None:
        ledger = Ledger()
        runtime = FailsAfterWorktree(ledger)
        _ = seed(daemon, spec("a", writes=["a/"]))
        with pytest.raises(RuntimeError, match="lost the checkout path"):
            _ = await daemon.run_initiative(
                "p", "a", runtime=runtime, collector=FakeCollector()
            )

        initiative = daemon.plan("p").initiatives["a"]
        assert initiative.state == "failed"
        attempt = initiative.attempts[0]
        # The reference survived the failure, so the workspace is reachable.
        assert attempt.worktree_ref is not None
        assert attempt.pane_ref is None
        _ = await daemon.discard_initiative("p", "a", attempt.id, runtime=runtime)
        assert runtime.removed == [attempt.worktree_ref]

    asyncio.run(scenario())
