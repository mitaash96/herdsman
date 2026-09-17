"""Sprint 10 daemon integration: fleet projections, while-away digest, archive."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlsplit

from fastapi import FastAPI
from starlette.types import Message, Scope

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    Event,
    InitiativeFailed,
    InitiativeSpec,
    PlanApproved,
    PlanArchived,
    PlanCreated,
    PlanProposed,
    PlanUnarchived,
    Routes,
    RuntimeObserved,
)
from herdsman.daemon import Daemon, create_app
from herdsman.store import EventStore

AT = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify_user(self, message: str) -> bool:
        self.messages.append(message)
        return True


def spec(initiative_id: str) -> InitiativeSpec:
    return InitiativeSpec(
        id=initiative_id,
        name=initiative_id,
        brief=f"do {initiative_id}",
        assignment=LUNA,
        routes=Routes(writes=[f"src/{initiative_id}/**"]),
    )


async def request(
    app: FastAPI, method: str, path: str, body: object | None = None
) -> tuple[int, dict[str, object] | list[object]]:
    raw = b"" if body is None else json.dumps(body).encode()
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "headers": [(b"content-type", b"application/json")] if raw else [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    status = cast(int, start["status"])
    response_body = b"".join(
        cast(bytes, item.get("body", b""))
        for item in sent
        if item["type"] == "http.response.body"
    )
    return status, cast(dict[str, object] | list[object], json.loads(response_body))


def seed(store: EventStore, events: Sequence[Event]) -> Daemon:
    daemon = Daemon(store)
    for event in events:
        _ = daemon.append(event)
    return daemon


def run_app(daemon: Daemon, scenario: Callable[[], Awaitable[None]]) -> None:
    try:
        asyncio.run(scenario())
    finally:
        daemon.store.close()


def test_fleet_route_projects_active_runs_and_hides_archived(tmp_path: Path) -> None:
    live = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="a",
            assignment=LUNA,
        ),
    ]
    filed = [
        PlanCreated(plan_id="plan_2", at=AT, brief="older run"),
        PlanProposed(plan_id="plan_2", at=AT, version=1, initiatives=[spec("b")]),
        PlanArchived(plan_id="plan_2", at=AT),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), [*live, *filed])

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "GET", "/fleet")
        assert status == 200
        fleet = cast(dict[str, object], body)
        runs = cast(list[dict[str, object]], fleet["runs"])
        assert [run["plan_id"] for run in runs] == ["plan_1"]
        assert runs[0]["status"] == "running"
        assert fleet["archived"] == 1
        assert fleet["total_runs"] == 1

        status, body = await request(app, "GET", "/fleet?include_archived=true")
        assert status == 200
        runs = cast(list[dict[str, object]], cast(dict[str, object], body)["runs"])
        assert {run["plan_id"] for run in runs} == {"plan_1", "plan_2"}

        status, body = await request(app, "GET", "/fleet/active")
        assert status == 200
        runs = cast(list[dict[str, object]], cast(dict[str, object], body)["runs"])
        assert [run["plan_id"] for run in runs] == ["plan_1"]

        status, body = await request(app, "GET", "/fleet/archived")
        assert status == 200
        runs = cast(list[dict[str, object]], cast(dict[str, object], body)["runs"])
        assert [run["plan_id"] for run in runs] == ["plan_2"]
        assert runs[0]["archived"] is True

        status, body = await request(app, "GET", "/fleet/attention")
        assert status == 200
        assert all(
            item["plan_id"] != "plan_2"
            for item in cast(list[dict[str, object]], body)
        )

        status, body = await request(app, "GET", "/fleet/notifications")
        assert status == 200
        assert all(
            item["plan_id"] != "plan_2"
            for item in cast(list[dict[str, object]], body)
        )

    run_app(daemon, scenario)


def test_attention_and_notifications_are_classified_once_by_fleet(tmp_path: Path) -> None:
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="a",
            assignment=LUNA,
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=2),
            initiative_id="a", reason="checks failed",
        ),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), events)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "GET", "/fleet/attention")
        assert status == 200
        items = cast(list[dict[str, object]], body)
        assert [item["kind"] for item in items] == ["failed"]
        assert items[0]["action"] == {
            "method": "POST",
            "path": "/plans/plan_1/initiatives/a/retry",
            "label": "Retry initiative",
        }
        assert items[0]["link"] == {"path": "/run?plan=plan_1&initiative=a"}

        status, body = await request(app, "GET", "/fleet/notifications")
        assert status == 200
        items = cast(list[dict[str, object]], body)
        assert [item["kind"] for item in items] == ["failed"]

    run_app(daemon, scenario)


def test_new_blockers_notify_herdr_once_by_stable_key(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    notifier = FakeNotifier()
    daemon = Daemon(store, project_root=tmp_path, notification_adapter=notifier)

    async def scenario() -> None:
        _ = daemon.append(PlanCreated(plan_id="plan_1", at=AT, brief="ship it"))
        _ = daemon.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]
            )
        )
        await asyncio.sleep(0)
        assert notifier.messages == [
            "plan version 1 is waiting for approval (1 initiative(s))"
        ]

        # Archiving removes the blocker from active attention. Unarchiving exposes
        # the same stable key again, but it was already attempted once.
        _ = daemon.append(PlanArchived(plan_id="plan_1", at=AT))
        _ = daemon.append(PlanUnarchived(plan_id="plan_1", at=AT))
        await asyncio.sleep(0)
        assert len(notifier.messages) == 1

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_notified_keys_survive_daemon_restart(tmp_path: Path) -> None:
    """A reopened daemon never resends an already-attempted blocker.

    The reviewer's reopen-plus-unrelated-append case: the same store is
    reopened, and an append to an unrelated plan must not re-notify the
    still-pending plan gate from the previous daemon life.
    """
    store = EventStore(tmp_path / "events.db")
    notifier = FakeNotifier()
    daemon = Daemon(store, project_root=tmp_path, notification_adapter=notifier)

    async def first_life() -> None:
        _ = daemon.append(PlanCreated(plan_id="plan_1", at=AT, brief="ship it"))
        _ = daemon.append(
            PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")])
        )
        await asyncio.sleep(0)
        assert len(notifier.messages) == 1

    try:
        asyncio.run(first_life())
    finally:
        store.close()

    reopened_store = EventStore(tmp_path / "events.db")
    reopened_notifier = FakeNotifier()
    reopened = Daemon(
        reopened_store, project_root=tmp_path,
        notification_adapter=reopened_notifier,
    )

    async def second_life() -> None:
        # An append on an unrelated plan still scans the whole fleet, where
        # plan_1's gate is an unchanged active blocker.
        _ = reopened.append(PlanCreated(plan_id="plan_2", at=AT, brief="other"))
        await asyncio.sleep(0)
        assert reopened_notifier.messages == []

    try:
        asyncio.run(second_life())
    finally:
        reopened_store.close()


def test_stalled_attempt_is_attention_but_never_a_notification(
    tmp_path: Path,
) -> None:
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="a",
            assignment=LUNA,
        ),
        RuntimeObserved(
            plan_id="plan_1", at=AT + timedelta(minutes=1),
            attempt_id="att_1", kind="pane_output", detail={"status": "working"},
        ),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), events)

    async def scenario() -> None:
        app = create_app(daemon)
        at = datetime.now(UTC) + timedelta(minutes=1, seconds=1000)
        status, body = await request(
            app, "GET", f"/fleet/attention?now={quote(at.isoformat())}"
        )
        assert status == 200
        items = cast(list[dict[str, object]], body)
        assert [item["kind"] for item in items] == ["stalled"]

        status, body = await request(
            app, "GET", f"/fleet/notifications?now={quote(at.isoformat())}"
        )
        assert status == 200
        assert cast(list[object], body) == []

    run_app(daemon, scenario)


def test_while_away_digest_is_deterministic_and_since_bounded(
    tmp_path: Path,
) -> None:
    routine = RuntimeObserved(
        plan_id="plan_1", at=AT + timedelta(minutes=1),
        attempt_id="att_1", kind="pane_output", detail={"status": "working"},
    )
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=2),
            initiative_id="a", reason="checks failed",
        ),
        routine,
        PlanArchived(plan_id="plan_1", at=AT + timedelta(minutes=3)),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), events)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "POST", "/while-away")
        assert status == 200
        entries = cast(list[dict[str, object]], body)
        # Routine terminal chatter is dropped; real changes survive in order.
        types = [entry["type"] for entry in entries]
        assert "runtime_observed" not in types
        assert types == ["plan_created", "plan_proposed", "initiative_failed", "plan_archived"]

        status, body = await request(
            app,
            "POST",
            "/while-away",
            {"since": (AT + timedelta(minutes=2)).isoformat()},
        )
        assert status == 200
        types = [entry["type"] for entry in cast(list[dict[str, object]], body)]
        assert types == ["plan_archived"]

        status, body = await request(app, "POST", "/while-away")
        assert status == 200
        assert body == entries  # deterministic across calls

    run_app(daemon, scenario)


def test_archive_and_unarchive_routes_persist_and_are_idempotent(
    tmp_path: Path,
) -> None:
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
    ]
    store = EventStore(tmp_path / "events.db")
    daemon = seed(store, events)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(
            app,
            "POST",
            "/plans/plan_1/archive",
            {"by": "ana", "reason": "done", "action_id": "arch-1"},
        )
        assert status == 200
        assert cast(dict[str, object], body)["archived"] is True
        stored = [
            event for event in store.read("plan_1") if isinstance(event, PlanArchived)
        ]
        assert len(stored) == 1
        assert stored[0].by == "ana"
        assert stored[0].reason == "done"

        # A repeated action_id is answered from the fold, not appended twice.
        status, body = await request(
            app,
            "POST",
            "/plans/plan_1/archive",
            {"by": "ana", "reason": "done", "action_id": "arch-1"},
        )
        assert status == 200
        assert len(stored) == 1
        assert len(
            [
                event
                for event in store.read("plan_1")
                if isinstance(event, PlanArchived)
            ]
        ) == 1

        # A second archive without the idempotency key is a contradiction.
        status, body = await request(app, "POST", "/plans/plan_1/archive")
        assert status == 409
        assert "already archived" in str(cast(dict[str, object], body)["detail"])

        status, body = await request(app, "POST", "/plans/plan_1/unarchive")
        assert status == 200
        assert cast(dict[str, object], body)["archived"] is False

        status, body = await request(app, "POST", "/plans/plan_1/unarchive")
        assert status == 409
        assert "not archived" in str(cast(dict[str, object], body)["detail"])

        status, body = await request(app, "POST", "/plans/missing/archive")
        assert status == 404
        assert "unknown plan" in str(cast(dict[str, object], body)["detail"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_fleet_routes_404_without_plans(tmp_path: Path) -> None:
    daemon = Daemon(EventStore(tmp_path / "events.db"))

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "GET", "/fleet")
        assert status == 200
        assert cast(dict[str, object], body)["runs"] == []

        status, body = await request(app, "GET", "/fleet/attention")
        assert status == 200
        assert cast(list[object], body) == []

        status, body = await request(app, "POST", "/while-away")
        assert status == 200
        assert cast(list[object], body) == []

    run_app(daemon, scenario)


def test_one_unfoldable_plan_is_named_not_fatal(tmp_path: Path) -> None:
    """A plan whose events no longer fold must not take the fleet down with it.

    `fleet()` folds every plan on disk, so one bad sequence — an older fixture,
    a run written under a previous schema — used to 500 the whole route. It is
    reported in `unreadable` instead, because a run that cannot be read is not a
    run that is not there.
    """
    good = [
        PlanCreated(plan_id="plan_good", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_good", at=AT, version=1, initiatives=[spec("a")]),
        PlanApproved(plan_id="plan_good", at=AT, version=1),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), good)
    # Written under the store's own hand: `append` folds first and would refuse
    # this, which is exactly why a row like it can only arrive from an older
    # writer.
    orphan = AttemptStarted(
        plan_id="plan_broken", at=AT, attempt_id="att_1", initiative_id="ghost",
        assignment=LUNA,
    )
    _ = daemon.store.db.execute(
        "INSERT INTO events (plan_id, at, type, payload) VALUES (?, ?, ?, ?)",
        ("plan_broken", orphan.at.isoformat(), orphan.type, orphan.model_dump_json()),
    )

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "GET", "/fleet")
        assert status == 200
        fleet = cast(dict[str, object], body)
        runs = cast(list[dict[str, object]], fleet["runs"])
        assert [run["plan_id"] for run in runs] == ["plan_good"]
        assert fleet["unreadable"] == ["plan_broken"]
        assert fleet["total_runs"] == 1

    run_app(daemon, scenario)


def test_unarchive_event_rejoins_active_navigation(tmp_path: Path) -> None:
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[spec("a")]),
        PlanArchived(plan_id="plan_1", at=AT),
        PlanUnarchived(plan_id="plan_1", at=AT),
    ]
    daemon = seed(EventStore(tmp_path / "events.db"), events)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await request(app, "GET", "/fleet")
        assert status == 200
        fleet = cast(dict[str, object], body)
        runs = cast(list[dict[str, object]], fleet["runs"])
        assert [run["plan_id"] for run in runs] == ["plan_1"]
        assert fleet["archived"] == 0

    run_app(daemon, scenario)
