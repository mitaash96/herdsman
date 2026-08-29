"""In-process daemon and its minimal SSE surface."""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .classes import Event
from .store import EventStore


class Daemon:
    """The event store's single writer and live in-process event fan-out."""

    def __init__(self, store: EventStore) -> None:
        self.store: EventStore = store
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}

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

    app.add_api_route("/plans/{plan_id}/events", stream_events, methods=["GET"])
    return app
