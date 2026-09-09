import asyncio
import json
import shlex
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi import FastAPI
from starlette.types import Message, Scope
from typing_extensions import override

from herdsman.checkpoint import Completion
from herdsman.classes import (
    Assignment,
    AttemptProvisioned,
    AttemptStarted,
    Checkpoint,
    CheckpointRecorded,
    CheckResult,
    Contract,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    OperatorAnswered,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    ProcessRestarted,
    Routes,
    RuntimeObserved,
    TaskNudged,
    TaskReassigned,
    TaskRedirected,
    Usage,
)
from herdsman.contracts import VERIFY_CHECK, ContractError
from herdsman.daemon import Daemon, create_app, sse
from herdsman.runtime import CHECKPOINT_MARKER
from herdsman.store import EventStore
from tests.test_classes import stream
from tests.test_dag_run import seed, spec

AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _next(events: AsyncGenerator[Event, None]) -> Event:
    return await anext(events)


async def _request(
    app: FastAPI, method: str, path: str, body: bytes = b""
) -> tuple[int, bytes]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

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
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if body
        else [],
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
    # The launch command now compiles before provisioning, so the scenario
    # needs a compilable Luna mapping for the compensation path to be reached.
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir(parents=True, exist_ok=True)
    _ = mapping.write_text(json.dumps({"binary": "luna-test"}))
    daemon = Daemon(store, project_root=tmp_path)
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


# --- Sprint 3: checkpoint approval lifecycle ---------------------------------


def gated_spec(node_id: str, *, depends_on: list[str] | None = None) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=f"implement {node_id}",
        assignment=LUNA,
        routes=Routes(writes=[f"{node_id}/"]),
        depends_on=depends_on or [],
        approval="required",
    )


def local_daemon(tmp_path: Path) -> tuple[EventStore, Daemon]:
    """A daemon whose executor command can compile: Luna mapped project-locally."""
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir(parents=True, exist_ok=True)
    _ = mapping.write_text(json.dumps({"binary": "luna-test"}))
    store = EventStore(tmp_path / ".herdsman" / "events.db")
    return store, Daemon(store, project_root=tmp_path)


class StubRuntime:
    """A one-shot run that emits the completion marker, without herdr."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code: int = exit_code

    async def create_worktree(self, branch: str) -> str:
        return f"worktree-{branch}"

    async def worktree_path(self, worktree_ref: str) -> Path:
        del worktree_ref
        return Path(".")

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        del worktree_ref, command, match
        return "pane-live"

    async def observe_events(
        self, plan_id: str, attempt_id: str, pane_ref: str
    ) -> AsyncIterator[RuntimeObserved]:
        del pane_ref
        payload = json.dumps(
            {
                "exit_code": self.exit_code,
                "usage": {
                    "input_tokens": 900,
                    "output_tokens": 100,
                    "source": "harness",
                },
            }
        )
        yield RuntimeObserved(
            plan_id=plan_id,
            at=AT,
            attempt_id=attempt_id,
            kind="pane_output_matched",
            detail={"read": {"text": f"{CHECKPOINT_MARKER} {payload}"}},
        )

    async def remove_worktree(self, worktree_ref: str) -> None:
        del worktree_ref
        return None

    async def aclose(self) -> None:
        return None


class StubCollector:
    """Deterministic evidence for the settlement policy under test."""

    def __init__(
        self,
        *,
        changed_paths: list[str] | None = None,
    ) -> None:
        self.changed_paths: list[str] | None = changed_paths

    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str:
        del path, inputs, timeout
        return "base-sha"

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        del path, timeout
        return Checkpoint(
            id=f"cp_{uuid4().hex}",
            attempt_id=attempt_id,
            changed_paths=(
                ["src/touched.py"] if self.changed_paths is None else self.changed_paths
            ),
            base_sha=base_sha,
            head_sha="head-sha",
            checks=[CheckResult(name="true", passed=True)],
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=f".herdsman/artifacts/{attempt_id}.patch",
        )


def gated_events() -> list[Event]:
    """Gated producer with a recorded checkpoint, feeding an automatic consumer."""
    return [
        PlanCreated(plan_id="plan_1", at=AT, brief="gated work"),
        PlanProposed(
            plan_id="plan_1",
            at=AT,
            version=1,
            initiatives=[
                gated_spec("a"),
                InitiativeSpec(
                    id="b",
                    name="b",
                    brief="implement b",
                    assignment=LUNA,
                    routes=Routes(writes=["b/"]),
                    depends_on=["a"],
                ),
            ],
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1",
            at=AT,
            attempt_id="att_1",
            initiative_id="a",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id="cp_1",
                attempt_id="att_1",
                changed_paths=["a/x.py"],
                exit_code=0,
            ),
        ),
    ]


def test_a_gated_initiative_awaits_review_until_approved(tmp_path: Path) -> None:
    """Sprint 3 exit: the gated consumer is ready exactly when approval lands."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), gated_spec("b", depends_on=["a"]))
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert checkpoint is not None
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "running"
            assert plan.ready() == []  # b stays blocked while review is pending

            report = daemon.checkpoint_report("p")
            review = report.initiatives[0]
            assert review.policy == "required"
            assert review.awaiting_review is True
            assert review.approved_version is None
            assert report.attention == []

            with pytest.raises(ValueError, match="requires approval"):
                _ = daemon.settle_initiative("p", "a", checkpoint.id)
            assert daemon.plan("p").ready() == []

            released = daemon.approve_checkpoint("p", checkpoint.id, by="reviewer")
            assert released.initiatives["a"].state == "settled"
            assert released.ready() == ["b"]
            assert (
                released.initiatives["a"].checkpoint_decisions[checkpoint.id].decided_by
                == "reviewer"
            )
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_rejected_gated_checkpoint_is_auditable_and_revisable(tmp_path: Path) -> None:
    """Sprint 3 exit: rejected evidence remains auditable and can be revised."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), gated_spec("b", depends_on=["a"]))
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None

            _ = daemon.reject_checkpoint("p", first.id, by="reviewer", reason="wrong scope")
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "failed"
            decision = plan.initiatives["a"].checkpoint_decisions[first.id]
            assert decision.state == "rejected"
            assert decision.reason == "wrong scope"
            assert plan.ready() == []
            report = daemon.checkpoint_report("p")
            assert report.initiatives[0].versions[0].decision == "rejected"
            assert report.initiatives[0].awaiting_review is False

            attempt_id = plan.initiatives["a"].attempts[0].id
            revision = Checkpoint(
                id="cp_2",
                attempt_id=attempt_id,
                changed_paths=["src/touched.py", "src/extra.py"],
                exit_code=0,
                usage=Usage(input_tokens=10, output_tokens=5, source="harness"),
            )
            _ = daemon.record_checkpoint("p", revision)
            revised = daemon.plan("p")
            assert [
                version.id for version in revised.initiatives["a"].checkpoint_versions
            ] == [first.id, "cp_2"]
            assert revised.initiatives["a"].attempts[0].checkpoint is not None
            assert revised.initiatives["a"].attempts[0].checkpoint.id == "cp_2"

            settled = daemon.approve_checkpoint("p", "cp_2", by="reviewer")
            assert settled.initiatives["a"].state == "settled"
            assert settled.ready() == ["b"]
            # The rejected version is still in the fold for audit.
            assert (
                settled.initiatives["a"].checkpoint_decisions[first.id].state
                == "rejected"
            )
        finally:
            store.close()

    asyncio.run(scenario())


def test_request_changes_blocks_until_a_revision_is_approved(tmp_path: Path) -> None:

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None

            _ = daemon.request_changes("p", first.id, reason="tighten the scope")
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "failed"
            assert (
                plan.initiatives["a"].checkpoint_decisions[first.id].state
                == "changes_requested"
            )
            with pytest.raises(ValueError, match="requires approval"):
                _ = daemon.settle_initiative("p", "a", first.id)

            attempt_id = plan.initiatives["a"].attempts[0].id
            revision = Checkpoint(
                id="cp_2",
                attempt_id=attempt_id,
                changed_paths=["src/touched.py"],
                exit_code=0,
                usage=Usage(input_tokens=10, output_tokens=5, source="harness"),
            )
            _ = daemon.record_checkpoint("p", revision)
            settled = daemon.approve_checkpoint("p", "cp_2")
            assert settled.initiatives["a"].state == "settled"
        finally:
            store.close()

    asyncio.run(scenario())


def test_later_rejection_taints_a_released_consumer(tmp_path: Path) -> None:
    """Sprint 3 exit: deterministic attention for already-released consumers."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]), spec("b", depends_on=["a"]))
            _ = await daemon.run_plan(
                "p",
                runtime_factory=StubRuntime,
                collector=StubCollector(),
            )
            producer = daemon.plan("p").initiatives["a"]
            checkpoint = producer.attempts[0].checkpoint
            assert checkpoint is not None
            assert daemon.checkpoint_report("p").attention == []

            _ = daemon.reject_checkpoint("p", checkpoint.id, reason="regression")
            report = daemon.checkpoint_report("p")
            assert len(report.attention) == 1
            item = report.attention[0]
            assert item.initiative_id == "b"
            assert item.producer_id == "a"
            assert item.checkpoint_id == checkpoint.id
            # The producer stays settled; the taint is the attention surface.
            assert daemon.plan("p").initiatives["a"].state == "settled"
        finally:
            store.close()

    asyncio.run(scenario())


