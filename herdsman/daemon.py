"""In-process daemon and its minimal HTTP surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .checkpoint import CheckpointError, Completion, GitCheckpointCollector
from .classes import (
    Checkpoint,
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
from .herdr import HerdrAdapter
from .runtime import (
    CompletionError,
    PiFrontierPlanner,
    PlannerError,
    completion_from_detail,
    compile_task_packet,
    executor_command,
    proposal_from_result,
)
from .store import EventStore


class Runtime(Protocol):
    async def create_worktree(self, branch: str) -> str: ...

    async def run(self, worktree_ref: str, command: str) -> str: ...

    def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str
    ) -> AsyncIterator[RuntimeObserved]: ...

    async def remove_worktree(self, worktree_ref: str) -> None: ...

    async def worktree_path(self, worktree_ref: str) -> Path: ...


class Collector(Protocol):
    def capture_base(self, path: Path) -> str: ...

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
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
        initiative = plan.initiatives[initiative_id]
        selected_runtime = runtime or HerdrAdapter(project_root=self.project_root)
        selected_collector = collector or GitCheckpointCollector(checks=checks)
        attempt_id = f"attempt_{uuid4().hex}"
        worktree_ref: str | None = None
        attempt_started = False
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
                path = await selected_runtime.worktree_path(worktree_ref)
                base_sha = cast(
                    str,
                    await _collector_call(
                        cast(Callable[..., object], selected_collector.capture_base),
                        path,
                        timeout=remaining(),
                    ),
                )
                packet = compile_task_packet(initiative.spec)
                pane_ref = await selected_runtime.run(
                    worktree_ref,
                    executor_command(packet, project_root=self.project_root),
                )
                _ = self.append(
                    # The adapter owns the opaque refs; neither is interpreted here.
                    AttemptStarted(
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        attempt_id=attempt_id,
                        initiative_id=initiative_id,
                        assignment=initiative.spec.assignment,
                        worktree_ref=worktree_ref,
                        pane_ref=pane_ref,
                    )
                )
                attempt_started = True
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
                        cast(Callable[..., object], selected_collector.collect),
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
            if attempt_started:
                fail("initiative run cancelled")
            raise
        except TimeoutError as exc:
            fail("initiative run timed out")
            raise RuntimeError("initiative run timed out") from exc
        except Exception as exc:
            fail(str(exc))
            raise
        finally:
            if worktree_ref is not None:
                await asyncio.shield(selected_runtime.remove_worktree(worktree_ref))

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
        if initiative.state != "running":
            raise ValueError(f"initiative {initiative_id} is not running")
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


def _supports_timeout(method: Callable[..., object]) -> bool:
    try:
        return "timeout" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


async def _collector_call(
    method: Callable[..., object],
    *args: object,
    timeout: float,
    **kwargs: object,
) -> object:
    if _supports_timeout(method):
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
            checkpoint = await daemon.run_initiative(
                plan_id,
                initiative_id,
                timeout=request.timeout if request is not None else 600.0,
            )
        except (ValueError, PermissionError, RuntimeError, CheckpointError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RunResponse(checkpoint=checkpoint)

    async def settle(plan_id: str, initiative_id: str, checkpoint_id: str) -> dict[str, object]:
        try:
            plan = daemon.settle_initiative(plan_id, initiative_id, checkpoint_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return cast(dict[str, object], plan.model_dump(mode="json"))

    app.add_api_route("/plans", create, methods=["POST"])
    app.add_api_route("/plans/{plan_id}", get_plan, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/approve", approve, methods=["POST"])
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/run", run, methods=["POST"]
    )
    app.add_api_route(
        "/plans/{plan_id}/initiatives/{initiative_id}/settle/{checkpoint_id}",
        settle,
        methods=["POST"],
    )
    app.add_api_route("/plans/{plan_id}/events", stream_events, methods=["GET"])
    return app


__all__ = ["Daemon", "create_app", "sse"]
