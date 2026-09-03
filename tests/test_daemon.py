import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope
from typing_extensions import override

from herdsman.classes import AttemptProvisioned, Event, PlanCreated, RuntimeObserved
from herdsman.daemon import Daemon, create_app, sse
from herdsman.store import EventStore
from tests.test_classes import stream


async def _next(events: AsyncGenerator[Event, None]) -> Event:
    return await anext(events)


async def _request(
    app: FastAPI, method: str, path: str
) -> tuple[int, bytes]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    status = cast(int, start["status"])
    body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in sent
        if message["type"] == "http.response.body"
    )
    return status, body


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


def test_plan_api_reviews_and_approves_a_plan(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    for event in stream()[:2]:
        _ = daemon.append(event)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await _request(app, "GET", "/plans/plan_1")
        assert status == 200
        assert json.loads(body)["approval"] == "pending"

        status, body = await _request(app, "POST", "/plans/plan_1/approve")
        assert status == 200
        assert json.loads(body)["approval"] == "approved"

        status, body = await _request(app, "POST", "/plans/plan_1/approve")
        assert status == 409
        assert "already approved" in json.loads(body)["detail"]

    try:
        asyncio.run(scenario())
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


def test_graph_and_risk_projections_are_served_over_the_api(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    for event in stream()[:2]:
        _ = daemon.append(event)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await _request(app, "GET", "/plans/plan_1/graph")
        assert status == 200
        graph = cast(dict[str, object], json.loads(body))
        assert graph["ready"] == ["init_a"]
        assert cast(list[object], graph["nodes"])
        assert cast(dict[str, object], graph["overhead"])["ratio"] is None

        status, body = await _request(app, "GET", "/plans/plan_1/risk")
        assert status == 200
        risk = cast(dict[str, object], json.loads(body))
        assert risk["critical_path"] == ["init_a", "init_c"]
        assert risk["max_concurrency"] == 1
        assert risk["conflicts"] == []

        status, _body = await _request(app, "GET", "/plans/nope/graph")
        assert status == 404

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_risk_api_reports_invalid_model_tiers_as_bad_request(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    tiers = tmp_path / ".herdsman" / "models.json"
    tiers.parent.mkdir()
    _ = tiers.write_text("{")
    daemon = Daemon(store, project_root=tmp_path)
    for event in stream()[:2]:
        _ = daemon.append(event)

    async def scenario() -> None:
        status, body = await _request(
            create_app(daemon), "GET", "/plans/plan_1/risk"
        )
        assert status == 400
        assert "invalid JSON in model tiers" in json.loads(body)["detail"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_running_a_whole_plan_requires_approval(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    for event in stream()[:2]:
        _ = daemon.append(event)

    async def scenario() -> None:
        status, body = await _request(create_app(daemon), "POST", "/plans/plan_1/run")
        assert status == 409
        assert "approved" in json.loads(body)["detail"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_store_failure_provisioning_removes_the_worktree(tmp_path: Path) -> None:
    """A store failure persisting the first worktree reference must not orphan it."""

    class BrokenStore(EventStore):
        @override
        def append(self, ev: Event) -> Event:
            if isinstance(ev, AttemptProvisioned):
                raise sqlite3.OperationalError("disk I/O error")
            return super().append(ev)

    class FakeRuntime:
        def __init__(self) -> None:
            self.worktree_ref: str = ""
            self.removed: list[str] = []

        async def create_worktree(self, branch: str) -> str:
            self.worktree_ref = f"worktree-{branch}"
            return self.worktree_ref

        async def worktree_path(self, worktree_ref: str) -> Path:
            del worktree_ref
            return tmp_path

        async def run(
            self, worktree_ref: str, command: str, *, match: str | None = None
        ) -> str:
            del worktree_ref, command, match
            raise AssertionError("the run never starts")

        def observe_events(
            self, plan_id: str, attempt_id: str, pane_ref: str
        ) -> AsyncIterator[RuntimeObserved]:
            del plan_id, attempt_id, pane_ref
            raise AssertionError("the run never reaches observation")

        async def remove_worktree(self, worktree_ref: str) -> None:
            self.removed.append(worktree_ref)

        async def aclose(self) -> None:
            return None

    store = BrokenStore(tmp_path / "events.db")
    daemon = Daemon(store)
    for event in stream()[:2]:
        _ = daemon.append(event)
    _ = daemon.approve_plan("plan_1")
    fake = FakeRuntime()

    async def scenario() -> None:
        # The store error itself must surface, not a masking removal failure.
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            _ = await daemon.run_initiative("plan_1", "init_a", runtime=fake)
        # Compensated: the worktree create_worktree returned was removed.
        assert fake.removed == [fake.worktree_ref]

    try:
        asyncio.run(scenario())
    finally:
        store.close()