def test_checkpoint_report_projects_versions_changes_and_attention(
    tmp_path: Path,
) -> None:

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), gated_spec("b", depends_on=["a"]))
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None
            _ = daemon.approve_checkpoint("p", first.id)
            # A later rejection keeps the original approval as the diff base.
            _ = daemon.reject_checkpoint("p", first.id, reason="regression")
            attempt_id = daemon.plan("p").initiatives["a"].attempts[0].id
            revision = Checkpoint(
                id="cp_2",
                attempt_id=attempt_id,
                changed_paths=["src/extra.py", "src/touched.py"],
                exit_code=0,
                usage=Usage(input_tokens=10, output_tokens=5, source="harness"),
            )
            _ = daemon.record_checkpoint("p", revision)

            review = daemon.checkpoint_report("p").initiatives[0]
            assert review.approved_version == 1
            assert review.approved_checkpoint_id == first.id
            assert review.awaiting_review is True
            assert review.versions[0].decision == "rejected"
            assert review.versions[0].superseded is True
            assert review.versions[1].decision == "pending"
            assert review.versions[1].superseded is False
            # Deterministic delta against what was actually approved.
            assert review.changes_since_approved == ["src/extra.py"]
        finally:
            store.close()

    asyncio.run(scenario())


def test_review_actions_are_served_over_the_api(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    for event in gated_events():
        _ = daemon.append(event)

    async def scenario() -> None:
        app = create_app(daemon)

        status, body = await _request(app, "GET", "/plans/plan_1/checkpoints")
        assert status == 200
        report = cast(dict[str, object], json.loads(body))
        producer = cast(dict[str, object], cast(list[object], report["initiatives"])[0])
        assert producer["policy"] == "required"
        assert producer["awaiting_review"] is True
        assert producer["approved_version"] is None

        status, body = await _request(
            app, "POST", "/plans/plan_1/checkpoints/cp_1/approve"
        )
        assert status == 200
        producer = cast(dict[str, object], json.loads(body)["initiatives"][0])
        assert producer["approved_version"] == 1
        assert producer["state"] == "settled"

        status, body = await _request(
            app, "POST", "/plans/plan_1/checkpoints/cp_1/approve"
        )
        assert status == 409
        assert "already approved" in json.loads(body)["detail"]

        status, body = await _request(
            app, "POST", "/plans/plan_1/checkpoints/cp_missing/approve"
        )
        assert status == 409
        assert "unknown checkpoint" in json.loads(body)["detail"]

        status, body = await _request(
            app, "POST", "/plans/missing/checkpoints/cp_1/approve"
        )
        assert status == 404

        # A released checkpoint can be rejected later; the report carries it.
        status, body = await _request(
            app, "POST", "/plans/plan_1/checkpoints/cp_1/reject"
        )
        assert status == 200
        producer = cast(dict[str, object], json.loads(body)["initiatives"][0])
        version = cast(dict[str, object], cast(list[object], producer["versions"])[0])
        assert version["decision"] == "rejected"
        # The approval survives as the diff base even after the rejection.
        assert producer["approved_version"] == 1

    asyncio.run(scenario())


def test_nav_routes_serve_the_live_index(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=REPO_ROOT)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await _request(app, "GET", "/nav/codemap")
        assert status == 200
        codemap = cast(dict[str, object], json.loads(body))
        files = {str(item["path"]) for item in cast(list[dict[str, object]], codemap["files"])}
        assert "herdsman/daemon.py" in files
        assert cast(list[object], codemap["symbols"])
        assert "entry_points" in codemap

        status, body = await _request(app, "GET", "/nav/tour")
        assert status == 200
        tour = cast(str, cast(dict[str, object], json.loads(body))["text"])
        assert "herdsman/daemon.py:" in tour
        assert "Checkpoint:" in tour

        status, body = await _request(
            app, "GET", "/nav/flow/create-approve-run-settle"
        )
        assert status == 200
        flow = cast(str, cast(dict[str, object], json.loads(body))["text"])
        assert "create-approve-run-settle" in flow
        assert "herdsman/daemon.py:" in flow

        status, body = await _request(
            app, "GET", "/nav/symbol/herdsman.daemon:Daemon.append"
        )
        assert status == 200
        symbol = cast(str, cast(dict[str, object], json.loads(body))["text"])
        assert "Daemon.append" in symbol
        assert "herdsman/daemon.py:" in symbol

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_the_checkpoint_lifecycle_replays_from_the_store(tmp_path: Path) -> None:
    """Sprint 3 exit: event replay rebuilds the whole review lifecycle."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), gated_spec("b", depends_on=["a"]))
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None
            _ = daemon.reject_checkpoint("p", first.id, reason="wrong scope")
            attempt_id = daemon.plan("p").initiatives["a"].attempts[0].id
            _ = daemon.record_checkpoint(
                "p",
                Checkpoint(
                    id="cp_2",
                    attempt_id=attempt_id,
                    changed_paths=["src/touched.py"],
                    exit_code=0,
                    usage=Usage(input_tokens=10, output_tokens=5, source="harness"),
                ),
            )
            _ = daemon.approve_checkpoint("p", "cp_2")

            replayed = Plan.fold(store.read("p"))
            assert replayed == daemon.plan("p")
            assert replayed.initiatives["a"].state == "settled"
            assert [
                version.id for version in replayed.initiatives["a"].checkpoint_versions
            ] == [first.id, "cp_2"]
            assert replayed.attention() == []
        finally:
            store.close()

    asyncio.run(scenario())


# --- Sprint 3: contract gates at settlement ----------------------------------


def contracted_spec(
    node_id: str,
    contract: Contract,
    *,
    depends_on: list[str] | None = None,
    writes: Sequence[str] = ("src/",),
    approval: Literal["automatic", "required"] = "automatic",
) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=f"implement {node_id}",
        assignment=LUNA,
        routes=Routes(writes=list(writes)),
        depends_on=depends_on or [],
        approval=approval,
        contract=contract,
    )


def test_an_out_of_scope_diff_cannot_settle(tmp_path: Path) -> None:
    """Sprint 3 exit: an out-of-scope diff is a typed failure, never accepted."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                contracted_spec("a", Contract(id="c"), writes=["a/"]),
                spec("b", depends_on=["a"]),
            )
            with pytest.raises(ContractError, match="out-of-scope-write") as excinfo:
                _ = await daemon.run_and_settle(
                    "p", "a", runtime=StubRuntime(), collector=StubCollector()
                )
            assert any(
                violation.code == "out-of-scope-write"
                and violation.detail == "src/touched.py"
                for violation in excinfo.value.violations
            )
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "failed"
            failed = next(
                event for event in store.read("p") if isinstance(event, InitiativeFailed)
            )
            assert "out-of-scope-write" in failed.reason
            assert plan.ready() == []  # b is not released by invalid evidence

            # The operator override cannot release it either.
            checkpoint = plan.initiatives["a"].attempts[0].checkpoint
            assert checkpoint is not None
            with pytest.raises(ContractError, match="out-of-scope-write"):
                _ = daemon.settle_initiative("p", "a", checkpoint.id)
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_missing_required_check_is_a_typed_settlement_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, contracted_spec("a", Contract(id="c", required_checks=["lint"])))
            with pytest.raises(ContractError, match="missing-check.*lint"):
                _ = await daemon.run_and_settle(
                    "p", "a", runtime=StubRuntime(), collector=StubCollector()
                )
            assert daemon.plan("p").initiatives["a"].state == "failed"
        finally:
            store.close()

    asyncio.run(scenario())


