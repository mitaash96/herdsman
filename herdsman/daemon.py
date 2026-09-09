"""In-process daemon and its minimal HTTP surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, BaseModel, Field

from . import nav, walkthrough
from .checkpoint import CheckpointError, Completion, GitCheckpointCollector
from .classes import (
    ArtifactRef,
    Attempt,
    Checkpoint,
    AttemptProvisioned,
    AttemptStarted,
    CheckpointApproved,
    CheckpointChangesRequested,
    CheckpointRecorded,
    CheckpointRejected,
    Assignment,
    CheckResult,
    ContractViolation,
    Event,
    Initiative,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    MemoryLeaf,
    OperatorAnswered,
    Plan,
    PlanApproved,
    PlanCreated,
    ProcessRestarted,
    RuntimeObserved,
    TaskNudged,
    TaskReassigned,
    TaskRedirected,
    Taint,
)
from .contracts import (
    VERIFY_CHECK,
    ContractError,
    summarize_violations,
    validate_checkpoint,
)
from .graph import (
    DownstreamImpact,
    Overhead,
    PlanGraph,
    RiskReport,
    ancestor_patches,
    conflicts_with,
    contention,
    downstream_impact,
    max_concurrency,
    overhead,
    plan_graph,
    risk_report,
)
from .herdr import HerdrAdapter
from .runtime import (
    CHECKPOINT_PATTERN,
    CompletionError,
    PiFrontierPlanner,
    PlannerError,
    completion_from_detail,
    compile_task_packet,
    estimate_tokens,
    executor_command,
    proposal_from_result,
    resolve_model_tiers,
    LunaConfigError,
)
from .store import EventStore
from .verifier import Verifier


class Runtime(Protocol):
    async def create_worktree(self, branch: str) -> str: ...

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str: ...

    def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str
    ) -> AsyncIterator[RuntimeObserved]: ...

    async def remove_worktree(self, worktree_ref: str) -> None: ...

    async def aclose(self) -> None: ...

    async def worktree_path(self, worktree_ref: str) -> Path: ...


class PaneRuntime(Protocol):
    """The live-pane primitives interventions use (the herdr adapter's)."""

    async def nudge_pane(self, pane_ref: str, text: str) -> None: ...

    async def focus_pane(self, pane_ref: str) -> None: ...

    async def restart_process(self, pane_ref: str, command: str) -> str: ...

    async def aclose(self) -> None: ...


class Collector(Protocol):
    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str: ...

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint: ...


class Daemon:
    """The event store's single writer and live in-process event fan-out."""

    def __init__(self, store: EventStore, *, project_root: str | Path = ".") -> None:
        self.store: EventStore = store
        self.project_root: Path = Path(project_root).expanduser().resolve()
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        # ponytail: launch commands live in daemon memory so `restart_process`
        # can re-issue exactly what the attempt got; persisted packets are
        # Sprint 6-A and surviving the cache is Sprint 5's recovery.
        self._attempt_commands: dict[str, str] = {}

    def plan(self, plan_id: str) -> Plan:
        """Return a plan rebuilt from its persisted event stream."""
        return self.store.load(plan_id)

    def append(self, event: Event) -> Event:
        """Persist an event, then make that persisted event visible to subscribers."""
        persisted = self.store.append(event)
        for queue in self._subscribers.get(persisted.plan_id, set()):
            # ponytail: queues are unbounded; add backpressure when clients can lag.
            queue.put_nowait(persisted)
        return persisted

    async def events(self, plan_id: str) -> AsyncGenerator[Event, None]:
        """Yield future persisted events for one plan."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(plan_id, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers = self._subscribers[plan_id]
            subscribers.remove(queue)
            if not subscribers:
                del self._subscribers[plan_id]

    async def create_plan(
        self,
        brief: str,
        *,
        planner: object | None = None,
        planner_assignment: Assignment | None = None,
        plan_id: str | None = None,
    ) -> Plan:
        """Run one planner call and persist its validated one-node proposal."""
        if not brief.strip():
            raise ValueError("plan brief cannot be empty")
        selected_plan_id = plan_id or f"plan_{uuid4().hex}"
        assignment = planner_assignment or Assignment(harness="pi", model="default")
        at = datetime.now(UTC)
        _ = self.append(
            PlanCreated(
                plan_id=selected_plan_id,
                at=at,
                brief=brief,
                planner=assignment,
            )
        )
        runner = planner or PiFrontierPlanner(model=assignment.model)
        result = await _planner_call(runner, brief)
        proposal = proposal_from_result(
            result,
            plan_id=selected_plan_id,
            at=datetime.now(UTC),
        )
        _ = self.append(proposal)
        return self.store.load(selected_plan_id)

    def approve_plan(self, plan_id: str, version: int | None = None) -> Plan:
        """Persist explicit approval; approval is required by ``run_initiative``."""
        plan = self.store.load(plan_id)
        selected_version = plan.version if version is None else version
        _ = self.append(
            PlanApproved(plan_id=plan_id, at=datetime.now(UTC), version=selected_version)
        )
        return self.store.load(plan_id)

    async def run_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        runtime: Runtime | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        by: str = "daemon",
        origin: Literal["run", "retry"] = "run",
    ) -> Checkpoint | None:
        """Run one approved frontier node and record, but never settle, it."""
        if timeout <= 0:
            raise ValueError("run timeout must be positive")
        plan = self.store.load(plan_id)
        if plan.approval != "approved":
            raise PermissionError("plan must be approved before running an initiative")
        initiative = self._admit_attempt(plan, initiative_id)
        selected_runtime = runtime or HerdrAdapter(project_root=self.project_root)
        selected_collector = collector or GitCheckpointCollector(
            checks=collect_checks(checks, initiative.spec),
            project_root=self.project_root,
        )
        attempt_id = f"attempt_{uuid4().hex}"
        packet = compile_task_packet(
            initiative.spec,
            _inputs(plan, initiative_id),
            brief=initiative.current_brief,
            assignment=initiative.current_assignment,
            leaves=plan.memory_leaves,
        )
        # Compiled before the reservation so a task reassigned off luna, or a
        # broken Luna mapping, fails the request instead of stranding an
        # attempt that could never run.
        command = executor_command(packet, project_root=self.project_root)
        inputs = [
            self.project_root / patch for patch in ancestor_patches(plan, initiative_id)
        ]
        # Reserve the attempt before anything is provisioned.  The fold refuses
        # a second attempt on a running initiative, so a concurrent run for the
        # same node is turned away here -- not after its agent is already live.
        # Nothing above awaits, so the admission check and this reservation are
        # one atomic step on the event loop: two racing runs cannot both pass.
        _ = self.append(
            AttemptStarted(
                plan_id=plan_id,
                at=datetime.now(UTC),
                attempt_id=attempt_id,
                initiative_id=initiative_id,
                assignment=initiative.current_assignment,
                brief_version=len(initiative.brief_versions) + 1,
                packet_tokens=estimate_tokens(packet.json()),
                by=by,
                origin=origin,
            )
        )
        self._attempt_commands[attempt_id] = command
        worktree_ref: str | None = None
        failed = False

        def fail(reason: str) -> None:
            nonlocal failed
            if failed:
                return
            failed = True
            current = self.store.load(plan_id).initiatives[initiative_id]
            if current.state != "failed":
                _ = self.append(
                    InitiativeFailed(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        initiative_id=initiative_id,
                        reason=reason[:2000],
                    )
                )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        def remaining() -> float:
            budget = deadline - loop.time()
            if budget <= 0:
                raise TimeoutError("initiative run timed out")
            return budget

        try:
            async with asyncio.timeout_at(deadline):
                worktree_ref = await selected_runtime.create_worktree(
                    f"herdsman/{plan_id}/{initiative_id}/{attempt_id}"
                )
                # Persisted the moment it exists.  Anything that fails below
                # would otherwise leave a worktree that `discard` cannot reach,
                # because the reference lived only in this local variable.
                try:
                    _ = self.append(
                        AttemptProvisioned(
                            plan_id=plan_id,
                            at=datetime.now(UTC),
                            attempt_id=attempt_id,
                            worktree_ref=worktree_ref,
                        )
                    )
                except Exception:
                    # The worktree exists but its reference was never persisted,
                    # so nothing could ever reach it; compensate by removing it,
                    # then let the store error surface.  A failed removal must
                    # not mask the store error that caused this.
                    with suppress(Exception):
                        await selected_runtime.remove_worktree(worktree_ref)
                    raise
                path = await selected_runtime.worktree_path(worktree_ref)
                base_sha = cast(
                    str,
                    await _collector_call(
                        selected_collector.capture_base,
                        path,
                        inputs=inputs,
                        timeout=remaining(),
                    ),
                )
                pane_ref = await selected_runtime.run(
                    worktree_ref,
                    command,
                    match=CHECKPOINT_PATTERN,
                )
                _ = self.append(
                    # The adapter owns the opaque refs; neither is interpreted here.
                    AttemptProvisioned(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        attempt_id=attempt_id,
                        worktree_ref=worktree_ref,
                        pane_ref=pane_ref,
                    )
                )
                completion: Completion | None = None
                async for event in selected_runtime.observe_events(
                    plan_id, attempt_id, pane_ref
                ):
                    if event.plan_id != plan_id or event.attempt_id != attempt_id:
                        raise RuntimeError("runtime event crossed attempt boundary")
                    _ = self.append(event)
                    evidence = completion_from_detail(event.detail)
                    if evidence is not None:
                        completion = evidence
                if completion is None:
                    raise CompletionError(
                        "runtime ended without a HERDSMAN_CHECKPOINT marker"
                    )
                checkpoint = cast(
                    Checkpoint,
                    await _collector_call(
                        selected_collector.collect,
                        path,
                        attempt_id,
                        completion,
                        base_sha=base_sha,
                        timeout=remaining(),
                    ),
                )
                checkpoint = _verify_proposed(plan, initiative_id, checkpoint, path)
                _ = self.append(
                    CheckpointRecorded(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        checkpoint=checkpoint,
                    )
                )
                return checkpoint
        except asyncio.CancelledError:
            fail("initiative run cancelled")
            raise
        except TimeoutError as exc:
            fail("initiative run timed out")
            raise RuntimeError("initiative run timed out") from exc
        except Exception as exc:
            fail(str(exc))
            raise
        finally:
            # `run` parks a subscription before launching; if observation never
            # started, nothing else would close it.
            await asyncio.shield(selected_runtime.aclose())

    async def run_plan(
        self,
        plan_id: str,
        *,
        max_concurrent: int | None = None,
        runtime_factory: Callable[[], Runtime] | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
    ) -> Plan:
        """Run an approved plan to a standstill, respecting the DAG.

        Every ready initiative starts concurrently, up to the plan's own
        maximum concurrency, minus any that would write where a running one
        writes.  A clean checkpoint settles its initiative, which is what makes
        the downstream node ready; anything else fails and stops that branch.
        Returns when nothing is running and nothing more can start.

        Each initiative gets its own runtime: a herdr adapter holds per-pane
        subscriptions and closes all of them at once, so one shared across
        concurrent initiatives would cut the first finisher's siblings loose.
        """
        if timeout <= 0:
            raise ValueError("run timeout must be positive")
        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        if self.store.load(plan_id).approval != "approved":
            raise PermissionError("plan must be approved before running it")
        running: dict[asyncio.Task[Checkpoint | None], str] = {}
        stalled: set[str] = set()
        """Initiatives whose run failed without reserving an attempt.

        Such a run left the initiative `pending` and therefore still ready, so
        rescheduling it would spin forever on a fault that is not going to
        change -- a rejected timeout, or a refused admission.
        """
        try:
            while True:
                plan = self.store.load(plan_id)
                limit = (
                    max_concurrency(plan) if max_concurrent is None else max_concurrent
                )
                for initiative_id in plan.ready():
                    active = set(running.values())
                    if len(running) >= limit:
                        break
                    if initiative_id in active or initiative_id in stalled:
                        continue
                    if conflicts_with(plan, initiative_id, active):
                        continue  # serialized: it writes where a running one writes
                    task = asyncio.create_task(
                        self.run_and_settle(
                            plan_id,
                            initiative_id,
                            runtime=runtime_factory() if runtime_factory else None,
                            collector=collector,
                            checks=checks,
                            timeout=timeout,
                        )
                    )
                    running[task] = initiative_id
                if not running:
                    return self.store.load(plan_id)
                done, _ = await asyncio.wait(
                    set(running), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    initiative_id = running.pop(task)
                    # A failed initiative is already an `initiative_failed`
                    # event; the plan carries on with whatever else can run.
                    if (
                        task.exception() is not None
                        and self.store.load(plan_id).initiatives[initiative_id].state
                        == "pending"
                    ):
                        stalled.add(initiative_id)
        except BaseException:
            for task in running:
                _ = task.cancel()
            if running:
                _ = await asyncio.gather(*running, return_exceptions=True)
            raise

    async def run_and_settle(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        runtime: Runtime | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        by: str = "daemon",
        origin: Literal["run", "retry"] = "run",
    ) -> Checkpoint | None:
        """Run one initiative and apply the settlement policy to its evidence.

        Every user-facing run path goes through here -- the plan scheduler and
        the single-initiative API alike -- so identical evidence settles
        identically no matter which one produced it. `run_initiative` stays the
        primitive that records without judging.

        Sprint 3 gate: a contract that declares `approval="required"` never
        settles here. Its checkpoint is recorded and left for review, so its
        dependents stay blocked until `approve_checkpoint` settles it. Under
        the automatic policy a contract violation fails the initiative with a
        typed `ContractError`; the evidence stays recorded for review.
        """
        checkpoint = await self.run_initiative(
            plan_id,
            initiative_id,
            runtime=runtime,
            collector=collector,
            checks=checks,
            timeout=timeout,
            by=by,
            origin=origin,
        )
        if checkpoint is None:
            return None
        plan = self.store.load(plan_id)
        if plan.initiatives[initiative_id].spec.approval == "required":
            # The recorded evidence awaits review; contract enforcement joins
            # at settlement, so approval of invalid evidence cannot release
            # the node (and the reviewer sees the violations in the report).
            return checkpoint
        failures = [check.name for check in checkpoint.checks if not check.passed]
        if checkpoint.exit_code == 0 and not failures:
            try:
                _ = self.settle_initiative(plan_id, initiative_id, checkpoint.id)
            except ContractError as exc:
                _ = self.append(
                    InitiativeFailed(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        initiative_id=initiative_id,
                        reason=str(exc)[:2000],
                    )
                )
                raise
            return checkpoint
        # Not a gate -- gates are Sprint 3.  Dirty evidence simply does not
        # advance the DAG, and the operator can still settle it by hand.
        reason = (
            f"checkpoint {checkpoint.id} exited {checkpoint.exit_code}"
            if checkpoint.exit_code != 0
            else f"checkpoint {checkpoint.id} failed checks: {', '.join(failures)}"
        )
        _ = self.append(
            InitiativeFailed(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                reason=reason[:2000],
            )
        )
        return checkpoint

    def graph(self, plan_id: str) -> PlanGraph:
        """The stable running-graph projection the UI and CLI read."""
        return plan_graph(self.store.load(plan_id))

    def risk(self, plan_id: str) -> RiskReport:
        """The plan gate's structural risk report for the current version."""
        return risk_report(
            self.store.load(plan_id),
            tiers=resolve_model_tiers(self.project_root),
        )

    def overhead(self, plan_id: str) -> Overhead:
        """Orchestration tokens over productive tokens, against the 20% target."""
        return overhead(self.store.load(plan_id))

    def record_checkpoint(self, plan_id: str, checkpoint: Checkpoint) -> Plan:
        """Append mechanical evidence without changing settlement state.

        Recording against an attempt whose checkpoint was rejected or had
        changes requested records a revision: a new version, with the old one
        preserved for audit. The fold refuses anything else.

        Evidence is recorded as supplied; the contract gate fires at settlement
        (`_settle`), where acceptance is decided.
        """
        if checkpoint.usage is None:
            raise ValueError("checkpoint usage is required for an attempt")
        _ = self.append(
            CheckpointRecorded(
                plan_id=plan_id,
                at=datetime.now(UTC),
                checkpoint=checkpoint,
            )
        )
        return self.store.load(plan_id)

    def settle_initiative(
        self, plan_id: str, initiative_id: str, checkpoint_id: str
    ) -> Plan:
        """Explicitly settle only against a checkpoint belonging to the node."""
        return self._settle(plan_id, initiative_id, checkpoint_id)

    def _settle(self, plan_id: str, initiative_id: str, checkpoint_id: str) -> Plan:
        """The one settlement path, gated by the fold's own contract check.

        Every route to completion acceptance -- automatic settlement in
        `run_and_settle`, the operator's explicit settle, and the settle that
        follows an approval -- appends `InitiativeSettled` through here, and
        the event fold refuses to apply it when the checkpoint fails its
        contract (typed `ContractError`) or the approval policy, so nothing
        is written. Identical evidence is accepted identically everywhere.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if initiative.state not in {"running", "failed"}:
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; "
                + "only a running or failed initiative can be settled"
            )
        if not any(
            attempt.checkpoint is not None and attempt.checkpoint.id == checkpoint_id
            for attempt in initiative.attempts
        ):
            raise ValueError("initiative has no matching recorded checkpoint")
        _ = self.append(
            InitiativeSettled(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                checkpoint_id=checkpoint_id,
            )
        )
        return self.store.load(plan_id)

    def approve_checkpoint(
        self,
        plan_id: str,
        checkpoint_id: str,
        *,
        by: str = "operator",
        reason: str = "",
    ) -> Plan:
        """Approve one checkpoint version; a finished gated node settles with it.

        Approval is what releases the gated node's dependents: if the initiative
        has finished its run and this is still its current version, approval
        settles it in the same action, so a consumer is ready exactly when the
        producer checkpoint it must build on is approved.

        The named checkpoint's contract is validated first: invalid evidence
        raises a typed `ContractError` before anything is appended, so the
        decision stays pending and no approval event persists for it.
        """
        plan = self.store.load(plan_id)
        initiative = _checkpoint_initiative(plan, checkpoint_id)
        if initiative.spec.contract is not None:
            checkpoint = next(
                version
                for version in initiative.checkpoint_versions
                if version.id == checkpoint_id
            )
            violations = validate_checkpoint(
                initiative.spec, checkpoint, initiative.spec.contract
            )
            if violations:
                raise ContractError(
                    summarize_violations(violations), violations=violations
                )
        _ = self.append(
            CheckpointApproved(
                plan_id=plan_id,
                at=datetime.now(UTC),
                checkpoint_id=checkpoint_id,
                by=by,
                reason=reason,
            )
        )
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[initiative.spec.id]
        latest = initiative.latest_checkpoint
        if (
            initiative.state in {"running", "failed"}
            and latest is not None
            and latest.id == checkpoint_id
        ):
            _ = self._settle(plan_id, initiative.spec.id, checkpoint_id)
        return self.store.load(plan_id)

    def reject_checkpoint(
        self,
        plan_id: str,
        checkpoint_id: str,
        *,
        by: str = "operator",
        reason: str = "",
    ) -> Plan:
        """Reject one checkpoint version; released consumers become attention.

        The fold refuses settlement on rejected evidence and blocks further
        readiness, while the version stays in the projection for audit and the
        initiative can record a revised checkpoint.
        """
        _ = self.append(
            CheckpointRejected(
                plan_id=plan_id,
                at=datetime.now(UTC),
                checkpoint_id=checkpoint_id,
                by=by,
                reason=reason,
            )
        )
        return self.store.load(plan_id)

    def request_changes(
        self,
        plan_id: str,
        checkpoint_id: str,
        *,
        by: str = "operator",
        reason: str = "",
    ) -> Plan:
        """Ask for a revision: neither approved nor rejected, still blocking."""
        _ = self.append(
            CheckpointChangesRequested(
                plan_id=plan_id,
                at=datetime.now(UTC),
                checkpoint_id=checkpoint_id,
                by=by,
                reason=reason,
            )
        )
        return self.store.load(plan_id)

    def checkpoint_report(self, plan_id: str) -> CheckpointReport:
        """The readable review surface: versions, decisions, and attention."""
        plan = self.store.load(plan_id)
        return CheckpointReport(
            plan_id=plan_id,
            initiatives=[
                _review_view(initiative) for initiative in plan.initiatives.values()
            ],
            attention=plan.attention(),
        )

    async def discard_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        attempt_id: str,
        *,
        runtime: Runtime | None = None,
    ) -> Plan:
        """Remove one retained attempt worktree through the runtime adapter.

        Worktrees are deliberately preserved by ``run_initiative`` for review
        and repair evidence.  Discard is the explicit lifecycle action that
        releases that herdr-owned workspace; it does not alter the event
        projection or settle an initiative.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if initiative.state not in {"failed", "settled"}:
            raise ValueError(
                f"cannot discard attempt {attempt_id} while initiative "
                + f"{initiative_id} is {initiative.state}; it must be failed or settled"
            )
        attempt = next(
            (candidate for candidate in initiative.attempts if candidate.id == attempt_id),
            None,
        )
        if attempt is None:
            raise ValueError(
                f"attempt {attempt_id} does not belong to initiative {initiative_id}"
            )
        if attempt.worktree_ref is None:
            raise ValueError(f"attempt {attempt_id} has no worktree to discard")
        selected_runtime = runtime or HerdrAdapter(project_root=self.project_root)
        await selected_runtime.remove_worktree(attempt.worktree_ref)
        return self.store.load(plan_id)

    # --- Sprint 4 interventions -------------------------------------------------

    async def retry_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        runtime: Runtime | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        by: str = "operator",
    ) -> Checkpoint | None:
        """Retry a failed initiative: a new attempt on its current brief.

        A retry is not a process restart: it compiles a fresh packet from the
        task's current brief version, assignment, and memory leaves, opens a
        fresh worktree, and reserves a new attempt; the failed attempt and its
        evidence stay in the history. The new attempt event names `by` and is
        marked `origin="retry"`, so persisted attempt state stays attributable
        and distinguishable from an ordinary run. Settlement follows the one
        policy in `run_and_settle`.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if initiative.state != "failed":
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; "
                + "retry retries a failed initiative"
            )
        return await self.run_and_settle(
            plan_id,
            initiative_id,
            runtime=runtime,
            collector=collector,
            checks=checks,
            timeout=timeout,
            by=by,
            origin="retry",
        )

    def redirect_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        brief: str = "",
        *,
        checkpoint_id: str | None = None,
        by: str = "operator",
        reason: str = "",
    ) -> Plan:
        """Point a task at a new brief version or an existing checkpoint.

        Exactly one target: a replacement brief, or a checkpoint version in
        the plan whose deterministic derived brief the next attempt continues
        from. The running attempt keeps its snapshot, so nothing live is
        disturbed, and the fold records a run-scoped redirect leaf whose
        claim is the new brief, so later packets carry the correction. What
        downstream work this disturbs is previewed by `impact`.
        """
        _ = self.append(
            TaskRedirected(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                brief=brief,
                checkpoint_id=checkpoint_id,
                by=by,
                reason=reason,
            )
        )
        return self.store.load(plan_id)

    def reassign_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        assignment: Assignment,
        *,
        by: str = "operator",
        reason: str = "",
    ) -> Plan:
        """Give a task a different harness/model, keeping attempt history.

        The override applies to the next attempt only: a running attempt
        finishes on its own snapshot and past attempts keep theirs. The fold
        refuses a reassignment onto the current assignment.
        """
        _ = self.append(
            TaskReassigned(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                assignment=assignment,
                by=by,
                reason=reason,
            )
        )
        return self.store.load(plan_id)

    async def nudge_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        text: str,
        *,
        by: str = "operator",
        ground_truth: bool = False,
        runtime: PaneRuntime | None = None,
    ) -> Plan:
        """Send free-text guidance to a task's live pane.

        The fold validates the nudge against the live attempt, and when it is
        flagged `ground_truth` records the correction as a run-scoped leaf
        that later packets carry. The pane delivery precedes the event, so a
        refused delivery leaves no record.
        """
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            attempt, pane = self._live_attempt(plan_id, initiative_id)
            # Delivery precedes the record: replay must never claim an
            # intervention the live agent did not receive.
            await adapter.nudge_pane(pane, text)
            _ = self.append(
                TaskNudged(
                    plan_id=plan_id,
                    at=datetime.now(UTC),
                    initiative_id=initiative_id,
                    attempt_id=attempt.id,
                    text=text,
                    by=by,
                    ground_truth=ground_truth,
                )
            )
        finally:
            await asyncio.shield(adapter.aclose())
        return self.store.load(plan_id)

    async def operator_answer(
        self,
        plan_id: str,
        attempt_id: str,
        subject: str,
        answer: str,
        *,
        by: str = "operator",
        runtime: PaneRuntime | None = None,
    ) -> Plan:
        """Answer an agent's live block/decision request; the answer is truth.

        One event is the whole ceremony: it is the audit record, and the fold
        projects the run-scoped leaf that later packets carry and that makes
        repeat requests on the same subject auto-answerable.
        """
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            _attempt, pane = self._pane_attempt(plan_id, attempt_id)
            # Delivery precedes the record: replay must never claim an answer
            # the live agent did not receive.
            await adapter.nudge_pane(pane, _pane_answer(subject, answer))
            _ = self.append(
                OperatorAnswered(
                    plan_id=plan_id,
                    at=datetime.now(UTC),
                    attempt_id=attempt_id,
                    subject=subject,
                    answer=answer,
                    by=by,
                )
            )
        finally:
            await asyncio.shield(adapter.aclose())
        return self.store.load(plan_id)

    async def auto_answer(
        self,
        plan_id: str,
        attempt_id: str,
        subject: str,
        *,
        runtime: PaneRuntime | None = None,
    ) -> MemoryLeaf | None:
        """Answer a repeat request mechanically from an active leaf.

        A request whose subject matches a run-scoped leaf is answered with the
        leaf's claim through the daemon's one template -- no operator turn, no
        model call. Returns the leaf used, or None when no leaf matches and
        the request must go to the operator (`operator_answer`). The target
        attempt is validated before any leaf lookup: a repeat request routed
        to a historical attempt is a refusal, not an auto-answer. The
        delivery is folded as an attributable nudge citing the leaf;
        `ground_truth` stays False because the leaf itself already carries
        the ground truth.
        """
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            attempt, pane = self._pane_attempt(plan_id, attempt_id)
            leaf = next(
                (
                    candidate
                    for candidate in reversed(self.store.load(plan_id).memory_leaves)
                    if candidate.subject.strip().casefold()
                    == subject.strip().casefold()
                ),
                None,
            )
            if leaf is None:
                return None
            text = _pane_answer(leaf.subject, leaf.claim)
            # Delivery precedes the record, as for every pane intervention.
            await adapter.nudge_pane(pane, text)
            _ = self.append(
                TaskNudged(
                    plan_id=plan_id,
                    at=datetime.now(UTC),
                    initiative_id=attempt.initiative_id,
                    attempt_id=attempt_id,
                    text=text,
                    by=f"daemon:{leaf.id}",
                    ground_truth=False,
                )
            )
        finally:
            await asyncio.shield(adapter.aclose())
        return leaf

    async def restart_process(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        by: str = "operator",
        runtime: PaneRuntime | None = None,
    ) -> str:
        """Re-issue the live attempt's command in place. Not a retry.

        Restart is the recovery action for a hung or crashed executor: the
        same packet, the same worktree, the same attempt. The adapter
        interrupts the foreground process first, so the re-issued command
        reaches a fresh prompt instead of the hung process. One attributable
        `process_restarted` event is appended only after the pane took the
        restart.
        """
        attempt, pane = self._live_attempt(plan_id, initiative_id)
        command = self._attempt_commands.get(attempt.id)
        if command is None:
            raise ValueError(
                f"attempt {attempt.id} has no recorded command to restart"
            )
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            pane_ref = await adapter.restart_process(pane, command)
        finally:
            await asyncio.shield(adapter.aclose())
        _ = self.append(
            ProcessRestarted(
                plan_id=plan_id,
                at=datetime.now(UTC),
                attempt_id=attempt.id,
                by=by,
            )
        )
        return pane_ref

    async def focus_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        runtime: PaneRuntime | None = None,
    ) -> str:
        """Focus the herdr pane running a task, from the task reference.

        Focus is a read-only convenience with no event and no ground truth,
        so any task whose latest attempt has a pane qualifies, settled or
        failed alike — unlike the event-producing pane actions.
        """
        initiative = self.store.load(plan_id).initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if not initiative.attempts or initiative.attempts[-1].pane_ref is None:
            raise ValueError(f"initiative {initiative_id} has no pane to focus")
        pane = initiative.attempts[-1].pane_ref
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            await adapter.focus_pane(pane)
        finally:
            await asyncio.shield(adapter.aclose())
        return pane

    def impact(self, plan_id: str, initiative_id: str) -> DownstreamImpact:
        """What a disruptive action on a task would disturb, before mutating."""
        return downstream_impact(self.store.load(plan_id), initiative_id)

    def _admit_attempt(self, plan: Plan, initiative_id: str) -> Initiative:
        """The one admission rule for starting an attempt, run or retry alike.

        A failed initiative is retryable as a new attempt on its current brief
        version and assignment; the fold still refuses a second live attempt,
        so the reservation below serializes concurrent callers.
        """
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if initiative.state == "pending":
            if initiative_id not in plan.ready():
                raise ValueError(f"initiative {initiative_id} is not ready")
        elif initiative.state == "failed":
            if not plan.dependencies_released(initiative):
                raise ValueError(
                    f"initiative {initiative_id} cannot start: a dependency's "
                    + "checkpoint is not currently approved"
                )
        else:
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; only a "
                + "pending or failed initiative can start an attempt"
            )
        contended = _contending_writers(plan, initiative_id)
        if contended:
            raise ValueError(
                f"initiative {initiative_id} writes where running "
                + f"{', '.join(sorted(contended))} writes; it cannot start yet"
            )
        return initiative

    def _live_attempt(self, plan_id: str, initiative_id: str) -> tuple[Attempt, str]:
        """A task's live attempt and its pane, which event-producing actions need.

        The checks mirror the fold's guards, so a refusal happens before any
        pane bytes are sent rather than after a message is delivered.
        """
        initiative = self.store.load(plan_id).initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        if initiative.state != "running":
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; "
                + "only a running task has a live pane"
            )
        if not initiative.attempts:
            raise ValueError(f"initiative {initiative_id} has no live attempt")
        attempt = initiative.attempts[-1]
        if attempt.pane_ref is None:
            raise ValueError(f"attempt {attempt.id} has no pane to message")
        return attempt, attempt.pane_ref

    def _pane_attempt(self, plan_id: str, attempt_id: str) -> tuple[Attempt, str]:
        """The named attempt's live pane, for messaging the agent.

        Only a running initiative's latest attempt has an agent listening: a
        historical attempt would target its old pane, so answering or
        auto-answering one must never persist as current ground truth.
        """
        for initiative in self.store.load(plan_id).initiatives.values():
            if not initiative.attempts or initiative.attempts[-1].id != attempt_id:
                continue
            attempt = initiative.attempts[-1]
            if initiative.state != "running":
                raise ValueError(
                    f"initiative {initiative.spec.id} is {initiative.state}; "
                    + "only a running task can be messaged"
                )
            if attempt.pane_ref is None:
                raise ValueError(f"attempt {attempt_id} has no pane to message")
            return attempt, attempt.pane_ref
        raise ValueError(f"unknown or superseded attempt {attempt_id}")


def _pane_answer(subject: str, text: str) -> str:
    """The one deterministic message an answer takes to the pane."""
    return f"[{subject}] {text}"


def _checkpoint_initiative(plan: Plan, checkpoint_id: str) -> Initiative:
    """The initiative a review action targets; checkpoint ids are plan-unique."""
    for initiative in plan.initiatives.values():
        if any(version.id == checkpoint_id for version in initiative.checkpoint_versions):
            return initiative
    raise ValueError(f"unknown checkpoint {checkpoint_id}")


def collect_checks(base: Sequence[str], spec: InitiativeSpec) -> tuple[str, ...]:
    """Shell checks a run executes: the caller's checks plus the contract's.

    A required check that never runs would fail every settlement with
    `missing-check`, so declared shell checks are executed alongside the
    caller's own. The in-process `verify-proposed` check is excluded: the
    daemon computes it from the attempt worktree, not the shell.
    """
    contract = spec.contract
    required = (
        []
        if contract is None
        else [check for check in contract.required_checks if check != VERIFY_CHECK]
    )
    return tuple(dict.fromkeys([*base, *required]))


def _verify_proposed(
    plan: Plan, initiative_id: str, checkpoint: Checkpoint, worktree: Path
) -> Checkpoint:
    """Run the proposed-code verifier as an in-process contract check.

    A contract that requires `verify-proposed` gets its verdict computed from
    the attempt worktree's composed Python files -- the tree the checkpoint
    would hand to consumers, so partial diffs verify in their real context.
    The verdict is appended to the manifest like any shell check: BLOCK fails
    it and the contract gate refuses settlement; PASS/WARN pass, with WARN
    carried in the summary as reviewer attention. Evidence the collector
    already verified is kept as-is.
    """
    contract = plan.initiatives[initiative_id].spec.contract
    if contract is None or VERIFY_CHECK not in contract.required_checks:
        return checkpoint
    if any(check.name == VERIFY_CHECK for check in checkpoint.checks):
        return checkpoint
    result = _verify_files(worktree, checkpoint.changed_paths)
    return checkpoint.model_copy(update={"checks": [*checkpoint.checks, result]})


def _verify_files(worktree: Path, changed_paths: Sequence[str]) -> CheckResult:
    """Verify every composed Python file the attempt changed, worst verdict wins."""
    python_paths = sorted({path for path in changed_paths if path.endswith(".py")})
    if not python_paths:
        return CheckResult(
            name=VERIFY_CHECK, passed=True, summary="no python changes to verify"
        )
    verifier = Verifier(worktree)
    verdict = "PASS"
    problems: list[str] = []
    for relative in python_paths:
        source_file = worktree / relative
        if not source_file.is_file():
            continue  # a deletion has no proposed code to verify
        try:
            source = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        report = verifier.verify(source, target=relative)
        if report.verdict == "BLOCK":
            verdict = "BLOCK"
        elif report.verdict == "WARN" and verdict != "BLOCK":
            verdict = "WARN"
        for ref in report.references:
            if ref.status == "phantom":
                repair = next(
                    (item for item in report.repairs if item.phantom == ref.name), None
                )
                suffix = (
                    f" (did you mean {', '.join(repair.suggestions)}?)"
                    if repair is not None and repair.suggestions
                    else ""
                )
                problems.append(f"{relative}: {ref.name} is not defined{suffix}")
            elif ref.status == "unknown":
                problems.append(f"{relative}: {ref.name} could not be verified")
        blast = report.blast_radius
        if blast is not None and blast.affected_paths:
            problems.append(
                f"{relative}: modifies symbols {', '.join(blast.modified) or '(none)'} "
                + f"used by {len(blast.affected_paths)} dependent module(s)"
            )
    if problems:
        summary = f"{verdict}: " + "; ".join(problems)
    else:
        summary = f"{verdict}: {len(python_paths)} python file(s) verified"
    return CheckResult(name=VERIFY_CHECK, passed=verdict != "BLOCK", summary=summary[:1000])


def _contending_writers(plan: Plan, initiative_id: str) -> set[str]:
    """Running initiatives whose write scope overlaps this one's.

    Derived from the plan projection rather than from any one scheduler's
    bookkeeping, so a direct run and a plan run are admitted under the same
    rule.  A local `running` set would only serialize initiatives that one
    `run_plan` call happened to launch.
    """
    running = {
        candidate
        for candidate, initiative in plan.initiatives.items()
        if initiative.state == "running" and candidate != initiative_id
    }
    if not running or not conflicts_with(plan, initiative_id, running):
        return set()
    return {
        peer
        for found in contention(plan)
        if found.kind == "write_write" and initiative_id in found.initiatives
        for peer in set(found.initiatives) - {initiative_id}
        if peer in running
    }


def _inputs(plan: Plan, initiative_id: str) -> list[ArtifactRef]:
    """The settled checkpoints this initiative depends on, by reference.

    Physical artifacts cross the edge: a checkpoint id, its commit, and the
    paths it touched.  No prose summary is generated, and nothing about
    sibling initiatives is included.
    """
    refs: list[ArtifactRef] = []
    for dependency in plan.initiatives[initiative_id].spec.depends_on:
        upstream = plan.initiatives.get(dependency)
        if upstream is None:
            continue
        checkpoint = next(
            (
                attempt.checkpoint
                for attempt in reversed(upstream.attempts)
                if attempt.checkpoint is not None
            ),
            None,
        )
        if checkpoint is None:
            continue
        refs.append(
            ArtifactRef(
                initiative_id=dependency,
                checkpoint_id=checkpoint.id,
                head_sha=checkpoint.head_sha,
                changed_paths=list(checkpoint.changed_paths),
                patch_path=checkpoint.patch_path,
            )
        )
    return refs


async def _collector_call(
    method: Callable[..., object],
    *args: object,
    timeout: float,
    **kwargs: object,
) -> object:
    """Run bounded mechanical collection without blocking the event loop.

    The collector shells out to git and to the configured checks, which take
    as long as the checks take.  On the loop thread that would stall every
    other request, every SSE subscriber, and `asyncio.timeout_at`, which can
    only fire when the loop regains control.
    """
    kwargs["timeout"] = timeout
    return await asyncio.to_thread(method, *args, **kwargs)


async def _planner_call(planner: object, brief: str) -> object:
    method = getattr(planner, "propose", None)
    if callable(method):
        value = cast(Callable[[str], object], method)(brief)
    elif callable(planner):
        value = cast(Callable[[str], object], planner)(brief)
    else:
        raise PlannerError("planner must provide propose(brief)")
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value


class CreateRequest(BaseModel):
    brief: str


class ReviewRequest(BaseModel):
    """Who acted and why; the reason is what makes a verdict auditable."""

    by: str = "operator"
    reason: str = ""


class CheckpointVersionView(BaseModel):
    """One preserved checkpoint version and its derived review state."""

    version: int
    checkpoint_id: str
    attempt_id: str
    decision: str
    decided_at: AwareDatetime | None = None
    decided_by: str = ""
    reason: str = ""
    approved_at: AwareDatetime | None = None
    """When this version was approved, if it ever was; survives a rejection."""
    superseded: bool
    exit_code: int | None = None
    failed_checks: list[str] = []
    failed_check_summaries: dict[str, str] = {}
    """Why each failed check failed -- e.g. the verify verdict with repairs."""
    changed_paths: list[str] = []
    patch_path: str | None = None
    walkthrough: walkthrough.Walkthrough
    """Changed paths grouped into logical cohorts, for checkpoint review."""


class InitiativeReviewView(BaseModel):
    """The review lifecycle of one initiative's checkpoints."""

    initiative_id: str
    name: str
    policy: str
    state: str
    awaiting_review: bool
    approved_version: int | None = None
    """The latest version that was ever approved: the diff base.

    It survives a later rejection — released consumers built on that
    approval, so `changes_since_approved` stays comparable to it.
    """
    approved_checkpoint_id: str | None = None
    changes_since_approved: list[str] = []
    violations: list[str] = []
    """Typed contract failures of the latest version, empty when it is acceptable."""
    versions: list[CheckpointVersionView] = []


