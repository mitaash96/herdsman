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
    APPROVE_CHECKS_GREEN,
    ArtifactRef,
    Attempt,
    Checkpoint,
    action_fingerprint,
    AttemptProvisioned,
    AttemptStarted,
    CheckpointApproved,
    CheckpointChangesRequested,
    CheckpointDecision,
    CheckpointRecorded,
    CheckpointRejected,
    Assignment,
    CheckResult,
    ContractViolation,
    Event,
    Initiative,
    InitiativeCancelled,
    InitiativeFailed,
    InitiativePaused,
    InitiativeResumed,
    InitiativeSettled,
    InitiativeSpec,
    MemoryLeaf,
    OperatorAnswered,
    Plan,
    PlanApproved,
    PlanCreated,
    PolicyDecisionRecorded,
    ProcessRestarted,
    REPEATED_FAILURE_LIMIT,
    STOP_LOSS_BUDGET,
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
from .herdr import (
    HerdrAdapter,
    HerdrError,
    HerdrResourceError,
    RuntimeInventory,
    reconcile_inventory,
)
from .observability import (
    accounted_burn,
    activity_projection,
    anomalies,
    burn_down,
    initiative_events,
    makespan_eta,
    token_ledger,
    vitals,
)
from .runtime import (
    CHECKPOINT_PATTERN,
    CompletionError,
    FailureDelta,
    PiFrontierPlanner,
    PlannerError,
    completion_from_detail,
    compile_task_packet,
    packet_snapshot,
    executor_command,
    proposal_from_result,
    resolve_model_tiers,
    LunaConfigError,
)
from .store import EventStore
from .policy import BudgetGuard, PolicyDigest, digest_projection, evaluate_checkpoint
from .verifier import Verifier


class Runtime(Protocol):
    async def create_worktree(self, branch: str) -> str: ...

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str: ...

    def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ) -> AsyncIterator[RuntimeObserved]: ...

    async def remove_worktree(self, worktree_ref: str) -> None: ...

    async def aclose(self) -> None: ...

    async def worktree_path(self, worktree_ref: str) -> Path: ...

    async def inventory(self) -> RuntimeInventory: ...


class PaneRuntime(Protocol):
    """The live-pane primitives interventions use (the herdr adapter's)."""

    async def nudge_pane(self, pane_ref: str, text: str) -> None: ...

    async def focus_pane(self, pane_ref: str) -> None: ...

    async def restart_process(self, pane_ref: str, command: str) -> str: ...

    async def interrupt_pane(self, pane_ref: str) -> None: ...

    async def aclose(self) -> None: ...


class PaneFocus(Protocol):
    """The single runtime operation needed by the UI's focus action."""

    async def focus_pane(self, pane_ref: str) -> None: ...