def test_command_policy_rejects_unlisted_checks(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                contracted_spec("a", Contract(id="p", allowed_commands=["uv run pytest -q"])),
            )
            with pytest.raises(ContractError, match="command-not-permitted"):
                _ = await daemon.run_and_settle(
                    "p", "a", runtime=StubRuntime(), collector=StubCollector()
                )
            assert daemon.plan("p").initiatives["a"].state == "failed"
        finally:
            store.close()

    asyncio.run(scenario())


def test_command_policy_permits_listed_checks(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, contracted_spec("a", Contract(id="ok", allowed_commands=["true"])))
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert checkpoint is not None
            assert daemon.plan("p").initiatives["a"].state == "settled"
        finally:
            store.close()

    asyncio.run(scenario())


def test_required_checks_are_composed_into_the_run(tmp_path: Path) -> None:
    from herdsman.daemon import collect_checks

    contract = Contract(
        id="c", required_checks=["true", "uv run pytest -q", VERIFY_CHECK]
    )
    checks = collect_checks(("true",), contracted_spec("a", contract))
    assert checks == ("true", "uv run pytest -q")  # deduped; verify never shell-runs
    assert collect_checks(("uv run pytest -q",), contracted_spec("a", Contract(id="x"))) == (
        "uv run pytest -q",
    )
    _ = tmp_path  # keeps the tmp_path fixture, unused here


class VerifiedRuntime(StubRuntime):
    """A run whose worktree holds real composed files for the verifier."""

    def __init__(self, files: dict[str, str]) -> None:
        super().__init__()
        self.files: dict[str, str] = files
        self.root: Path | None = None

    @override
    async def create_worktree(self, branch: str) -> str:
        self.root = Path(tempfile.mkdtemp(prefix="herdsman-verify-"))
        for relative, content in self.files.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(content, encoding="utf-8")
        return f"worktree-{branch}"

    @override
    async def worktree_path(self, worktree_ref: str) -> Path:
        assert self.root is not None
        return self.root

    @override
    async def remove_worktree(self, worktree_ref: str) -> None:
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root = None


GOOD_FEATURE = (
    "from herdsman.helper import ready\n\n\ndef main() -> bool:\n    return ready()\n"
)
PHANTOM_FEATURE = (
    "from herdsman.helper import nope\n\n\ndef main() -> bool:\n    return nope()\n"
)
WORKTREE_FILES = {
    "herdsman/__init__.py": "",
    "herdsman/helper.py": "def ready() -> bool:\n    return True\n",
}


