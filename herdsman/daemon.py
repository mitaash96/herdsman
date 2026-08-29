"""In-process daemon and its minimal SSE surface."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .classes import Event, Plan, PlanApproved
from .store import EventStore


class Daemon:
    """The event store's single writer and live in-process event fan-out."""

    def __init__(self, store: EventStore) -> None:
        self.store: EventStore = store
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}

    def plan(self, plan_id: str) -> Plan:
        """Return a plan rebuilt from its persisted event stream."""
        return self.store.load(plan_id)

    def approve_plan(self, plan_id: str, version: int | None = None) -> Plan:
        """Approve the current proposed version and return its projection."""
        plan = self.plan(plan_id)
        approved_version = plan.version if version is None else version
        _ = self.append(
            PlanApproved(
                plan_id=plan_id,
                at=datetime.now(UTC),
                version=approved_version,
            )
        )
        return self.plan(plan_id)

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

    async def get_plan(plan_id: str) -> Plan:
        try:
            return daemon.plan(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def approve_plan(plan_id: str, version: int | None = None) -> Plan:
        try:
            return daemon.approve_plan(plan_id, version)
        except ValueError as exc:
            if plan_id not in daemon.store.plans():
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.add_api_route("/plans/{plan_id}", get_plan, methods=["GET"])
    app.add_api_route("/plans/{plan_id}/approve", approve_plan, methods=["POST"])
    app.add_api_route("/plans/{plan_id}/events", stream_events, methods=["GET"])
    return app