class Collector(Protocol):
    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str: ...

    def diagnose(
        self,
        path: Path,
        attempt_id: str,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> str | None: ...

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
        self._run_tasks: dict[tuple[str, str], asyncio.Task[object]] = {}
        """(plan_id, initiative_id) -> the live run task, so cancel can stop a
        running agent and recovery can tell stale attempts from owned ones."""

    def plan(self, plan_id: str) -> Plan:
        """Return a plan rebuilt from its persisted event stream."""
        return self.store.load(plan_id)

    def replay(
        self,
        plan_id: str,
        *,
        through_seq: int | None = None,
        through_at: AwareDatetime | None = None,
    ) -> Plan:
        """Fold an historical event prefix without touching the live cache."""
        if through_seq is not None and through_at is not None:
            raise ValueError("choose through_seq or through_at, not both")
        events = self.store.read(
            plan_id, through_seq=through_seq, through_at=through_at
        )
        return Plan.fold(events)

    def digest(self, plan_id: str) -> PolicyDigest:
        """Return deterministic, rule-attributed automatic decisions."""
        return digest_projection(self.store.load(plan_id))

    def append(self, event: Event) -> Event:
        """Persist an event, then make that persisted event visible to subscribers."""
        persisted = self.store.append(event)
        for queue in self._subscribers.get(persisted.plan_id, set()):
            # ponytail: queues are unbounded; add backpressure when clients can lag.
            queue.put_nowait(persisted)
        return persisted

    def _repeated_action(self, plan: Plan, ev: Event) -> Plan | None:
        """The idempotency gate for an action request about to be appended.

        Returns the folded plan when `ev` repeats the request its
        `action_id` already recorded — the prior outcome, answered from the
        fold. A reused key over a different action, target, or payload is a
        conflict: raised, never silently applied or silently ignored.
        """
        if ev.action_id is None:
            return None
        recorded = plan.action_ids.get(ev.action_id)
        if recorded is None:
            return None
        if recorded == f"{ev.type}:{action_fingerprint(ev)}":
            return plan
        raise ValueError(
            f"action request {ev.action_id} was already recorded as {recorded}; "
            + f"refusing to reuse it for {ev.type} with a different request"
        )

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
        action_id: str | None = None,
        unattended: bool = False,
    ) -> Checkpoint | None:
        """Run one approved frontier node and record, but never settle, it."""
        plan, initiative = self._validate_run_admission(
            plan_id, initiative_id, timeout=timeout, origin=origin
        )
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
            failures=_failure_deltas(plan, initiative_id),
        )
        # Compiled before the reservation so a task reassigned off luna, or a
        # broken Luna mapping, fails the request instead of stranding an
        # attempt that could never run.
        command = executor_command(packet, project_root=self.project_root)
        inputs = [
            self.project_root / patch for patch in ancestor_patches(plan, initiative_id)
        ]
        # Budget admission is deliberately before reservation and provisioning;
        # it never interrupts an already-running attempt.
        _ = self._admit_attempt(
            plan,
            initiative_id,
            origin=origin,
            packet_tokens=packet.snapshot().total_tokens,
        )
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
                packet_tokens=packet.snapshot().total_tokens,
                packet_snapshot=packet_snapshot(packet),
                by=by,
                origin=origin,
                action_id=action_id,
                unattended=unattended,
            )
        )
        self._attempt_commands[attempt_id] = command
        worktree_ref: str | None = None
        base_sha: str | None = None
        path: Path | None = None
        failed = False

        async def fail(reason: str) -> None:
            nonlocal failed
            if failed:
                return
            failed = True
            current = self.store.load(plan_id).initiatives[initiative_id]
            if current.state == "failed":
                return
            evidence: list[str] = []
            live = current.attempts[-1] if current.attempts else None
            # Preserve the raw diff as a repair diagnostic before anything can
            # clean the worktree up — but only when nothing was collected yet
            # (a collected checkpoint already carries its own patch) and the
            # diff base is known. A diagnostic failure never masks the
            # original one, so its own errors are swallowed.
            if (
                live is not None
                and live.checkpoint is None
                and base_sha is not None
                and path is not None
            ):
                try:
                    diagnostic = cast(
                        "str | None",
                        await _collector_call(
                            selected_collector.diagnose,
                            path,
                            attempt_id,
                            base_sha=base_sha,
                            timeout=10.0,
                        ),
                    )
                    if diagnostic is not None:
                        evidence.append(diagnostic)
                except Exception:
                    pass
            _ = self.append(
                InitiativeFailed(
                    plan_id=plan_id,
                    at=datetime.now(UTC),
                    initiative_id=initiative_id,
                    reason=reason[:2000],
                    evidence=evidence,
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
                    # The diff base rides along so a daemon death before collection
                    # cannot lose it.
                    AttemptProvisioned(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        attempt_id=attempt_id,
                        worktree_ref=worktree_ref,
                        pane_ref=pane_ref,
                        base_sha=base_sha,
                    )
                )
                checkpoint = await self._await_completion(
                    plan,
                    initiative_id,
                    attempt_id,
                    pane_ref,
                    runtime=selected_runtime,
                    collector=selected_collector,
                    path=path,
                    base_sha=base_sha,
                    timeout=remaining(),
                )
                return checkpoint
        except asyncio.CancelledError:
            await fail("initiative run cancelled")
            raise
        except TimeoutError as exc:
            await fail("initiative run timed out")
            raise RuntimeError("initiative run timed out") from exc
        except Exception as exc:
            await fail(str(exc))
            raise
        finally:
            # `run` parks a subscription before launching; if observation never
            # started, nothing else would close it.
            await asyncio.shield(selected_runtime.aclose())

    async def _await_completion(
        self,
        plan: Plan,
        initiative_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        runtime: Runtime,
        collector: Collector,
        path: Path,
        base_sha: str,
        timeout: float,
        match: str | None = None,
    ) -> Checkpoint:
        """Observe one live attempt to its marker, then collect and record.

        The tail every run shares: fresh attempts from `run_initiative` and
        reattached survivors from `resume_plan` alike. One observation path,
        one collection path, one record event — no second settlement path.
        `match` re-arms the checkpoint-marker waiter for a pane this daemon
        did not launch; a fresh run's waiter was armed by `run` already.
        """
        completion: Completion | None = None
        async for event in runtime.observe_events(
            plan.id, attempt_id, pane_ref, match=match
        ):
            if event.plan_id != plan.id or event.attempt_id != attempt_id:
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
                collector.collect,
                path,
                attempt_id,
                completion,
                base_sha=base_sha,
                timeout=timeout,
            ),
        )
        checkpoint = _verify_proposed(plan, initiative_id, checkpoint, path)
        _ = self.append(
            CheckpointRecorded(
                plan_id=plan.id,
                at=datetime.now(UTC),
                checkpoint=checkpoint,
            )
        )
        return checkpoint

    async def run_plan(
        self,
        plan_id: str,
        *,
        max_concurrent: int | None = None,
        runtime_factory: Callable[[], Runtime] | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        unattended: bool = False,
        budget_guard: BudgetGuard | None = None,
    ) -> Plan:
        """Run an approved plan to a standstill, respecting the DAG."""
        return await self._run_scheduler(
            plan_id,
            max_concurrent=max_concurrent,
            runtime_factory=runtime_factory,
            collector=collector,
            checks=checks,
            timeout=timeout,
            unattended=unattended,
            budget_guard=budget_guard,
        )

    async def run_unattended(
        self,
        plan_id: str,
        *,
        max_concurrent: int | None = None,
        runtime_factory: Callable[[], Runtime] | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        budget_guard: BudgetGuard | None = None,
    ) -> Plan:
        """Run the DAG using only each initiative's persisted policy."""
        return await self._run_scheduler(
            plan_id,
            max_concurrent=max_concurrent,
            runtime_factory=runtime_factory,
            collector=collector,
            checks=checks,
            timeout=timeout,
            unattended=True,
            budget_guard=budget_guard,
        )

    async def _run_scheduler(
        self,
        plan_id: str,
        *,
        max_concurrent: int | None,
        runtime_factory: Callable[[], Runtime] | None,
        collector: Collector | None,
        checks: Sequence[str],
        timeout: float,
        unattended: bool,
        budget_guard: BudgetGuard | None,
    ) -> Plan:
        """Shared DAG scheduler; unattended adds policy-bounded retries."""
        if timeout <= 0:
            raise ValueError("run timeout must be positive")
        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        if self.store.load(plan_id).approval != "approved":
            raise PermissionError("plan must be approved before running it")
        running: dict[asyncio.Task[Checkpoint | None], str] = {}
        stalled: set[str] = set()
        try:
            while True:
                plan = self.store.load(plan_id)
                limit = max_concurrency(plan) if max_concurrent is None else max_concurrent
                candidates = list(plan.ready())
                if unattended:
                    candidates.extend(
                        initiative.spec.id
                        for initiative in plan.initiatives.values()
                        if initiative.state == "failed"
                        and self._unattended_retryable(plan, initiative)
                    )
                for initiative_id in candidates:
                    active = set(running.values())
                    if len(running) >= limit:
                        break
                    if initiative_id in active or initiative_id in stalled:
                        continue
                    if conflicts_with(plan, initiative_id, active):
                        continue
                    task = asyncio.create_task(
                        self.run_and_settle(
                            plan_id,
                            initiative_id,
                            runtime=runtime_factory() if runtime_factory else None,
                            collector=collector,
                            checks=checks,
                            timeout=timeout,
                            origin=("retry" if plan.initiatives[initiative_id].state == "failed" else "run"),
                            unattended=unattended,
                            budget_guard=budget_guard,
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
                    if task.exception() is not None:
                        current = self.store.load(plan_id)
                        state = current.initiatives[initiative_id].state
                        if state == "pending" or (
                            state == "failed"
                            and (
                                not unattended
                                or not self._unattended_retryable(
                                    current, current.initiatives[initiative_id]
                                )
                            )
                        ):
                            stalled.add(initiative_id)
        except BaseException:
            for task in running:
                _ = task.cancel()
            if running:
                _ = await asyncio.gather(*running, return_exceptions=True)
            raise

    def _record_budget_stop(self, plan_id: str, initiative_id: str) -> None:
        """Fail closed before launching work without the 6-A ledger."""
        _ = self.store.load(plan_id).initiatives[initiative_id]
        _ = self.append(
            PolicyDecisionRecorded(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                outcome="stopped",
                rule_ids=[STOP_LOSS_BUDGET],
                reason="token budget is configured but no ledger BudgetGuard is available",
            )
        )
        _ = self.append(
            InitiativeFailed(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                reason="token budget is configured but no ledger BudgetGuard is available",
            )
        )

    @staticmethod
    def _unattended_retryable(plan: Plan, initiative: Initiative) -> bool:
        """Retry only a checks-green stop, and only below its policy ceiling."""
        if len(initiative.attempts) >= initiative.spec.policy.max_attempts:
            return False
        if not plan.dependencies_released(initiative):
            return False
        if not initiative.attempts:
            return False
        attempt_id = initiative.attempts[-1].id
        for decision in reversed(plan.policy_decisions):
            if decision.initiative_id != initiative.spec.id:
                continue
            return (
                decision.attempt_id == attempt_id
                and decision.outcome == "stopped"
                and decision.rule_ids == [APPROVE_CHECKS_GREEN]
            )
        return False

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
        action_id: str | None = None,
        unattended: bool = False,
        budget_guard: BudgetGuard | None = None,
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
        # Registered so cancel can stop a live agent and recovery can tell a
        # stale attempt from one this daemon still owns.
        task: asyncio.Task[object] | None = asyncio.current_task()
        key = (plan_id, initiative_id)
        if task is not None:
            self._run_tasks[key] = task
        try:
            attempt_origin = origin
            while True:
                if unattended:
                    current, initiative = self._validate_run_admission(
                        plan_id,
                        initiative_id,
                        timeout=timeout,
                        origin=attempt_origin,
                    )
                    if (
                        initiative.spec.policy.token_budget is not None
                        and budget_guard is None
                    ):
                        self._record_budget_stop(plan_id, initiative_id)
                        return None
                try:
                    checkpoint = await self.run_initiative(
                        plan_id,
                        initiative_id,
                        runtime=runtime,
                        collector=collector,
                        checks=checks,
                        timeout=timeout,
                        by=by,
                        origin=attempt_origin,
                        action_id=action_id,
                        unattended=unattended,
                    )
                except Exception:
                    if unattended:
                        self._record_unattended_failure_decision(plan_id, initiative_id)
                    raise
                if checkpoint is None:
                    return None
                if unattended:
                    _ = self._apply_unattended_policy(
                        plan_id,
                        initiative_id,
                        checkpoint,
                        budget_guard=budget_guard,
                    )
                    current = self.store.load(plan_id)
                    if self._unattended_retryable(
                        current, current.initiatives[initiative_id]
                    ):
                        attempt_origin = "retry"
                        action_id = None
                        continue
                else:
                    self._apply_settlement_policy(plan_id, initiative_id, checkpoint)
                return checkpoint
        finally:
            if task is not None and self._run_tasks.get(key) is task:
                _ = self._run_tasks.pop(key, None)

    def _record_unattended_failure_decision(
        self, plan_id: str, initiative_id: str
    ) -> None:
        """Attribute a failed attempt that produced no checkpoint evidence."""
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[initiative_id]
        if initiative.state != "failed" or not initiative.attempts:
            return
        attempt_id = initiative.attempts[-1].id
        if any(
            event.initiative_id == initiative_id and event.attempt_id == attempt_id
            for event in plan.policy_decisions
        ):
            return
        rule = (
            "stop_loss.retry_ceiling"
            if len(initiative.attempts) >= initiative.spec.policy.max_attempts
            else "approve.checks_green"
        )
        _ = self.append(
            PolicyDecisionRecorded(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                attempt_id=attempt_id,
                outcome="stopped",
                rule_ids=[rule],
                reason="attempt failed before checkpoint evidence was recorded",
            )
        )

    def _apply_unattended_policy(
        self,
        plan_id: str,
        initiative_id: str,
        checkpoint: Checkpoint,
        *,
        budget_guard: BudgetGuard | None = None,
    ) -> Literal["approved", "stopped", "escalated"]:
        """Record and apply one pure unattended policy decision."""
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[initiative_id]
        attempt_id = initiative.attempts[-1].id
        decision = evaluate_checkpoint(
            initiative.spec,
            checkpoint,
            attempt_count=len(initiative.attempts),
            budget_guard=budget_guard,
        )
        _ = self.append(
            PolicyDecisionRecorded(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                attempt_id=attempt_id,
                checkpoint_id=checkpoint.id,
                outcome=decision.outcome,
                rule_ids=decision.rule_ids,
                reason=decision.reason,
            )
        )
        if decision.outcome == "approved":
            self._apply_settlement_policy(plan_id, initiative_id, checkpoint)
        elif decision.outcome == "stopped":
            _ = self.append(
                InitiativeFailed(
                    plan_id=plan_id,
                    at=datetime.now(UTC),
                    initiative_id=initiative_id,
                    reason=decision.reason[:2000],
                )
            )
        return decision.outcome

    def _apply_settlement_policy(
        self, plan_id: str, initiative_id: str, checkpoint: Checkpoint
    ) -> None:
        """The one settlement policy, applied to recorded evidence.

        Every user-facing run path goes through here -- the plan scheduler and
        the single-initiative API alike, and now a reattached survivor too --
        so identical evidence settles identically no matter which one produced
        it.

        Sprint 3 gate: a contract that declares `approval="required"` never
        settles here. Its checkpoint is recorded and left for review, so its
        dependents stay blocked until `approve_checkpoint` settles it. Under
        the automatic policy a contract violation fails the initiative with a
        typed `ContractError`; the evidence stays recorded for review.
        """
        plan = self.store.load(plan_id)
        if plan.initiatives[initiative_id].spec.approval == "required":
            # The recorded evidence awaits review; contract enforcement joins
            # at settlement, so approval of invalid evidence cannot release
            # the node (and the reviewer sees the violations in the report).
            return
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
            return
        # Not a gate -- gates are Sprint 3.  Dirty evidence simply does not
        # advance the DAG, and the operator can still settle it by hand.  The
        # reason carries no checkpoint id: the id differs per attempt, and
        # identical failures must normalize identically for the fold's
        # repeated-failure signature to see the repetition.  The checkpoint
        # itself stays referenced from the attempt's recorded evidence.
        reason = (
            f"checkpoint exited {checkpoint.exit_code}"
            if checkpoint.exit_code != 0
            else f"checkpoint failed checks: {', '.join(failures)}"
        )
        _ = self.append(
            InitiativeFailed(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=initiative_id,
                reason=reason[:2000],
            )
        )

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

    def tokens(self, plan_id: str):
        """Return the deterministic attributed token ledger."""
        return token_ledger(self.store.load(plan_id))

    def status(self, plan_id: str) -> dict[str, object]:
        """Return routine observability projections without a model call."""
        plan = self.store.load(plan_id)
        events = self.store.read(plan_id)
        ledger = token_ledger(plan)
        activity = activity_projection(events)
        attempt_owner = {
            attempt.id: initiative.spec.id
            for initiative in plan.initiatives.values()
            for attempt in initiative.attempts
        }
        for item in activity:
            item.initiative_id = attempt_owner.get(item.attempt_id, "")
        return {
            "plan_id": plan_id,
            "graph": self.graph(plan_id).model_dump(mode="json"),
            "overhead": self.overhead(plan_id).model_dump(mode="json"),
            "burn_down": burn_down(plan, ledger).model_dump(mode="json"),
            "eta": makespan_eta(plan).model_dump(mode="json"),
            "anomalies": [item.model_dump(mode="json") for item in anomalies(plan, ledger)],
            "vitals": vitals(plan, events).model_dump(mode="json"),
            "activity": [item.model_dump(mode="json") for item in activity],
            "attention": [item.model_dump(mode="json") for item in plan.attention()],
            "events": [item.model_dump(mode="json") for item in initiative_events(events)],
        }

    def packet(self, plan_id: str, attempt_id: str):
        """Return one persisted packet receipt for the inspector."""
        plan = self.store.load(plan_id)
        for initiative in plan.initiatives.values():
            for attempt in initiative.attempts:
                if attempt.id == attempt_id:
                    if attempt.packet_snapshot is None:
                        raise ValueError(f"attempt {attempt_id} has no packet snapshot")
                    return attempt.packet_snapshot
        raise ValueError(f"unknown attempt {attempt_id}")

    def packet_diff(self, plan_id: str, before_attempt_id: str, after_attempt_id: str):
        from .observability import packet_diff
        return packet_diff(
            self.packet(plan_id, before_attempt_id),
            self.packet(plan_id, after_attempt_id),
        )

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
        if initiative.state not in {"running", "failed", "paused"}:
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; "
                + "only a running, failed, or paused initiative can be settled"
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
        action_id: str | None = None,
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
        request = CheckpointApproved(
            plan_id=plan_id,
            at=datetime.now(UTC),
            checkpoint_id=checkpoint_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            # Already recorded: the fold's answer to a repeated request.
            return repeat
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
        _ = self.append(request)
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[initiative.spec.id]
        latest = initiative.latest_checkpoint
        if (
            initiative.state in {"running", "failed", "paused"}
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
        action_id: str | None = None,
    ) -> Plan:
        """Reject one checkpoint version; released consumers become attention.

        The fold refuses settlement on rejected evidence and blocks further
        readiness, while the version stays in the projection for audit and the
        initiative can record a revised checkpoint. A repeated `action_id`
        returns the recorded outcome instead of appending a second verdict.
        """
        plan = self.store.load(plan_id)
        request = CheckpointRejected(
            plan_id=plan_id,
            at=datetime.now(UTC),
            checkpoint_id=checkpoint_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            return repeat
        _ = self.append(request)
        return self.store.load(plan_id)

    def request_changes(
        self,
        plan_id: str,
        checkpoint_id: str,
        *,
        by: str = "operator",
        reason: str = "",
        action_id: str | None = None,
    ) -> Plan:
        """Ask for a revision: neither approved nor rejected, still blocking."""
        plan = self.store.load(plan_id)
        request = CheckpointChangesRequested(
            plan_id=plan_id,
            at=datetime.now(UTC),
            checkpoint_id=checkpoint_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            return repeat
        _ = self.append(request)
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
        if initiative.state not in {"failed", "cancelled", "settled"}:
            raise ValueError(
                f"cannot discard attempt {attempt_id} while initiative "
                + f"{initiative_id} is {initiative.state}; it must be failed, "
                + "cancelled, or settled"
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

    # --- Sprint 5 durable recovery ----------------------------------------------

    def pause_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        by: str = "operator",
        reason: str = "",
        action_id: str | None = None,
    ) -> Plan:
        """Hold a task's scheduler; a live attempt keeps running and settles.

        The fold enforces the stop rule — no new attempt starts while paused —
        and resume recomputes the next state from attempt history, so nothing
        is stored twice. A repeated `action_id` returns the recorded outcome
        instead of appending a second pause.
        """
        plan = self.store.load(plan_id)
        if initiative_id not in plan.initiatives:
            raise ValueError(f"unknown initiative {initiative_id}")
        request = InitiativePaused(
            plan_id=plan_id,
            at=datetime.now(UTC),
            initiative_id=initiative_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            return repeat
        _ = self.append(request)
        return self.store.load(plan_id)

    def unpause_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        by: str = "operator",
        reason: str = "",
        action_id: str | None = None,
    ) -> Plan:
        """Release a paused task; the fold recomputes failed-vs-pending."""
        plan = self.store.load(plan_id)
        if initiative_id not in plan.initiatives:
            raise ValueError(f"unknown initiative {initiative_id}")
        request = InitiativeResumed(
            plan_id=plan_id,
            at=datetime.now(UTC),
            initiative_id=initiative_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            return repeat
        _ = self.append(request)
        return self.store.load(plan_id)

    async def cancel_initiative(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        by: str = "operator",
        reason: str = "",
        action_id: str | None = None,
        runtime: PaneRuntime | None = None,
    ) -> Plan:
        """Cancel a task for good, stopping a live agent by interrupting it.

        Cancel is terminal like settlement: no retry, redirect, reassignment,
        or settlement reaches a cancelled task, and downstream work stays
        pending — cancel never releases a dependency. A live agent's pane is
        interrupted with C-c so token burn stops; the worktree and every
        preserved artifact stay for `discard` and salvage. The tracked run
        task is stopped so its failure record lands before the cancel event:
        the fold would otherwise replay a failure over a cancelled task.
        A repeated `action_id` returns the recorded outcome.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        request = InitiativeCancelled(
            plan_id=plan_id,
            at=datetime.now(UTC),
            initiative_id=initiative_id,
            by=by,
            reason=reason,
            action_id=action_id,
        )
        if (repeat := self._repeated_action(plan, request)) is not None:
            return repeat
        pane = (
            initiative.attempts[-1].pane_ref
            if initiative.attempts and initiative.state in {"running", "paused"}
            else None
        )
        if pane is not None:
            adapter = runtime or HerdrAdapter(project_root=self.project_root)
            try:
                await adapter.interrupt_pane(pane)
            except HerdrResourceError:
                pass  # the pane is already gone; there is no agent to stop
            finally:
                await asyncio.shield(adapter.aclose())
        task = self._run_tasks.get((plan_id, initiative_id))
        if task is not None and task is not asyncio.current_task():
            _ = task.cancel()
            # The run task's own failure record must land first: a failure
            # event replayed after the cancel would flip the fold's state.
            _ = await asyncio.gather(task, return_exceptions=True)
        _ = self.append(request)
        return self.store.load(plan_id)

    def recovery_report(self, plan_id: str) -> RecoveryReport:
        """The fold-only recovery projection: what a daemon death left stale.

        Stale means a running initiative — or a paused one whose latest
        attempt is still live — whose latest attempt this daemon does not
        track: the exact set `resume` reconciles. Orphaned herdr
        resources are not listed here: seeing them needs the adapter, so they
        are reported by the resume action's probes instead.
        """
        plan = self.store.load(plan_id)
        return RecoveryReport(plan_id=plan_id, stale=self._stale_attempts(plan))

    async def resume_plan(
        self,
        plan_id: str,
        *,
        runtime: Runtime | None = None,
        collector: Collector | None = None,
        checks: Sequence[str] = ("uv run pytest -q",),
        timeout: float = 600.0,
        assume_missing: bool = False,
    ) -> RecoveryReport:
        """Reconcile a plan after a daemon death, without re-driving the DAG.

        Per stale attempt, deterministically: a pane that still lives in a
        Herdsman-owned workspace is reattached — the observation-to-record
        tail every run shares, then the one settlement policy, so work the
        agent finished while the daemon was down is collected, never repeated.
        A missing pane is closed with a fixed, auditable failure reason and
        the existing retry path applies unchanged. Completed work is never
        touched: settled initiatives and recorded checkpoints replay from the
        fold exactly as they were.

        No pending or failed initiative is started here — starting work is the
        operator's explicit `run`/`retry`, not a side effect of recovery.
        Every outcome is an appended event, so a repeated resume finds no
        stale attempts and writes nothing — except evidence left pending
        review, which stays listed, unwritten, until the reviewer decides.
        With `assume_missing`, stale attempts are force-closed without
        probing herdr; an attempt whose checkpoint already landed continues
        under the settlement policy instead, since there is nothing left to
        probe. Orphaned Herdsman-owned worktrees and panes are reported,
        never removed.
        """
        if timeout <= 0:
            raise ValueError("resume timeout must be positive")
        plan = self.store.load(plan_id)
        stale = self._stale_attempts(plan)
        report = RecoveryReport(plan_id=plan_id, stale=list(stale))
        if not stale:
            return report
        if assume_missing:
            outcomes: dict[str, str] = {}
            for entry in stale:
                initiative = self.store.load(plan_id).initiatives[entry.initiative_id]
                attempt = next(
                    candidate
                    for candidate in initiative.attempts
                    if candidate.id == entry.attempt_id
                )
                if attempt.checkpoint is not None:
                    # Collected work: nothing left to probe, so the recorded
                    # checkpoint continues under the policy, not the hammer.
                    outcomes[entry.initiative_id] = (
                        self._continue_recorded_checkpoint(
                            plan_id, entry.initiative_id, attempt.checkpoint
                        )
                    )
                    continue
                outcomes[entry.initiative_id] = self._close_stale_attempt(
                    plan_id,
                    entry,
                    reason=(
                        f"recovery: pane {entry.pane_ref or entry.attempt_id} "
                        + "assumed missing by operator"
                    ),
                )
            return report.model_copy(update={"outcomes": outcomes})
        selected_runtime = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            inventory = await selected_runtime.inventory()
            orphaned = reconcile_inventory(
                inventory,
                worktree_refs=self._persisted_worktree_refs(),
                pane_refs=self._persisted_pane_refs(),
            )
            outcomes = {
                entry.initiative_id: await self._reconcile_attempt(
                    plan_id,
                    entry,
                    runtime=selected_runtime,
                    collector=collector,
                    checks=checks,
                    timeout=timeout,
                    inventory=inventory,
                )
                for entry in stale
            }
        finally:
            await asyncio.shield(selected_runtime.aclose())
        return report.model_copy(
            update={
                "outcomes": outcomes,
                "orphaned_worktrees": list(orphaned.orphaned_worktrees),
                "orphaned_panes": list(orphaned.orphaned_panes),
            }
        )

    def _stale_attempts(self, plan: Plan) -> list[RecoveryAttempt]:
        """Initiatives whose latest attempt this daemon does not own.

        Running tasks always qualify. A paused task qualifies only while its
        latest attempt is still live — pause holds the scheduler, not the
        agent, so a daemon death orphans that attempt exactly like a running
        one. A task paused with no live attempt (before its first attempt,
        or after a failure closed the window) has nothing open to recover.
        """
        stale: list[RecoveryAttempt] = []
        for initiative in plan.initiatives.values():
            if initiative.state not in {"running", "paused"} or not initiative.attempts:
                continue
            if initiative.attempts[-1].id in plan.live_until:
                continue  # the live window closed with the attempt's end
            if (plan.id, initiative.spec.id) in self._run_tasks:
                continue  # a live run in this process still owns it
            attempt = initiative.attempts[-1]
            stale.append(
                RecoveryAttempt(
                    initiative_id=initiative.spec.id,
                    attempt_id=attempt.id,
                    pane_ref=attempt.pane_ref,
                    worktree_ref=attempt.worktree_ref,
                )
            )
        return stale

    def _close_stale_attempt(
        self, plan_id: str, entry: RecoveryAttempt, *, reason: str
    ) -> str:
        """Close one stale attempt with a failure event; retry applies unchanged."""
        current = self.store.load(plan_id).initiatives.get(entry.initiative_id)
        if current is None or current.state not in {"running", "paused"}:
            return "skipped"  # someone else closed it first; nothing to write
        unattended = any(
            attempt.id == entry.attempt_id and attempt.unattended
            for attempt in current.attempts
        )
        _ = self.append(
            InitiativeFailed(
                plan_id=plan_id,
                at=datetime.now(UTC),
                initiative_id=entry.initiative_id,
                reason=reason[:2000],
            )
        )
        if unattended:
            self._record_unattended_failure_decision(plan_id, entry.initiative_id)
        return "failed"

    async def _reconcile_attempt(
        self,
        plan_id: str,
        entry: RecoveryAttempt,
        *,
        runtime: Runtime,
        collector: Collector | None,
        checks: Sequence[str],
        timeout: float,
        inventory: RuntimeInventory,
    ) -> str:
        """One deterministic outcome for one stale attempt: reattach or close."""
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[entry.initiative_id]
        attempt = next(
            candidate
            for candidate in initiative.attempts
            if candidate.id == entry.attempt_id
        )
        if attempt.checkpoint is not None:
            # A CheckpointRecorded that survived the crash is already
            # collected work: never re-observed, never closed pane-missing.
            return self._continue_recorded_checkpoint(
                plan_id, entry.initiative_id, attempt.checkpoint
            )
        if entry.pane_ref is None:
            return self._close_stale_attempt(
                plan_id,
                entry,
                reason=f"recovery: attempt {entry.attempt_id} was never provisioned a pane",
            )
        if entry.pane_ref not in {pane.pane_id for pane in inventory.panes}:
            return self._close_stale_attempt(
                plan_id,
                entry,
                reason=f"daemon death: pane {entry.pane_ref} missing",
            )
        if attempt.base_sha is None or attempt.worktree_ref is None:
            # A pre-feature stream: without the diff base the attempt's work
            # cannot be collected, so it is closed and the retry path applies.
            return self._close_stale_attempt(
                plan_id,
                entry,
                reason=f"recovery: attempt {entry.attempt_id} has no recorded diff base",
            )
        selected_collector = collector or GitCheckpointCollector(
            checks=collect_checks(checks, initiative.spec),
            project_root=self.project_root,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                path = await runtime.worktree_path(attempt.worktree_ref)
                checkpoint = await self._await_completion(
                    plan,
                    entry.initiative_id,
                    attempt.id,
                    entry.pane_ref,
                    runtime=runtime,
                    collector=selected_collector,
                    path=path,
                    base_sha=attempt.base_sha,
                    timeout=max(deadline - loop.time(), 0.0),
                    match=CHECKPOINT_PATTERN,
                )
        except TimeoutError:
            return self._close_stale_attempt(
                plan_id,
                entry,
                reason="recovery: initiative run timed out",
            )
        except Exception as exc:
            return self._close_stale_attempt(
                plan_id, entry, reason=f"recovery: {exc}"
            )
        if attempt.unattended:
            outcome = self._apply_unattended_policy(
                plan_id, entry.initiative_id, checkpoint
            )
            return self._recovery_policy_outcome(
                plan_id, entry.initiative_id, outcome, reattached=True
            )
        self._apply_settlement_policy(plan_id, entry.initiative_id, checkpoint)
        return "reattached"

    def _recovery_policy_outcome(
        self,
        plan_id: str,
        initiative_id: str,
        outcome: Literal["approved", "stopped", "escalated"],
        *,
        reattached: bool = False,
    ) -> str:
        """Map an unattended recovery decision to its observable outcome."""
        if outcome == "escalated":
            return "review-pending"
        state = self.store.load(plan_id).initiatives[initiative_id].state
        if state != "settled":
            return "failed"
        return "reattached" if reattached else "settled"

    def _continue_recorded_checkpoint(
        self, plan_id: str, initiative_id: str, checkpoint: Checkpoint
    ) -> str:
        """Finish one attempt whose checkpoint landed before a daemon death.

        The work is already collected: recovery never re-observes the attempt
        and never closes its pane as missing. Mechanically the existing
        policy completes what the crash interrupted — an approval that landed
        before the crash settles exactly as `approve_checkpoint` would have,
        and otherwise clean or dirty evidence settles or fails under the one
        settlement policy — while evidence still awaiting review stays
        untouched for the reviewer. Repeat-safe: every branch writes either
        a closing event or nothing.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives[initiative_id]
        decision = initiative.checkpoint_decisions.get(
            checkpoint.id, CheckpointDecision()
        )
        if initiative.attempts[-1].unattended:
            policy_decision = next(
                (
                    event
                    for event in reversed(plan.policy_decisions)
                    if event.initiative_id == initiative_id
                    and event.attempt_id == checkpoint.attempt_id
                    and event.checkpoint_id == checkpoint.id
                ),
                None,
            )
            if policy_decision is not None:
                if policy_decision.outcome == "escalated":
                    return "review-pending"
                if policy_decision.outcome == "stopped":
                    if initiative.state != "failed":
                        _ = self.append(
                            InitiativeFailed(
                                plan_id=plan_id,
                                at=datetime.now(UTC),
                                initiative_id=initiative_id,
                                reason=policy_decision.reason[:2000],
                            )
                        )
                    return "failed"
                _ = self._settle(plan_id, initiative_id, checkpoint.id)
                return "settled"
            outcome = self._apply_unattended_policy(
                plan_id, initiative_id, checkpoint
            )
            return self._recovery_policy_outcome(plan_id, initiative_id, outcome)
        if decision.state == "approved":
            # The approval was written before the crash and the settlement
            # was not: finish the approve path's own continuation.
            _ = self._settle(plan_id, initiative_id, checkpoint.id)
            return "settled"
        if initiative.spec.approval != "required" and decision.state == "pending":
            self._apply_settlement_policy(plan_id, initiative_id, checkpoint)
            state = self.store.load(plan_id).initiatives[initiative_id].state
            return "settled" if state == "settled" else "failed"
        return "review-pending"

    def _persisted_worktree_refs(self) -> list[str]:
        """Every worktree reference any folded plan's attempts persist."""
        refs: list[str] = []
        for candidate in self.store.plans():
            for initiative in self.store.load(candidate).initiatives.values():
                refs.extend(
                    attempt.worktree_ref
                    for attempt in initiative.attempts
                    if attempt.worktree_ref is not None
                )
        return refs

    def _persisted_pane_refs(self) -> list[str]:
        """Every pane reference any folded plan's attempts persist."""
        refs: list[str] = []
        for candidate in self.store.plans():
            for initiative in self.store.load(candidate).initiatives.values():
                refs.extend(
                    attempt.pane_ref
                    for attempt in initiative.attempts
                    if attempt.pane_ref is not None
                )
        return refs

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
        action_id: str | None = None,
        unattended: bool = False,
    ) -> Checkpoint | None:
        """Retry a failed initiative: a new attempt on its current brief.

        A retry is not a process restart: it compiles a fresh packet from the
        task's current brief version, assignment, memory leaves, and the last
        failure's bounded delta, opens a fresh worktree, and reserves a new
        attempt; the failed attempt and its evidence stay in the history. The
        new attempt event names `by` and is marked `origin="retry"`, so
        persisted attempt state stays attributable and distinguishable from an
        ordinary run. Settlement follows the one policy in `run_and_settle`.

        `action_id` makes the request idempotent: a repeat is answered from
        the fold's record (the outcome it already produced) instead of
        starting a second attempt.
        """
        plan = self.store.load(plan_id)
        initiative = plan.initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        # The would-be reservation, fingerprinted without the attempt id and
        # packet estimate `run_initiative` generates per call, so a repeat of
        # the same retry request matches what was recorded, byte for byte.
        request = AttemptStarted(
            plan_id=plan_id,
            at=datetime.now(UTC),
            attempt_id="",
            initiative_id=initiative_id,
            assignment=initiative.current_assignment,
            brief_version=len(initiative.brief_versions) + 1,
            by=by,
            origin="retry",
            action_id=action_id,
            unattended=unattended,
        )
        if self._repeated_action(plan, request) is not None:
            # Already recorded: re-read the outcome instead of re-running.
            return initiative.latest_checkpoint
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
            action_id=action_id,
            unattended=unattended,
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
        refused delivery leaves no record; the event's `at` is the delivery's
        initiation time, so a delivery that began against the validated live
        attempt still folds when the attempt settles or fails during the
        pane write.
        """
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            attempt, pane = self._live_attempt(plan_id, initiative_id)
            # Delivery precedes the record: replay must never claim an
            # intervention the live agent did not receive. `initiated_at` is
            # captured synchronously with the validation, before the await,
            # so the fold can admit the record even if the attempt settles
            # during the pane write.
            initiated_at = datetime.now(UTC)
            await adapter.nudge_pane(pane, text)
            _ = self.append(
                TaskNudged(
                    plan_id=plan_id,
                    at=initiated_at,
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
        repeat requests on the same subject auto-answerable. As with every
        pane delivery, the event's `at` is the initiation time, so a delivery
        that began against the validated live attempt still folds when the
        attempt settles or fails during the pane write.
        """
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            _attempt, pane = self._pane_attempt(plan_id, attempt_id)
            # Delivery precedes the record, with the initiation time captured
            # before the await (see `nudge_initiative`).
            initiated_at = datetime.now(UTC)
            await adapter.nudge_pane(pane, _pane_answer(subject, answer))
            _ = self.append(
                OperatorAnswered(
                    plan_id=plan_id,
                    at=initiated_at,
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
            # Delivery precedes the record, as for every pane intervention;
            # the initiation time is captured before the await.
            initiated_at = datetime.now(UTC)
            await adapter.nudge_pane(pane, text)
            _ = self.append(
                TaskNudged(
                    plan_id=plan_id,
                    at=initiated_at,
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
        restart; its `at` is the initiation time, so the record still folds
        when the attempt settles or fails during the restart itself.
        """
        attempt, pane = self._live_attempt(plan_id, initiative_id)
        command = self._attempt_commands.get(attempt.id)
        if command is None:
            raise ValueError(
                f"attempt {attempt.id} has no recorded command to restart"
            )
        adapter = runtime or HerdrAdapter(project_root=self.project_root)
        try:
            initiated_at = datetime.now(UTC)
            pane_ref = await adapter.restart_process(pane, command)
        finally:
            await asyncio.shield(adapter.aclose())
        _ = self.append(
            ProcessRestarted(
                plan_id=plan_id,
                at=initiated_at,
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
        runtime: PaneFocus | None = None,
    ) -> str:
        """Focus the most recent pane recorded for an initiative."""
        initiative = self.store.load(plan_id).initiatives.get(initiative_id)
        if initiative is None:
            raise ValueError(f"unknown initiative {initiative_id}")
        pane = next(
            (
                attempt.pane_ref
                for attempt in reversed(initiative.attempts)
                if attempt.pane_ref is not None
            ),
            None,
        )
        if pane is None:
            raise ValueError(f"initiative {initiative_id} has no pane to focus")
        if runtime is not None:
            await runtime.focus_pane(pane)
            return pane
        adapter = HerdrAdapter(project_root=self.project_root)
        try:
            await adapter.focus_pane(pane)
        finally:
            await asyncio.shield(adapter.aclose())
        return pane

    def impact(self, plan_id: str, initiative_id: str) -> DownstreamImpact:
        """What a disruptive action on a task would disturb, before mutating."""
        return downstream_impact(self.store.load(plan_id), initiative_id)

    def _validate_run_admission(
        self,
        plan_id: str,
        initiative_id: str,
        *,
        timeout: float,
        origin: Literal["run", "retry"],
        packet_tokens: int = 0,
    ) -> tuple[Plan, Initiative]:
        """Validate a run without appending events or reserving an attempt."""
        if timeout <= 0:
            raise ValueError("run timeout must be positive")
        plan = self.store.load(plan_id)
        if plan.approval != "approved":
            raise PermissionError("plan must be approved before running an initiative")
        return plan, self._admit_attempt(
            plan, initiative_id, origin=origin, packet_tokens=packet_tokens
        )

    def _admit_attempt(
        self,
        plan: Plan,
        initiative_id: str,
        *,
        origin: Literal["run", "retry"] = "run",
        packet_tokens: int = 0,
    ) -> Initiative:
        """The one admission rule for starting an attempt, run or retry alike.

        A failed initiative is retryable as a new attempt on its current brief
        version and assignment; the fold still refuses a second live attempt,
        so the reservation below serializes concurrent callers.

        Repeated-failure stopping: when the last failed attempt's identical
        signature has already been retried once past the promotion limit —
        the attempt that carried the promoted memory leaf failed the same way
        again — mechanical retries stop. A changed signature (a different
        check or error) still admits, and the fold's attempt ceiling stays
        the hard bound.
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
            last = initiative.attempts[-1]
            for (owner, check, error), record in plan.failure_signatures.items():
                if owner != initiative_id or last.id not in record.attempts:
                    continue
                if record.count > REPEATED_FAILURE_LIMIT:
                    raise ValueError(
                        f"initiative {initiative_id} failed {record.count} times "
                        + f"with the same signature ({check}: {error}); "
                        + "repeated-failure stopping refuses another mechanical "
                        + "retry"
                    )
            if origin == "run":
                raise ValueError(
                    f"initiative {initiative_id} is failed; a new attempt "
                    + "on failed work is a retry: start it with origin='retry'"
                )
        else:
            raise ValueError(
                f"initiative {initiative_id} is {initiative.state}; only a "
                + "pending or failed initiative can start an attempt"
            )
        if len(initiative.attempts) >= initiative.spec.policy.max_attempts:
            raise ValueError(
                f"initiative {initiative_id} has reached the attempt ceiling of "
                + f"{initiative.spec.policy.max_attempts}; no further attempt can start"
            )
        if packet_tokens:
            prior_plan_burn = accounted_burn(plan)
            prior_initiative_burn = accounted_burn(plan, initiative_id)
            if initiative.spec.token_cap is not None and (
                prior_initiative_burn + packet_tokens > initiative.spec.token_cap
            ):
                raise ValueError(
                    f"initiative {initiative_id} token cap exhausted: "
                    + f"{prior_initiative_burn + packet_tokens} > {initiative.spec.token_cap}"
                )
            if plan.token_cap is not None and prior_plan_burn + packet_tokens > plan.token_cap:
                raise ValueError(
                    f"plan token cap exhausted: {prior_plan_burn + packet_tokens} > {plan.token_cap}"
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


def _failure_deltas(plan: Plan, initiative_id: str) -> list[FailureDelta]:
    """Bounded failure evidence for the next packet, read from the fold only.

    The last failed attempt's failed checks and failure reason, as the fold's
    signature projection normalized them. Never the transcript: each delta is
    a bounded one-line string, and `compile_task_packet` caps the count.
    """
    initiative = plan.initiatives.get(initiative_id)
    if initiative is None or not initiative.attempts:
        return []
    last = initiative.attempts[-1].id
    deltas: list[FailureDelta] = []
    for (owner, check, error), record in plan.failure_signatures.items():
        if owner != initiative_id or last not in record.attempts:
            continue
        deltas.append(
            FailureDelta(
                attempt_id=last,
                check=None if check == "error" else check,
                error=error,
            )
        )
    return deltas


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
    action_id: str | None = None


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
    unattended: bool = False


class RetryRequest(RunRequest):
    """A retry; `preview` returns the downstream impact without mutating.

    `by` names the retrying actor on the new attempt's event and state; the
    daemon stays the actor of ordinary runs. `action_id` makes the request
    idempotent: a repeat is answered from the fold's record.
    """

    by: str = "operator"
    preview: bool = False
    action_id: str | None = None


class RunPlanRequest(BaseModel):
    timeout: float = 600.0
    max_concurrent: int | None = None
    unattended: bool = False


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


class RecoveryAttempt(BaseModel):
    """One stale attempt a daemon death left running."""

    initiative_id: str
    attempt_id: str
    pane_ref: str | None = None
    worktree_ref: str | None = None


class RecoveryReport(BaseModel):
    """The reconciliation surface: stale attempts, outcomes, and orphans.

    `stale` is the fold-only projection `GET /recovery` returns. `resume`
    fills `outcomes` (per stale initiative: reattached, settled,
    review-pending, failed, or skipped)
    and the Herdsman-owned herdr resources no persisted attempt claims.
    Nothing is deleted here: cleanup stays the explicit `discard`.
    """

    plan_id: str
    stale: list[RecoveryAttempt] = []
    outcomes: dict[str, str] = {}
    orphaned_worktrees: list[str] = []
    orphaned_panes: list[str] = []


class InterventionRequest(BaseModel):
    """Who acted, why, and the idempotency key that makes repeats safe."""

    by: str = "operator"
    reason: str = ""
    action_id: str | None = None


class CancelRequest(InterventionRequest):
    """Cancel a task for good; `preview` returns the downstream impact."""

    preview: bool = False


class ResumePlanRequest(BaseModel):
    """State-recovery resume: reconcile stale attempts with the live herdr.

    `assume_missing` force-closes stale attempts without probing herdr, for
    when herdr itself is gone for good; without it, an unreachable herdr is a
    typed refusal and nothing is written.
    """

    assume_missing: bool = False
    timeout: float = 600.0


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
        selected = request or RunRequest()
        try:
            checkpoint = await daemon.run_and_settle(
                plan_id,
                initiative_id,
                timeout=selected.timeout,
                unattended=selected.unattended,
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
                unattended=selected.unattended,
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return daemon.graph(plan_id)

    async def replay(
        plan_id: str,
        seq: int | None = None,
        at: AwareDatetime | None = None,
        through_seq: int | None = None,
        through_at: AwareDatetime | None = None,
    ) -> Plan:
        selected_seq = seq if seq is not None else through_seq
        selected_at = at if at is not None else through_at
        try:
            return daemon.replay(
                plan_id, through_seq=selected_seq, through_at=selected_at
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc

    async def digest(plan_id: str) -> PolicyDigest:
        try:
            return daemon.digest(plan_id)
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc

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

    async def status(plan_id: str) -> dict[str, object]:
        try:
            return daemon.status(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def tokens(plan_id: str):
        try:
            return daemon.tokens(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def packet(plan_id: str, attempt_id: str):
        try:
            return daemon.packet(plan_id, attempt_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def packet_diff(plan_id: str, before_attempt_id: str, after_attempt_id: str):
        try:
            return daemon.packet_diff(plan_id, before_attempt_id, after_attempt_id)
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
                plan_id,
                initiative_id,
                timeout=selected.timeout,
                by=selected.by,
                action_id=selected.action_id,
                unattended=selected.unattended,
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise plan_error(plan_id, exc) from exc
        return RunResponse(checkpoint=checkpoint)

    async def pause(
        plan_id: str,
        initiative_id: str,
        request: InterventionRequest | None = None,
    ) -> dict[str, object]:
        selected = request or InterventionRequest()
        try:
            plan = daemon.pause_initiative(
                plan_id,
                initiative_id,
                by=selected.by,
                reason=selected.reason,
                action_id=selected.action_id,
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def unpause(
        plan_id: str,
        initiative_id: str,
        request: InterventionRequest | None = None,
    ) -> dict[str, object]:
        selected = request or InterventionRequest()
        try:
            plan = daemon.unpause_initiative(
                plan_id,
                initiative_id,
                by=selected.by,
                reason=selected.reason,
                action_id=selected.action_id,
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def cancel(
        plan_id: str,
        initiative_id: str,
        request: CancelRequest | None = None,
    ) -> dict[str, object]:
        selected = request or CancelRequest()
        try:
            if selected.preview:
                return {
                    "impact": daemon.impact(
                        plan_id, initiative_id
                    ).model_dump(mode="json")
                }
            plan = await daemon.cancel_initiative(
                plan_id,
                initiative_id,
                by=selected.by,
                reason=selected.reason,
                action_id=selected.action_id,
            )
        except ValueError as exc:
            raise plan_error(plan_id, exc) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    async def recovery(plan_id: str) -> RecoveryReport:
        try:
            return daemon.recovery_report(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def resume(
        plan_id: str, request: ResumePlanRequest | None = None
    ) -> RecoveryReport:
        selected = request or ResumePlanRequest()
        try:
            return await daemon.resume_plan(
                plan_id,
                timeout=selected.timeout,
                assume_missing=selected.assume_missing,
            )
        except (ValueError, RuntimeError, HerdrError) as exc:
            raise plan_error(plan_id, exc) from exc

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
                        action_id=selected.action_id,
                    )
                elif action == "reject":
                    _ = daemon.reject_checkpoint(
                        plan_id,
                        checkpoint_id,
                        by=selected.by,
                        reason=selected.reason,
                        action_id=selected.action_id,
                    )
                else:
                    _ = daemon.request_changes(
                        plan_id,
                        checkpoint_id,
                        by=selected.by,
                        reason=selected.reason,
                        action_id=selected.action_id,
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
    app.add_api_route("/plans/{plan_id}/replay", replay, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/digest", digest, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/risk", risk, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/status", status, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/tokens", tokens, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/packets/{attempt_id}", packet, methods=["GET"])
    app.add_api_route(
        "/plans/{plan_id}/packets/{before_attempt_id}/diff/{after_attempt_id}",
        packet_diff,
        methods=["GET"],
    )
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
        "/plans/{plan_id}/initiatives/{initiative_id}/pause", pause, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/unpause",
        unpause,
        methods=["POST"],
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/cancel",
        cancel,
        methods=["POST"],
    )
    app.add_api_route("/plans/{plan_id}/recovery", recovery, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/resume", resume, methods=["POST"])
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