def test_verify_proposed_blocks_a_phantom_proposal(tmp_path: Path) -> None:
    """The verifier is a contract check: BLOCK refuses settlement, with repairs."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            contract = Contract(id="c", required_checks=[VERIFY_CHECK])
            _ = seed(daemon, contracted_spec("a", contract, writes=["herdsman/"]))
            runtime = VerifiedRuntime(
                {**WORKTREE_FILES, "herdsman/feature.py": PHANTOM_FEATURE}
            )
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=runtime, collector=StubCollector(
                    changed_paths=["herdsman/feature.py"]
                )
            )
            assert checkpoint is not None
            verify = next(
                check for check in checkpoint.checks if check.name == VERIFY_CHECK
            )
            assert verify.passed is False
            assert "herdsman.helper:nope" in verify.summary
            assert daemon.plan("p").initiatives["a"].state == "failed"
            version = daemon.checkpoint_report("p").initiatives[0].versions[0]
            assert version.failed_checks == [VERIFY_CHECK]
            assert "not defined" in (version.failed_check_summaries.get(VERIFY_CHECK) or "")

            # The operator cannot settle past a BLOCKed verdict either.
            with pytest.raises(ContractError, match="failed-check.*verify-proposed"):
                _ = daemon.settle_initiative("p", "a", checkpoint.id)
        finally:
            store.close()

    asyncio.run(scenario())


def test_verify_proposed_passes_clean_code_and_settles(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            contract = Contract(id="c", required_checks=[VERIFY_CHECK])
            _ = seed(
                daemon,
                contracted_spec("a", contract, writes=["herdsman/"]),
                spec("b", depends_on=["a"]),
            )
            runtime = VerifiedRuntime(
                {**WORKTREE_FILES, "herdsman/feature.py": GOOD_FEATURE}
            )
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=runtime, collector=StubCollector(
                    changed_paths=["herdsman/feature.py"]
                )
            )
            assert checkpoint is not None
            verify = next(
                check for check in checkpoint.checks if check.name == VERIFY_CHECK
            )
            assert verify.passed is True
            assert daemon.plan("p").initiatives["a"].state == "settled"
            assert daemon.plan("p").ready() == ["b"]
        finally:
            store.close()

    asyncio.run(scenario())


def test_approval_cannot_release_contract_violating_evidence(tmp_path: Path) -> None:
    """A required policy plus a violated contract: review sees the typed
    violations, approval cannot settle them, and reject-revise-approve is
    the recovery."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            gated = contracted_spec(
                "a", Contract(id="c"), writes=["a/"], approval="required"
            )
            _ = seed(daemon, gated, spec("b", depends_on=["a"]))
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None

            review = daemon.checkpoint_report("p").initiatives[0]
            assert review.awaiting_review is True
            assert review.violations == [
                "out-of-scope-write: changed path 'src/touched.py' is outside declared writes"
            ]

            with pytest.raises(ContractError, match="out-of-scope-write"):
                _ = daemon.approve_checkpoint("p", first.id)
            assert daemon.plan("p").initiatives["a"].state != "settled"
            assert daemon.plan("p").ready() == []

            # Recovery: reject, revise in scope, approve the revision.
            _ = daemon.reject_checkpoint("p", first.id, reason="out of scope")
            attempt_id = daemon.plan("p").initiatives["a"].attempts[0].id
            _ = daemon.record_checkpoint(
                "p",
                Checkpoint(
                    id="cp_2",
                    attempt_id=attempt_id,
                    changed_paths=["a/x.py"],
                    exit_code=0,
                    usage=Usage(input_tokens=10, output_tokens=5, source="harness"),
                ),
            )
            released = daemon.approve_checkpoint("p", "cp_2", by="reviewer")
            assert released.initiatives["a"].state == "settled"
            assert released.ready() == ["b"]
            assert daemon.checkpoint_report("p").initiatives[0].violations == []
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_refused_settlement_is_never_persisted(tmp_path: Path) -> None:
    """F1 regression: a direct store append of InitiativeSettled cannot bypass
    the fold's contract gate — the event is refused and never written."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                contracted_spec("a", Contract(id="c", required_checks=["lint"])),
            )
            _ = daemon.append(
                AttemptStarted(
                    plan_id="p", at=AT, attempt_id="att_1",
                    initiative_id="a", assignment=LUNA,
                )
            )
            _ = daemon.append(
                CheckpointRecorded(
                    plan_id="p",
                    at=AT,
                    checkpoint=Checkpoint(
                        id="cp_1",
                        attempt_id="att_1",
                        changed_paths=["src/touched.py"],
                        exit_code=0,
                        checks=[CheckResult(name="true", passed=True)],
                        usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
                    ),
                )
            )
            with pytest.raises(ContractError, match="missing-check"):
                _ = daemon.store.append(
                    InitiativeSettled(
                        plan_id="p", at=AT, initiative_id="a", checkpoint_id="cp_1"
                    )
                )
            events = store.read("p")
            assert not any(event.type == "initiative_settled" for event in events)
            replayed = Plan.fold(events)  # replay stays safe and consistent
            assert replayed.initiatives["a"].state == "running"
        finally:
            store.close()

    asyncio.run(scenario())


def test_approval_of_invalid_evidence_is_refused_and_stays_pending(
    tmp_path: Path,
) -> None:
    """F2 regression: approve_checkpoint validates the contract first — a
    refused approval persists no CheckpointApproved event and the decision
    stays pending for a revised version."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                contracted_spec("a", Contract(id="c"), writes=["a/"], approval="required"),
            )
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None

            with pytest.raises(ContractError, match="out-of-scope-write"):
                _ = daemon.approve_checkpoint("p", first.id)
            plan = daemon.plan("p")
            decision = plan.initiatives["a"].checkpoint_decisions[first.id]
            assert decision.state == "pending"
            assert not any(
                event.type == "checkpoint_approved" for event in store.read("p")
            )
            assert plan.initiatives["a"].state == "running"
            assert plan.initiatives["a"].latest_checkpoint is not None
            assert plan.initiatives["a"].latest_checkpoint.id == first.id
        finally:
            store.close()

    asyncio.run(scenario())
