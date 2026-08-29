import asyncio
from datetime import UTC, datetime
from collections.abc import AsyncGenerator
from pathlib import Path

from starlette.types import Message, Scope

from herdsman.classes import Event, PlanCreated, RuntimeObserved
from herdsman.daemon import Daemon, create_app, sse
from herdsman.store import EventStore


async def _next(events: AsyncGenerator[Event, None]) -> Event:
    return await anext(events)


async def _stream_one_event(daemon: Daemon) -> tuple[RuntimeObserved, RuntimeObserved]:
    events = daemon.events("plan_1")
    received = asyncio.create_task(_next(events))
    await asyncio.sleep(0)
    sent = daemon.append(
        RuntimeObserved(
            plan_id="plan_1",
            at=datetime(2026, 8, 25, tzinfo=UTC),
            attempt_id="attempt_1",
            kind="pane_output",
            detail={"text": "hello"},
        )
    )
    try:
        received_event = await received
        assert isinstance(sent, RuntimeObserved)
        assert isinstance(received_event, RuntimeObserved)
        return sent, received_event
    finally:
        await events.aclose()


def test_app_rejects_an_unknown_plan(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/plans/missing/events",
        "raw_path": b"/plans/missing/events",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    try:
        asyncio.run(
            create_app(Daemon(store))(
                scope,
                receive,
                send,
            )
        )
        start = next(message for message in sent if message["type"] == "http.response.start")
        assert start["status"] == 404
    finally:
        store.close()


def test_sse_streams_a_persisted_event(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    try:
        _ = daemon.append(
            PlanCreated(
                plan_id="plan_1",
                at=datetime(2026, 8, 25, tzinfo=UTC),
                brief="test plan",
            )
        )
        sent, received = asyncio.run(_stream_one_event(daemon))

        assert received == sent
        assert sent.seq > 0
        assert sse(sent) == (
            f"id: {sent.seq}\nevent: runtime_observed\ndata: {sent.model_dump_json()}\n\n"
        )
    finally:
        store.close()