class CheckpointReport(BaseModel):
    """The plan's checkpoint review surface, deterministic and event-folded."""

    plan_id: str
    initiatives: list[InitiativeReviewView] = []
    attention: list[Taint] = []


class RunRequest(BaseModel):
    timeout: float = 600.0


class RetryRequest(RunRequest):
    """A retry; `preview` returns the downstream impact without mutating.

    `by` names the retrying actor on the new attempt's event and state; the
    daemon stays the actor of ordinary runs.
    """

    by: str = "operator"
    preview: bool = False


class RunPlanRequest(BaseModel):
    timeout: float = 600.0
    max_concurrent: int | None = None


class RedirectRequest(BaseModel):
    """A new brief version or a checkpoint to continue from; exactly one of
    `brief` and `checkpoint_id` is set. `preview` returns the downstream
    impact without mutating."""

    brief: str = ""
    checkpoint_id: str | None = None
    by: str = "operator"
    reason: str = ""
    preview: bool = False


class ReassignRequest(BaseModel):
    """A next-attempt harness/model override; empty halves are rejected.
    `preview` returns the downstream impact without mutating."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    by: str = "operator"
    reason: str = ""
    preview: bool = False


class NudgeRequest(BaseModel):
    """Free-text guidance for the live attempt."""

    text: str = Field(min_length=1)
    by: str = "operator"
    ground_truth: bool = False


class AnswerRequest(BaseModel):
    """One operator answer to an agent block/decision request."""

    subject: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    by: str = "operator"


class AutoAnswerRequest(BaseModel):
    """A repeat request the daemon answers mechanically from a leaf."""

    subject: str = Field(min_length=1)


class PaneResponse(BaseModel):
    """The herdr pane an initiative-scoped pane action targeted."""

    pane_ref: str


class RestartRequest(BaseModel):
    """Actor attribution for a process restart."""

    by: str = "operator"


class AutoAnswerResponse(BaseModel):
    """The leaf that answered mechanically; null routes to the operator."""

    leaf: MemoryLeaf | None = None


class RunResponse(BaseModel):
    checkpoint: Checkpoint | None


def _review_view(initiative: Initiative) -> InitiativeReviewView:
    versions = [
        _version_view(initiative, version, number)
        for number, version in enumerate(initiative.checkpoint_versions, start=1)
    ]
    latest = initiative.latest_checkpoint
    violations: list[ContractViolation] = []
    if initiative.spec.contract is not None and latest is not None:
        violations = validate_checkpoint(initiative.spec, latest, initiative.spec.contract)
    approved_position = next(
        (
            position
            for position in range(len(versions), 0, -1)
            if versions[position - 1].approved_at is not None
        ),
        None,
    )
    approved = (
        initiative.checkpoint_versions[approved_position - 1]
        if approved_position is not None
        else None
    )
    latest_decision = (
        initiative.checkpoint_decisions.get(latest.id) if latest is not None else None
    )
    return InitiativeReviewView(
        initiative_id=initiative.spec.id,
        name=initiative.spec.name,
        policy=initiative.spec.approval,
        state=initiative.state,
        awaiting_review=(
            initiative.spec.approval == "required"
            and latest is not None
            and latest_decision is not None
            and latest_decision.state == "pending"
        ),
        approved_version=approved_position,
        approved_checkpoint_id=approved.id if approved is not None else None,
        changes_since_approved=(
            sorted(set(latest.changed_paths) - set(approved.changed_paths))
            if approved is not None and latest is not None
            else []
        ),
        violations=[
            f"{violation.code}: {violation.message}" for violation in violations
        ],
        versions=versions,
    )


def _version_view(
    initiative: Initiative, checkpoint: Checkpoint, version: int
) -> CheckpointVersionView:
    decision = initiative.checkpoint_decisions.get(checkpoint.id)
    return CheckpointVersionView(
        version=version,
        checkpoint_id=checkpoint.id,
        attempt_id=checkpoint.attempt_id,
        decision=decision.state if decision is not None else "pending",
        decided_at=decision.decided_at if decision is not None else None,
        decided_by=decision.decided_by if decision is not None else "",
        reason=decision.reason if decision is not None else "",
        approved_at=decision.approved_at if decision is not None else None,
        superseded=version < len(initiative.checkpoint_versions),
        exit_code=checkpoint.exit_code,
        failed_checks=[check.name for check in checkpoint.checks if not check.passed],
        failed_check_summaries={
            check.name: check.summary
            for check in checkpoint.checks
            if not check.passed
        },
        changed_paths=list(checkpoint.changed_paths),
        patch_path=checkpoint.patch_path,
        walkthrough=walkthrough.walkthrough(checkpoint.changed_paths),
    )


def sse(event: Event) -> str:
    """Encode one domain event as an SSE message."""
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def create_app(daemon: Daemon) -> FastAPI:
    """Build the daemon's local HTTP API."""
    app = FastAPI()

    async def stream_events(plan_id: str) -> StreamingResponse:
        if plan_id not in daemon.store.plans():
            raise HTTPException(status_code=404, detail="unknown plan")
        return StreamingResponse(
            (sse(event) async for event in daemon.events(plan_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    async def create(request: CreateRequest) -> dict[str, object]:
        try:
            plan = await daemon.create_plan(request.brief)
        except (PlannerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def get_plan(plan_id: str) -> Plan:
        try:
            return daemon.plan(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def approve(plan_id: str, version: int | None = None) -> dict[str, object]:
        try:
            plan = daemon.approve_plan(plan_id, version)
        except (ValueError, PermissionError) as exc:
            if plan_id not in daemon.store.plans():
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def run(
        plan_id: str, initiative_id: str, request: RunRequest | None = None
    ) -> RunResponse:
        try:
            checkpoint = await daemon.run_and_settle(
                plan_id,
                initiative_id,
                timeout=request.timeout if request is not None else 600.0,
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RunResponse(checkpoint=checkpoint)

    async def run_whole_plan(
        plan_id: str, request: RunPlanRequest | None = None
    ) -> PlanGraph:
        selected = request or RunPlanRequest()
        try:
            _ = await daemon.run_plan(
                plan_id,
                max_concurrent=selected.max_concurrent,
                timeout=selected.timeout,
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return daemon.graph(plan_id)

    async def graph(plan_id: str) -> PlanGraph:
        try:
            return daemon.graph(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def risk(plan_id: str) -> RiskReport:
        try:
            return daemon.risk(plan_id)
        except LunaConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def codemap() -> dict[str, object]:
        return nav.build_index(daemon.project_root).to_dict()

    async def tour() -> dict[str, str]:
        return {"text": nav.tour_text(nav.build_index(daemon.project_root))}

    async def flow(name: str) -> dict[str, str]:
        try:
            return {"text": nav.flow_text(nav.build_index(daemon.project_root), name)}
        except nav.NavError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def symbol(name: str) -> dict[str, str]:
        try:
            return {"text": nav.symbol_text(nav.build_index(daemon.project_root), name)}
        except nav.NavError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def settle(plan_id: str, initiative_id: str, checkpoint_id: str) -> dict[str, object]:
        try:
            plan = daemon.settle_initiative(plan_id, initiative_id, checkpoint_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def discard(
        plan_id: str, initiative_id: str, attempt_id: str
    ) -> dict[str, object]:
        try:
            plan = await daemon.discard_initiative(plan_id, initiative_id, attempt_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def checkpoints(plan_id: str) -> CheckpointReport:
        try:
            return daemon.checkpoint_report(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def plan_error(plan_id: str, exc: Exception) -> HTTPException:
        """Unknown plans are 404; every other domain refusal is 409."""
        if plan_id not in daemon.store.plans():
            return HTTPException(status_code=404, detail=str(exc))
        return HTTPException(status_code=409, detail=str(exc))

    async def retry(
        plan_id: str, initiative_id: str, request: RetryRequest | None = None
    ) -> RunResponse | dict[str, object]:
        selected = request or RetryRequest()
        try:
            if selected.preview:
                return {"impact": daemon.impact(plan_id, initiative_id).model_dump(mode="json")}
            checkpoint = await daemon.retry_initiative(
                plan_id, initiative_id, timeout=selected.timeout, by=selected.by
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise plan_error(plan_id, exc) from exc
        return RunResponse(checkpoint=checkpoint)

    async def redirect(
        plan_id: str, initiative_id: str, request: RedirectRequest
    ) -> dict[str, object]:
        try:
            if request.preview:
                return {
                    "impact": daemon.impact(
                        plan_id, initiative_id
                    ).model_dump(mode="json")
                }
            plan = daemon.redirect_initiative(
                plan_id,
                initiative_id,
                request.brief,
                checkpoint_id=request.checkpoint_id,
                by=request.by,
                reason=request.reason,
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def reassign(
        plan_id: str, initiative_id: str, request: ReassignRequest
    ) -> dict[str, object]:
        try:
            if request.preview:
                return {
                    "impact": daemon.impact(
                        plan_id, initiative_id
                    ).model_dump(mode="json")
                }
            plan = daemon.reassign_initiative(
                plan_id,
                initiative_id,
                Assignment(harness=request.harness, model=request.model),
                by=request.by,
                reason=request.reason,
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def nudge(
        plan_id: str, initiative_id: str, request: NudgeRequest
    ) -> dict[str, object]:
        try:
            plan = await daemon.nudge_initiative(
                plan_id,
                initiative_id,
                request.text,
                by=request.by,
                ground_truth=request.ground_truth,
            )
        except (ValueError, RuntimeError) as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def answer(
        plan_id: str, attempt_id: str, request: AnswerRequest
    ) -> dict[str, object]:
        try:
            plan = await daemon.operator_answer(
                plan_id,
                attempt_id,
                request.subject,
                request.answer,
                by=request.by,
            )
        except (ValueError, RuntimeError) as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def auto_answer(
        plan_id: str, attempt_id: str, request: AutoAnswerRequest
    ) -> AutoAnswerResponse:
        try:
            leaf = await daemon.auto_answer(plan_id, attempt_id, request.subject)
        except (ValueError, RuntimeError) as exc:
            raise plan_error(plan_id, exc) from exc
        return AutoAnswerResponse(leaf=leaf)

    async def restart(
        plan_id: str, initiative_id: str, request: RestartRequest | None = None
    ) -> PaneResponse:
        try:
            pane_ref = await daemon.restart_process(
                plan_id, initiative_id, by=request.by if request is not None else "operator"
            )
        except (ValueError, RuntimeError) as exc:
            raise plan_error(plan_id, exc) from exc
        return PaneResponse(pane_ref=pane_ref)

    async def focus(plan_id: str, initiative_id: str) -> PaneResponse:
        try:
            pane_ref = await daemon.focus_initiative(plan_id, initiative_id)
        except (ValueError, RuntimeError) as exc:
            raise plan_error(plan_id, exc) from exc
        return PaneResponse(pane_ref=pane_ref)

    async def impact(plan_id: str, initiative_id: str) -> DownstreamImpact:
        try:
            return daemon.impact(plan_id, initiative_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def review_route(
        action: Literal["approve", "reject", "changes"]
    ) -> Callable[[str, str, ReviewRequest | None], Awaitable[CheckpointReport]]:
        async def handler(
            plan_id: str, checkpoint_id: str, request: ReviewRequest | None = None
        ) -> CheckpointReport:
            selected = request or ReviewRequest()
            try:
                if action == "approve":
                    _ = daemon.approve_checkpoint(
                        plan_id,
                        checkpoint_id,
                        by=selected.by,
                        reason=selected.reason,
                    )
                elif action == "reject":
                    _ = daemon.reject_checkpoint(
                        plan_id,
                        checkpoint_id,
                        by=selected.by,
                        reason=selected.reason,
                    )
                else:
                    _ = daemon.request_changes(
                        plan_id,
                        checkpoint_id,
                        by=selected.by,
                        reason=selected.reason,
                    )
            except ValueError as exc:
                if plan_id not in daemon.store.plans():
                    raise HTTPException(status_code=404, detail=str(exc)) from exc
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return daemon.checkpoint_report(plan_id)

        return handler

    app.add_api_route("/plans", create, methods=["POST"])
    app.add_api_route("/plans/{plan_id}", get_plan, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/approve", approve, methods=["POST"])
    app.add_api_route("/plans/{plan_id}/run", run_whole_plan, methods=["POST"])
    app.add_api_route("/plans/{plan_id}/graph", graph, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/risk", risk, methods=["GET"])
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/run", run, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/settle/{checkpoint_id}",
        settle,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/discard/{attempt_id}",
        discard,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/retry", retry, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/redirect",
        redirect,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/reassign",
        reassign,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/nudge", nudge, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/restart",
        restart,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/focus", focus, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/impact", impact, methods=["GET"]
    )
    app.add_api_route(
        "/plans/{plan_id}/attempts/{attempt_id}/answer", answer, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/attempts/{attempt_id}/auto-answer",
        auto_answer,
        methods=["POST"],
    )
    app.add_api_route("/plans/{plan_id}/events", stream_events, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/checkpoints", checkpoints, methods=["GET"])
    app.add_api_route(
        "/plans/{plan_id}/checkpoints/{checkpoint_id}/approve",
        review_route("approve"),
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/checkpoints/{checkpoint_id}/reject",
        review_route("reject"),
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/checkpoints/{checkpoint_id}/changes",
        review_route("changes"),
        methods=["POST"],
    )
    app.add_api_route("/nav/codemap", codemap, methods=["GET"])
    app.add_api_route("/nav/tour", tour, methods=["GET"])
    app.add_api_route("/nav/flow/{name}", flow, methods=["GET"])
    app.add_api_route("/nav/symbol/{name}", symbol, methods=["GET"])
    return app


__all__ = ["Daemon", "create_app", "sse"]