def test_nav_routes_map_unknown_names_to_404(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=REPO_ROOT)

    async def scenario() -> None:
        app = create_app(daemon)
        status, body = await _request(app, "GET", "/nav/flow/no-such-flow")
        assert status == 404
        assert "unknown flow" in json.loads(body)["detail"]

        status, body = await _request(app, "GET", "/nav/symbol/no-such-symbol-xyz")
        assert status == 404
        assert "unknown symbol" in json.loads(body)["detail"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


# --- Sprint 4: task interventions ---------------------------------------------


class PaneStub:
    """Records the live-pane primitive calls an intervention made."""

    def __init__(self) -> None:
        self.nudges: list[tuple[str, str]] = []
        self.focused: list[str] = []
        self.restarts: list[tuple[str, str]] = []

    async def nudge_pane(self, pane_ref: str, text: str) -> None:
        self.nudges.append((pane_ref, text))

    async def focus_pane(self, pane_ref: str) -> None:
        self.focused.append(pane_ref)

    async def restart_process(self, pane_ref: str, command: str) -> str:
        self.restarts.append((pane_ref, command))
        return pane_ref

    async def aclose(self) -> None:
        return None


class CapturingRuntime(StubRuntime):
    """A one-shot run that records the command it was given."""

    def __init__(self, exit_code: int = 0) -> None:
        super().__init__(exit_code)
        self.commands: list[str] = []

    @override
    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        self.commands.append(command)
        return await super().run(worktree_ref, command, match=match)


def packet_from_command(command: str) -> dict[str, object]:
    """The packet an executor received, parsed back out of the launch command."""
    prompt = shlex.split(command)[-1]
    return cast(dict[str, object], json.loads(prompt.split("TASK_PACKET=", 1)[1]))


def test_retry_is_a_new_attempt_on_the_current_brief_assignment_and_leaves(
    tmp_path: Path,
) -> None:
    """Sprint 4: retry keeps attempt history and runs on redirected ground truth."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                spec("a", writes=["a/"]),
                spec("b", depends_on=["a"], writes=["b/"]),
                spec("c", writes=["c/"]),
            )
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "failed"

            _ = daemon.redirect_initiative(
                "p", "a", "revised brief", by="lead", reason="scope changed"
            )
            big = Assignment(harness="luna", model="big-1")
            _ = daemon.reassign_initiative("p", "a", big, by="lead")
            runner = CapturingRuntime()
            checkpoint = await daemon.retry_initiative(
                "p", "a", runtime=runner, collector=StubCollector()
            )
            assert checkpoint is not None
            plan = daemon.plan("p")
            initiative = plan.initiatives["a"]
            # The failed attempt and its evidence stay in the history.
            assert initiative.state == "settled"
            assert [attempt.assignment for attempt in initiative.attempts] == [LUNA, big]
            assert initiative.attempts[0].checkpoint is not None
            # The retry ran on the redirected brief, the reassigned model, and
            # the run-scoped leaf the redirect projected.
            packet = packet_from_command(runner.commands[-1])
            assert packet["brief"] == "revised brief"
            assert packet["assignment"] == {"harness": "luna", "model": "big-1"}
            assert packet["memory"] == [
                "[redirect] a.brief: revised brief"
            ]
            started = [
                event for event in store.read("p") if isinstance(event, AttemptStarted)
            ]
            assert started[-1].brief_version == 2
            assert started[-1].assignment == big
            # Independent and downstream work is undisturbed.
            assert plan.initiatives["b"].state == "pending"
            assert plan.initiatives["c"].state == "pending"
        finally:
            store.close()

    asyncio.run(scenario())


def test_retry_only_applies_to_a_failed_initiative(tmp_path: Path) -> None:
    """A retry is the failed-initiative action; run is the pending one."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), spec("c", writes=["c/"]))
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert checkpoint is not None
            assert daemon.plan("p").initiatives["a"].state == "running"
            with pytest.raises(ValueError, match="retry retries a failed initiative"):
                _ = await daemon.retry_initiative("p", "a")
            with pytest.raises(ValueError, match="retry retries a failed initiative"):
                _ = await daemon.retry_initiative("p", "c")
            _ = daemon.approve_checkpoint("p", checkpoint.id)
            with pytest.raises(ValueError, match="retry retries a failed initiative"):
                _ = await daemon.retry_initiative("p", "a")
        finally:
            store.close()

    asyncio.run(scenario())


def test_retry_refuses_while_a_dependency_checkpoint_is_unapproved(
    tmp_path: Path,
) -> None:
    """A retry cannot start on evidence that is no longer approved."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                spec("a", writes=["a/"]),
                spec("b", depends_on=["a"], writes=["b/"]),
            )
            first = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert first is not None
            _ = await daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["b"].state == "failed"
            _ = daemon.reject_checkpoint("p", first.id, by="reviewer", reason="wrong base")
            with pytest.raises(ValueError, match="checkpoint is not currently approved"):
                _ = await daemon.retry_initiative("p", "b")
            # One admission rule serves both the run and the retry path.
            with pytest.raises(ValueError, match="checkpoint is not currently approved"):
                _ = await daemon.run_initiative(
                    "p", "b", runtime=StubRuntime(), collector=StubCollector()
                )
            assert len(daemon.plan("p").initiatives["b"].attempts) == 1
        finally:
            store.close()

    asyncio.run(scenario())


def test_reassign_refuses_a_non_luna_harness_at_the_action(
    tmp_path: Path,
) -> None:
    """The executor boundary stays closed: model-only reassignment."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            with pytest.raises(ValueError, match="explicit luna"):
                _ = daemon.reassign_initiative(
                    "p", "a", Assignment(harness="pi", model="default"), reason="try pi"
                )
            # The refusal left no event and no phantom attempt behind.
            assert not [
                event for event in store.read("p") if isinstance(event, TaskReassigned)
            ]
            assert daemon.plan("p").initiatives["a"].assignment_override is None
            assert len(daemon.plan("p").initiatives["a"].attempts) == 1
            # A model-only reassignment still works.
            _ = daemon.reassign_initiative(
                "p", "a", Assignment(harness="luna", model="big-1"), reason="more room"
            )
            assert daemon.plan("p").initiatives["a"].assignment_override == Assignment(
                harness="luna", model="big-1"
            )
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_nudge_reaches_the_live_pane_and_ground_truth_becomes_a_leaf(
    tmp_path: Path,
) -> None:
    """A nudge steers the live attempt; the ground-truth flag records a leaf."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "running"
            pane = PaneStub()
            _ = await daemon.nudge_initiative(
                "p",
                "a",
                "focus on the parser first",
                by="lead",
                ground_truth=True,
                runtime=pane,
            )
            assert pane.nudges == [("pane-live", "focus on the parser first")]
            plan = daemon.plan("p")
            leaf = plan.memory_leaves[-1]
            assert (leaf.subject, leaf.claim, leaf.origin, leaf.by) == (
                "a.nudge",
                "focus on the parser first",
                "nudge",
                "lead",
            )
            nudged = [event for event in store.read("p") if isinstance(event, TaskNudged)]
            assert nudged[-1].attempt_id == plan.initiatives["a"].attempts[-1].id
            # A plain nudge steers the pane without adding a leaf.
            before = len(plan.memory_leaves)
            _ = await daemon.nudge_initiative("p", "a", "carry on", runtime=pane)
            assert len(daemon.plan("p").memory_leaves) == before
        finally:
            store.close()

    asyncio.run(scenario())


def test_an_operator_answer_is_a_leaf_and_reaches_the_pane(tmp_path: Path) -> None:
    """One operator answer is the audit record, the leaf, and the pane message."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            attempt_id = daemon.plan("p").initiatives["a"].attempts[-1].id
            pane = PaneStub()
            _ = await daemon.operator_answer(
                "p",
                attempt_id,
                "tabs-or-spaces",
                "tabs",
                by="reviewer",
                runtime=pane,
            )
            assert pane.nudges == [("pane-live", "[tabs-or-spaces] tabs")]
            leaf = daemon.plan("p").memory_leaves[-1]
            assert (leaf.subject, leaf.claim, leaf.origin, leaf.by) == (
                "tabs-or-spaces",
                "tabs",
                "operator-answer",
                "reviewer",
            )
        finally:
            store.close()

    asyncio.run(scenario())


def test_repeat_requests_auto_answer_from_the_leaf(tmp_path: Path) -> None:
    """A repeated request on an answered subject needs no operator turn."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            attempt_id = daemon.plan("p").initiatives["a"].attempts[-1].id
            _ = await daemon.operator_answer(
                "p", attempt_id, "tabs-or-spaces", "tabs", runtime=PaneStub()
            )
            pane = PaneStub()
            before = len(daemon.plan("p").memory_leaves)
            leaf = await daemon.auto_answer(
                "p", attempt_id, " Tabs-or-Spaces ", runtime=pane
            )
            assert leaf is not None and leaf.subject == "tabs-or-spaces"
            assert pane.nudges == [("pane-live", "[tabs-or-spaces] tabs")]
            # The mechanical answer adds no leaf: the leaf already carries it.
            assert len(daemon.plan("p").memory_leaves) == before
            delivered = [
                event for event in store.read("p") if isinstance(event, TaskNudged)
            ][-1]
            assert delivered.by == f"daemon:{leaf.id}"
            assert delivered.ground_truth is False
            # An unmatched subject needs an operator turn.
            assert (
                await daemon.auto_answer("p", attempt_id, "which license?", runtime=pane)
                is None
            )
            assert pane.nudges == [("pane-live", "[tabs-or-spaces] tabs")]
        finally:
            store.close()

    asyncio.run(scenario())


def test_process_restart_interrupts_and_records_one_auditable_event(
    tmp_path: Path,
) -> None:
    """A restart is delivered first, then folded as one attributable event."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            runner = CapturingRuntime()
            _ = await daemon.run_and_settle("p", "a", runtime=runner, collector=StubCollector())
            pane = PaneStub()
            pane_ref = await daemon.restart_process("p", "a", by="lead", runtime=pane)
            assert pane_ref == "pane-live"
            assert pane.restarts == [("pane-live", runner.commands[-1])]
            restarted = [
                event
                for event in store.read("p")
                if isinstance(event, ProcessRestarted)
            ]
            assert [(event.attempt_id, event.by) for event in restarted] == [
                (daemon.plan("p").initiatives["a"].attempts[-1].id, "lead")
            ]
            # Restart is not a retry: same attempt, no new packet.
            assert len(daemon.plan("p").initiatives["a"].attempts) == 1

            # A pane that never takes the restart leaves no audit event.
            class DeadPane(PaneStub):
                @override
                async def restart_process(self, pane_ref: str, command: str) -> str:
                    raise RuntimeError("pane is gone")

            before = len(store.read("p"))
            with pytest.raises(RuntimeError, match="pane is gone"):
                _ = await daemon.restart_process("p", "a", runtime=DeadPane())
            assert len(store.read("p")) == before

            # A fresh daemon (e.g. after a crash) has no command to re-issue.
            fresh = Daemon(store, project_root=tmp_path)
            with pytest.raises(ValueError, match="no recorded command"):
                _ = await fresh.restart_process("p", "a", runtime=pane)
        finally:
            store.close()

    asyncio.run(scenario())


def test_focus_targets_the_live_pane(tmp_path: Path) -> None:
    """A task reference is enough to bring its pane forward."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "failed"
            pane = PaneStub()
            assert await daemon.focus_initiative("p", "a", runtime=pane) == "pane-live"
            assert pane.focused == ["pane-live"]
            # Focus is a read-only convenience: a failed or settled task's
            # pane still qualifies, unlike the event-producing pane actions.
            _ = await daemon.retry_initiative(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "settled"
            pane = PaneStub()
            assert await daemon.focus_initiative("p", "a", runtime=pane) == "pane-live"
            assert pane.focused == ["pane-live"]
        finally:
            store.close()

    asyncio.run(scenario())


def test_pane_interventions_persist_only_after_delivery(tmp_path: Path) -> None:
    """A pane that never takes the message leaves no event and no leaf.

    Replay must never claim an intervention the live agent did not receive:
    otherwise ground-truth leaves could enter later packets from a message
    that never reached anyone.
    """

    class DeadPane(PaneStub):
        """Every delivery fails, like a pane that exited under the daemon."""

        @override
        async def nudge_pane(self, pane_ref: str, text: str) -> None:
            raise RuntimeError("pane is gone")

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            attempt_id = daemon.plan("p").initiatives["a"].attempts[-1].id
            before = len(store.read("p"))
            pane = DeadPane()
            with pytest.raises(RuntimeError, match="pane is gone"):
                _ = await daemon.nudge_initiative(
                    "p", "a", "focus on the parser", ground_truth=True, runtime=pane
                )
            with pytest.raises(RuntimeError, match="pane is gone"):
                _ = await daemon.operator_answer(
                    "p", attempt_id, "tabs-or-spaces", "tabs", runtime=pane
                )
            assert len(store.read("p")) == before
            assert daemon.plan("p").memory_leaves == []
            # With a matching leaf recorded, a failed auto-answer delivery
            # must likewise persist nothing.
            _ = await daemon.operator_answer(
                "p", attempt_id, "tabs-or-spaces", "tabs", runtime=PaneStub()
            )
            recorded = len(store.read("p"))
            with pytest.raises(RuntimeError, match="pane is gone"):
                _ = await daemon.auto_answer(
                    "p", attempt_id, "tabs-or-spaces", runtime=pane
                )
            assert len(store.read("p")) == recorded
            assert [
                leaf for leaf in daemon.plan("p").memory_leaves if leaf.origin == "nudge"
            ] == []
        finally:
            store.close()

    asyncio.run(scenario())


def test_answers_cannot_target_a_historical_attempt(tmp_path: Path) -> None:
    """Answers and auto-answers resolve to the live attempt only."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            _ = await daemon.retry_initiative(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            initiative = daemon.plan("p").initiatives["a"]
            assert len(initiative.attempts) == 2
            historical = initiative.attempts[0].id
            pane = PaneStub()
            with pytest.raises(ValueError, match="unknown or superseded attempt"):
                _ = await daemon.operator_answer(
                    "p", historical, "tabs-or-spaces", "tabs", runtime=pane
                )
            with pytest.raises(ValueError, match="unknown or superseded attempt"):
                _ = await daemon.auto_answer(
                    "p", historical, "tabs-or-spaces", runtime=pane
                )
            # The refusals delivered nothing and persisted nothing.
            assert pane.nudges == []
            assert daemon.plan("p").memory_leaves == []
            assert not [
                event for event in store.read("p") if isinstance(event, OperatorAnswered)
            ]
        finally:
            store.close()

    asyncio.run(scenario())


def test_impact_previews_what_a_disruptive_action_would_disturb(tmp_path: Path) -> None:
    """Downstream impact is a read, shown before a disruptive action commits."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                spec("a", writes=["a/"]),
                spec("b", depends_on=["a"], writes=["b/"]),
                spec("c", depends_on=["b"], writes=["c/"]),
            )
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            _ = await daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            impact = daemon.impact("p", "a")
            assert impact.initiative_id == "a"
            assert [node.initiative_id for node in impact.descendants] == ["b", "c"]
            assert impact.started == ["b"]
            assert impact.descendants[0].state == "failed"
        finally:
            store.close()

    asyncio.run(scenario())


def test_interventions_respect_the_domain_guards(tmp_path: Path) -> None:
    """The fold's guards surface through the daemon actions unchanged."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"), spec("b", writes=["b/"]), spec("c"))
            _ = await daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["b"].state == "settled"
            with pytest.raises(ValueError, match="can be redirected"):
                _ = daemon.redirect_initiative("p", "b", "nope")
            with pytest.raises(ValueError, match="can be reassigned"):
                _ = daemon.reassign_initiative("p", "b", Assignment(harness="luna", model="big-1"))
            with pytest.raises(ValueError, match="only a running task has a live pane"):
                _ = await daemon.nudge_initiative("p", "b", "nope", runtime=PaneStub())
            with pytest.raises(ValueError, match="only a running task has a live pane"):
                _ = await daemon.nudge_initiative("p", "c", "nope", runtime=PaneStub())
            with pytest.raises(ValueError, match="unknown or superseded attempt"):
                _ = await daemon.operator_answer(
                    "p", "attempt_missing", "subject", "answer", runtime=PaneStub()
                )
            # A live task refuses a reassignment onto its current assignment.
            with pytest.raises(ValueError, match="already assigned"):
                _ = daemon.reassign_initiative("p", "a", LUNA)
        finally:
            store.close()

    asyncio.run(scenario())

# --- Lane 5: intervention HTTP surfaces ---------------------------------------


def _json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode()


def test_retry_route_runs_a_new_attempt_on_the_current_brief(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """POST retry settles a failed initiative; a settled one is refused."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "failed"

            def stub_runtime(**kwargs: object) -> StubRuntime:
                del kwargs
                return StubRuntime()

            def stub_collector(*args: object, **kwargs: object) -> StubCollector:
                del args, kwargs
                return StubCollector()

            monkeypatch.setattr("herdsman.daemon.HerdrAdapter", stub_runtime)
            monkeypatch.setattr(
                "herdsman.daemon.GitCheckpointCollector", stub_collector
            )
            app = create_app(daemon)

            status, body = await _request(
                app, "POST", "/plans/p/initiatives/a/retry", _json_body({"timeout": 600.0})
            )
            assert status == 200
            assert json.loads(body)["checkpoint"] is not None
            initiative = daemon.plan("p").initiatives["a"]
            assert initiative.state == "settled"
            assert len(initiative.attempts) == 2

            status, body = await _request(
                app, "POST", "/plans/p/initiatives/a/retry", _json_body({})
            )
            assert status == 409
            assert "retry retries a failed initiative" in json.loads(body)["detail"]

            status, _body = await _request(
                app, "POST", "/plans/missing/initiatives/a/retry", _json_body({})
            )
            assert status == 404
        finally:
            store.close()

    asyncio.run(scenario())


def test_redirect_and_reassign_routes_fold_audit_and_validate(
    tmp_path: Path,
) -> None:
    """Redirect/reassign persist attributable events; empty briefs stop at 422."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]))
            app = create_app(daemon)

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/redirect",
                _json_body({"brief": "v2 brief", "by": "lead", "reason": "scope"}),
            )
            assert status == 200
            versions = cast(
                list[dict[str, object]],
                json.loads(body)["initiatives"]["a"]["brief_versions"],
            )
            assert versions[-1]["brief"] == "v2 brief"
            assert versions[-1]["by"] == "lead"
            redirected = [
                event for event in store.read("p") if isinstance(event, TaskRedirected)
            ]
            assert redirected[-1].by == "lead"

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/reassign",
                _json_body({"harness": "luna", "model": "big-1"}),
            )
            assert status == 200
            plan = daemon.plan("p")
            assert plan.initiatives["a"].assignment_override == Assignment(
                harness="luna", model="big-1"
            )
            reassigned = [
                event for event in store.read("p") if isinstance(event, TaskReassigned)
            ]
            assert reassigned[-1].assignment.model == "big-1"

            # The same assignment twice is a domain refusal, not a new event.
            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/reassign",
                _json_body({"harness": "luna", "model": "big-1"}),
            )
            assert status == 409
            assert "already assigned" in json.loads(body)["detail"]

            # Empty briefs are refused by the fold before anything persists.
            status, _body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/redirect",
                _json_body({"brief": ""}),
            )
            assert status == 409
            assert "cannot be empty" in json.loads(_body)["detail"]

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/nope/redirect",
                _json_body({"brief": "v2"}),
            )
            assert status == 409

            status, _body = await _request(
                app,
                "POST",
                "/plans/missing/initiatives/a/redirect",
                _json_body({"brief": "v2"}),
            )
            assert status == 404
        finally:
            store.close()

    asyncio.run(scenario())


