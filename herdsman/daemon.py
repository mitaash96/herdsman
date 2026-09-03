"""In-process daemon and its minimal HTTP surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .checkpoint import CheckpointError, Completion, GitCheckpointCollector
from .classes import (
    ArtifactRef,
    Checkpoint,
    AttemptProvisioned,
    AttemptStarted,
    CheckpointRecorded,
    Assignment,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    Plan,
    PlanApproved,
    PlanCreated,
    RuntimeObserved,
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
)
from .store import EventStore


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
            checks=checks, project_root=self.project_root
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
        failures = [check.name for check in checkpoint.checks if not check.passed]
        if checkpoint.exit_code == 0 and not failures:
            _ = self.settle_initiative(plan_id, initiative_id, checkpoint.id)
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
        """Append mechanical evidence without changing settlement state."""
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


class RunRequest(BaseModel):
    timeout: float = 600.0


class RunPlanRequest(BaseModel):
    timeout: float = 600.0
    max_concurrent: int | None = None


class RunResponse(BaseModel):
    checkpoint: Checkpoint | None


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
    return app


__all__ = ["Daemon", "create_app", "sse"]
