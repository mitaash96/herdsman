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
from pydantic import AwareDatetime, BaseModel

from .checkpoint import CheckpointError, Completion, GitCheckpointCollector
from .classes import (
    ArtifactRef,
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
    Plan,
    PlanApproved,
    PlanCreated,
    RuntimeObserved,
    Taint,
)
from .contracts import (
    VERIFY_CHECK,
    ContractError,
    summarize_violations,
    validate_checkpoint,
)
from .graph import (
    Overhead,
    PlanGraph,
    RiskReport,
    ancestor_patches,
    conflicts_with,
    contention,
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
    ) -> Checkpoint | None:
        """Run one approved frontier node and record, but never settle, it."""
        if timeout <= 0:
            raise ValueError("run timeout must be positive")
        plan = self.store.load(plan_id)
        if plan.approval != "approved":
            raise PermissionError("plan must be approved before running an initiative")
        if initiative_id not in plan.ready():
            raise ValueError(f"initiative {initiative_id} is not ready")
        contended = _contending_writers(plan, initiative_id)
        if contended:
            raise ValueError(
                f"initiative {initiative_id} writes where running "
                + f"{', '.join(sorted(contended))} writes; it cannot start yet"
            )
        initiative = plan.initiatives[initiative_id]
        selected_runtime = runtime or HerdrAdapter(project_root=self.project_root)
        selected_collector = collector or GitCheckpointCollector(
            checks=collect_checks(checks, initiative.spec),
            project_root=self.project_root,
        )
        attempt_id = f"attempt_{uuid4().hex}"
        packet = compile_task_packet(initiative.spec, _inputs(plan, initiative_id))
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
                assignment=initiative.spec.assignment,
                packet_tokens=estimate_tokens(packet.json()),
            )
        )
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
                    executor_command(packet, project_root=self.project_root),
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


class RunPlanRequest(BaseModel):
    timeout: float = 600.0
    max_concurrent: int | None = None


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
    return app


__all__ = ["Daemon", "create_app", "sse"]