def test_nudge_answer_and_auto_answer_routes_share_one_template(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """An operator answer makes a repeat subject mechanically answerable."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            attempt_id = daemon.plan("p").initiatives["a"].attempts[-1].id
            pane = PaneStub()

            def pane_factory(**kwargs: object) -> PaneStub:
                del kwargs
                return pane

            monkeypatch.setattr("herdsman.daemon.HerdrAdapter", pane_factory)
            app = create_app(daemon)

            status, _body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/nudge",
                _json_body({"text": "focus", "by": "lead", "ground_truth": True}),
            )
            assert status == 200
            assert pane.nudges == [("pane-live", "focus")]
            assert len(daemon.plan("p").memory_leaves) == 1

            status, _body = await _request(
                app,
                "POST",
                f"/plans/p/attempts/{attempt_id}/answer",
                _json_body({"subject": "tabs", "answer": "spaces", "by": "op"}),
            )
            assert status == 200
            assert pane.nudges[-1] == ("pane-live", "[tabs] spaces")
            answered = [
                event for event in store.read("p") if isinstance(event, OperatorAnswered)
            ]
            assert answered[-1].by == "op"

            leaves_before = len(daemon.plan("p").memory_leaves)
            status, body = await _request(
                app,
                "POST",
                f"/plans/p/attempts/{attempt_id}/auto-answer",
                _json_body({"subject": " TABS "}),
            )
            assert status == 200
            leaf = cast(dict[str, object], json.loads(body)["leaf"])
            assert leaf is not None and leaf["subject"] == "tabs"
            assert pane.nudges[-1] == ("pane-live", "[tabs] spaces")
            # The mechanical delivery adds no leaf; the audit is the nudge.
            assert len(daemon.plan("p").memory_leaves) == leaves_before
            delivered = [
                event for event in store.read("p") if isinstance(event, TaskNudged)
            ][-1]
            assert delivered.by == f"daemon:{leaf['id']}"

            status, body = await _request(
                app,
                "POST",
                f"/plans/p/attempts/{attempt_id}/auto-answer",
                _json_body({"subject": "which license?"}),
            )
            assert status == 200
            assert json.loads(body)["leaf"] is None

            status, body = await _request(
                app,
                "POST",
                "/plans/p/attempts/attempt_missing/answer",
                _json_body({"subject": "tabs", "answer": "spaces"}),
            )
            assert status == 409
            assert "unknown or superseded attempt" in json.loads(body)["detail"]
        finally:
            store.close()

    asyncio.run(scenario())


def test_restart_and_focus_routes_target_the_live_pane(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Restart re-issues the recorded command with no new attempt."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            runner = CapturingRuntime()
            _ = await daemon.run_and_settle(
                "p", "a", runtime=runner, collector=StubCollector()
            )
            pane = PaneStub()

            def live_pane(**kwargs: object) -> PaneStub:
                del kwargs
                return pane

            monkeypatch.setattr("herdsman.daemon.HerdrAdapter", live_pane)
            app = create_app(daemon)

            status, body = await _request(app, "POST", "/plans/p/initiatives/a/restart")
            assert status == 200
            assert json.loads(body) == {"pane_ref": "pane-live"}
            assert pane.restarts == [("pane-live", runner.commands[-1])]
            # Distinct from retry by construction: still one attempt.
            assert len(daemon.plan("p").initiatives["a"].attempts) == 1

            status, body = await _request(app, "POST", "/plans/p/initiatives/a/focus")
            assert status == 200
            assert json.loads(body) == {"pane_ref": "pane-live"}
            assert pane.focused == ["pane-live"]

            status, body = await _request(
                app, "POST", "/plans/p/initiatives/nope/restart"
            )
            assert status == 409

            status, _body = await _request(
                app, "POST", "/plans/missing/initiatives/a/focus"
            )
            assert status == 404
        finally:
            store.close()

    asyncio.run(scenario())


def test_impact_route_previews_before_a_disruptive_mutation(
    tmp_path: Path,
) -> None:
    """Impact is a read, shown before the redirect that disturbs it commits."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                spec("a", writes=["a/"]),
                spec("b", depends_on=["a"], writes=["b/"]),
                spec("c", depends_on=["b"], writes=["c/"]),
            )
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            _ = await daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            app = create_app(daemon)

            status, body = await _request(app, "GET", "/plans/p/initiatives/a/impact")
            assert status == 200
            impact = cast(dict[str, object], json.loads(body))
            assert impact["initiative_id"] == "a"
            descendants = cast(list[dict[str, object]], impact["descendants"])
            assert [node["initiative_id"] for node in descendants] == [
                "b",
                "c",
            ]
            assert impact["started"] == ["b"]

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/b/redirect",
                _json_body({"brief": "new direction"}),
            )
            assert status == 200
            versions = cast(
                list[dict[str, object]],
                json.loads(body)["initiatives"]["b"]["brief_versions"],
            )
            assert versions[-1]["brief"] == "new direction"

            status, _body = await _request(
                app, "GET", "/plans/p/initiatives/nope/impact"
            )
            assert status == 404

            status, _body = await _request(
                app, "GET", "/plans/missing/initiatives/a/impact"
            )
            assert status == 404
        finally:
            store.close()

    asyncio.run(scenario())


def test_disruptive_routes_preview_impact_before_mutating(tmp_path: Path) -> None:
    """`preview: true` returns the downstream impact and mutates nothing."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(
                daemon,
                gated_spec("a"),
                spec("b", depends_on=["a"], writes=["b/"]),
            )
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            app = create_app(daemon)
            before = len(store.read("p"))
            previews: list[tuple[str, dict[str, object]]] = [
                ("/plans/p/initiatives/a/retry", {"preview": True}),
                (
                    "/plans/p/initiatives/a/redirect",
                    {"brief": "v2 brief", "preview": True},
                ),
                (
                    "/plans/p/initiatives/a/reassign",
                    {"harness": "luna", "model": "big-1", "preview": True},
                ),
            ]
            for path, payload in previews:
                status, body_text = await _request(app, "POST", path, _json_body(payload))
                assert status == 200, (path, body_text)
                impact = cast(
                    dict[str, object],
                    cast(dict[str, object], json.loads(body_text))["impact"],
                )
                assert impact["initiative_id"] == "a"
                descendants = cast(
                    list[dict[str, object]], impact["descendants"]
                )
                assert [node["initiative_id"] for node in descendants] == ["b"]
            # Nothing mutated: the same events, no intervention recorded.
            assert len(store.read("p")) == before
            assert daemon.plan("p").initiatives["a"].assignment_override is None
            assert daemon.plan("p").initiatives["a"].brief_versions == []
        finally:
            store.close()

    asyncio.run(scenario())


def test_checkpoint_review_versions_carry_the_diff_walkthrough(
    tmp_path: Path,
) -> None:
    """Checkpoint review groups each version's changed paths into cohorts."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]))
            _ = await daemon.run_and_settle(
                "p",
                "a",
                runtime=StubRuntime(),
                collector=StubCollector(
                    changed_paths=["herdsman/daemon.py", "README.md"]
                ),
            )
            app = create_app(daemon)

            status, body = await _request(app, "GET", "/plans/p/checkpoints")
            assert status == 200
            versions = cast(
                list[dict[str, object]],
                json.loads(body)["initiatives"][0]["versions"],
            )
            assert versions[0]["walkthrough"] == {
                "cohorts": [
                    {
                        "name": "(root)",
                        "paths": ["README.md"],
                        "summary": "1 file at repository root",
                    },
                    {
                        "name": "daemon core",
                        "paths": ["herdsman/daemon.py"],
                        "summary": "1 file changed; daemon and CLI behavior changed",
                    },
                ],
                "total_files": 2,
            }
        finally:
            store.close()

    asyncio.run(scenario())
