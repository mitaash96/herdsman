import asyncio
import json
import shlex
import shutil
import sqlite3
from hashlib import sha256
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
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
    CheckpointApproved,
    CheckpointRecorded,
    CheckResult,
    Contract,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    MemoryLeaf,
    MemoryUseRecorded,
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
from herdsman.herdr import PaneEntry, RuntimeInventory, WorktreeEntry
from herdsman.memory import token_count
from herdsman.runtime import CHECKPOINT_MARKER, CompletionError
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
            self,
            plan_id: str,
            attempt_id: str,
            pane_ref: str,
            *,
            match: str | None = None,
        ) -> AsyncIterator[RuntimeObserved]:
            del plan_id, attempt_id, pane_ref, match
            raise AssertionError("the run never reaches observation")

        async def remove_worktree(self, worktree_ref: str) -> None:
            self.removed.append(worktree_ref)

        async def aclose(self) -> None:
            return None

        async def inventory(self) -> RuntimeInventory:
            return RuntimeInventory((), ())

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
    """A one-shot run that emits the completion marker, without herdr.

    Tracks the worktrees and panes it created so `inventory()` reports them
    surviving, as the real adapter would after its own run; the recovery
    tests pre-load `live_worktrees`/`live_panes` to stand in for resources a
    previous daemon left behind.
    """

    def __init__(
        self,
        exit_code: int = 0,
        *,
        live_worktrees: Sequence[str] = (),
        live_panes: Sequence[str] = (),
    ) -> None:
        self.exit_code: int = exit_code
        self.worktrees: list[str] = [*live_worktrees]
        self.panes: list[str] = [*live_panes]

    async def create_worktree(self, branch: str) -> str:
        ref = f"worktree-{branch}"
        self.worktrees.append(ref)
        return ref

    async def worktree_path(self, worktree_ref: str) -> Path:
        del worktree_ref
        return Path(".")

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        del worktree_ref, command, match
        self.panes.append("pane-live")
        return "pane-live"

    async def inventory(self) -> RuntimeInventory:
        worktrees = tuple(
            WorktreeEntry(ref, f"herdsman/{ref}", f"ws-{ref}", True, {})
            for ref in self.worktrees
        )
        panes = tuple(
            PaneEntry(pane, f"ws-{index}")
            for index, pane in enumerate(self.panes)
        )
        return RuntimeInventory(worktrees=worktrees, panes=panes)

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ) -> AsyncIterator[RuntimeObserved]:
        del pane_ref, match
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

    def diagnose(
        self,
        path: Path,
        attempt_id: str,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> str | None:
        del path, attempt_id, base_sha, timeout
        return None

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
        self.interrupts: list[str] = []

    async def nudge_pane(self, pane_ref: str, text: str) -> None:
        self.nudges.append((pane_ref, text))

    async def focus_pane(self, pane_ref: str) -> None:
        self.focused.append(pane_ref)

    async def restart_process(self, pane_ref: str, command: str) -> str:
        self.restarts.append((pane_ref, command))
        return pane_ref

    async def interrupt_pane(self, pane_ref: str) -> None:
        self.interrupts.append(pane_ref)

    async def aclose(self) -> None:
        return None


class RacePane(PaneStub):
    """A pane whose successful delivery races a settlement or failure in flight.

    Real pane writes are awaited: the daemon validates the live attempt,
    yields into the pane write, and only then records. This stub lands the
    supplied settlement or failure inside that await window, so the record
    races the state change exactly as it does against a live herdr.
    """

    def __init__(self, daemon: Daemon, action: Callable[[], object]) -> None:
        super().__init__()
        self.daemon: Daemon = daemon
        self.action: Callable[[], object] = action

    @override
    async def nudge_pane(self, pane_ref: str, text: str) -> None:
        await asyncio.sleep(0)
        _ = self.action()
        await super().nudge_pane(pane_ref, text)

    @override
    async def restart_process(self, pane_ref: str, command: str) -> str:
        await asyncio.sleep(0)
        _ = self.action()
        return await super().restart_process(pane_ref, command)


def _fail_attempt(daemon: Daemon) -> Callable[[], object]:
    """The executor crash that can land while a pane delivery is in flight."""

    def fail() -> Event:
        return daemon.append(
            InitiativeFailed(
                plan_id="p",
                at=datetime.now(UTC),
                initiative_id="a",
                reason="executor crashed",
            )
        )

    return fail


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
            assert (initiative.attempts[0].by, initiative.attempts[0].origin) == (
                "daemon",
                "run",
            )
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


def test_legacy_memory_packet_records_one_measured_receipt(tmp_path: Path) -> None:
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
            daemon.append(
                InitiativeFailed(
                    plan_id="p", at=datetime.now(UTC), initiative_id="a", reason="retry"
                )
            )
            runner = CapturingRuntime()
            _ = await daemon.retry_initiative(
                "p", "a", runtime=runner, collector=StubCollector()
            )
            packet = packet_from_command(runner.commands[-1])
            receipts = [
                event for event in store.read("p") if isinstance(event, MemoryUseRecorded)
            ]
            assert len(receipts) == 1
            receipt = receipts[0]
            assert receipt.operation == "inline"
            assert receipt.tokens == token_count(" ".join(cast(list[str], packet["memory"])))
            assert receipt.leaf_ids == packet["memory_leaf_ids"]
            assert receipt.leaf_versions == packet["memory_leaf_versions"]
        finally:
            store.close()

    asyncio.run(scenario())


def test_packet_memory_keeps_one_run_boundary_for_pull_and_auto_answer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            capability_path = tmp_path / ".herdsman" / "memory.json"
            _ = capability_path.write_text(
                json.dumps({"harnesses": {"luna": "B"}}), encoding="utf-8"
            )
            _ = seed(daemon, spec("base"))
            fact = tmp_path / "fact.txt"
            _ = fact.write_text("fact", encoding="utf-8")
            evidence = f"fact.txt@{sha256(b'fact').hexdigest()}"
            for leaf_id, subject, ttl_runs in (
                ("default", "default-ttl", None),
                ("explicit", "explicit-ttl", 20),
            ):
                daemon.create_memory_leaf(
                    "p",
                    MemoryLeaf(
                        id=leaf_id,
                        subject=subject,
                        claim="use tabs",
                        origin="salvage",
                        at=datetime.now(UTC),
                        evidence=[evidence],
                        ttl_runs=ttl_runs,
                        scope=[],
                        lifetime="project",
                    ),
                )

            # Nineteen persisted attempts put both leaves one run before the
            # expiry boundary.  Separate plans avoid the per-initiative retry
            # ceiling while exercising the project-wide count.
            for index in range(1, 20):
                plan_id = f"prior-{index}"
                prior = spec("prior")
                for event in (
                    PlanCreated(plan_id=plan_id, at=AT, brief="prior"),
                    PlanProposed(
                        plan_id=plan_id, at=AT, version=1, initiatives=[prior]
                    ),
                    PlanApproved(plan_id=plan_id, at=AT, version=1),
                    AttemptStarted(
                        plan_id=plan_id,
                        at=AT,
                        attempt_id=f"prior-attempt-{index}",
                        initiative_id="prior",
                        assignment=prior.assignment,
                    ),
                ):
                    _ = daemon.append(event)

            def append_target(plan_id: str) -> None:
                target = spec("target")
                for event in (
                    PlanCreated(plan_id=plan_id, at=AT, brief="target"),
                    PlanProposed(
                        plan_id=plan_id, at=AT, version=1, initiatives=[target]
                    ),
                    PlanApproved(plan_id=plan_id, at=AT, version=1),
                ):
                    _ = daemon.append(event)

            append_target("boundary")
            runner = CapturingRuntime()
            _ = await daemon.run_initiative(
                "boundary", "target", runtime=runner, collector=StubCollector()
            )
            packet = packet_from_command(runner.commands[-1])
            assert packet["memory_pointers"] == [
                "[explicit@1] use tabs",
                "[default@1] use tabs",
            ]
            attempt_id = daemon.plan("boundary").initiatives["target"].attempts[-1].id
            for subject in ("default-ttl", "explicit-ttl"):
                assert daemon.memory_pull(
                    "boundary", subject=subject, attempt_id=attempt_id
                ) is not None
            assert (
                await daemon.auto_answer(
                    "boundary", attempt_id, "explicit-ttl", runtime=PaneStub()
                )
            ) is not None

            # The next packet sees the persisted boundary attempt and both
            # leaves expire; the earlier attempt's explicit pull still works.
            append_target("later")
            later = CapturingRuntime()
            _ = await daemon.run_initiative(
                "later", "target", runtime=later, collector=StubCollector()
            )
            assert packet_from_command(later.commands[-1])["memory_pointers"] == []
            later_id = daemon.plan("later").initiatives["target"].attempts[-1].id
            assert daemon.memory_pull(
                "later", leaf_id="default", attempt_id=later_id
            ) is None
            assert daemon.memory_pull(
                "boundary", leaf_id="explicit", attempt_id=attempt_id
            ) is not None
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


def test_a_failed_task_reassigns_to_a_second_harness_and_the_retry_launches_it(
    tmp_path: Path,
) -> None:
    """Cross-harness reassignment: configured argv, new attempt, history kept."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        registry = tmp_path / ".herdsman" / "harnesses.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        _ = registry.write_text(
            json.dumps(
                {
                    "pi": {
                        "argv": ["/opt/pi", "--no-session", "--print", "{prompt}"],
                        "model_argv": ["--model"],
                    }
                }
            )
        )
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
            second = Assignment(harness="pi", model="frontier-9")
            _ = daemon.reassign_initiative("p", "a", second, by="lead", reason="try pi")
            runner = CapturingRuntime()
            _ = await daemon.retry_initiative(
                "p", "a", runtime=runner, collector=StubCollector(), by="lead"
            )
            plan = daemon.plan("p")
            initiative = plan.initiatives["a"]
            # The retry launched the configured second-harness argv with the
            # model inserted before the packet prompt.
            argv = shlex.split(runner.commands[-1])
            assert argv[:5] == [
                "/opt/pi", "--no-session", "--print", "--model", "frontier-9",
            ]
            packet = packet_from_command(runner.commands[-1])
            assert packet["assignment"] == {
                "harness": "pi", "model": "frontier-9",
            }
            # A new attempt was reserved, attributed to the retrying actor and
            # marked as a retry; the failed attempt keeps its assignment and
            # recorded evidence, and independent work is undisturbed.
            assert initiative.state == "settled"
            assert [attempt.assignment for attempt in initiative.attempts] == [
                LUNA,
                second,
            ]
            assert initiative.attempts[0].checkpoint is not None
            assert initiative.attempts[0].origin == "run"
            assert (initiative.attempts[1].by, initiative.attempts[1].origin) == (
                "lead",
                "retry",
            )
            started = [
                event for event in store.read("p") if isinstance(event, AttemptStarted)
            ]
            assert (started[-1].by, started[-1].origin) == ("lead", "retry")
            assert started[-1].assignment == second
            assert plan.initiatives["b"].state == "pending"
            assert plan.initiatives["c"].state == "pending"
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


def test_focus_uses_the_most_recent_available_pane(tmp_path: Path) -> None:
    """A newly reserved retry does not hide the previous attempt's pane."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            _ = daemon.append(
                AttemptStarted(
                    plan_id="p",
                    at=AT,
                    attempt_id="retry-pending",
                    initiative_id="a",
                    assignment=LUNA,
                    origin="retry",
                )
            )
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


def test_a_nudge_that_races_settlement_still_keeps_its_record(tmp_path: Path) -> None:
    """A successful delivery begun against the live attempt is never lost.

    The reviewer's approval can land while a nudge is in flight: the pane
    took the message, so its record and its ground-truth leaf must fold even
    though the attempt is no longer live by append time. An intervention
    initiated after the window closed is still refused, and replay folds the
    raced record the same way.
    """

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert checkpoint is not None
            pane = RacePane(
                daemon,
                lambda: daemon.approve_checkpoint("p", checkpoint.id, by="reviewer"),
            )
            _ = await daemon.nudge_initiative(
                "p",
                "a",
                "focus on the parser first",
                by="lead",
                ground_truth=True,
                runtime=pane,
            )
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "settled"
            # The delivered nudge is recorded against the attempt it reached.
            nudged = [event for event in store.read("p") if isinstance(event, TaskNudged)]
            assert nudged[-1].attempt_id == plan.initiatives["a"].attempts[-1].id
            assert (plan.memory_leaves[-1].subject, plan.memory_leaves[-1].claim) == (
                "a.nudge",
                "focus on the parser first",
            )
            # Replay folds the raced record identically.
            replay = EventStore(tmp_path / ".herdsman" / "events.db")
            try:
                assert replay.load("p").memory_leaves[-1].subject == "a.nudge"
            finally:
                replay.close()
            # An intervention first initiated after the window closed is refused.
            before = len(store.read("p"))
            with pytest.raises(ValueError, match="only a running task has a live pane"):
                _ = await daemon.nudge_initiative("p", "a", "too late", runtime=PaneStub())
            assert len(store.read("p")) == before
        finally:
            store.close()

    asyncio.run(scenario())


def test_answers_that_race_failure_still_keep_their_record(tmp_path: Path) -> None:
    """The executor can die while an answer is in flight; the delivery stands.

    An operator answer delivered to the agent before the crash still projects
    its leaf, and an auto-answer racing the same crash still folds its
    attributable nudge -- failed attempts, not failed deliveries.
    """

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            attempt_id = daemon.plan("p").initiatives["a"].attempts[-1].id
            pane = RacePane(daemon, _fail_attempt(daemon))
            _ = await daemon.operator_answer(
                "p", attempt_id, "tabs-or-spaces", "tabs", by="reviewer", runtime=pane
            )
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "failed"
            assert pane.nudges == [("pane-live", "[tabs-or-spaces] tabs")]
            leaf = plan.memory_leaves[-1]
            assert (leaf.subject, leaf.origin, leaf.by) == (
                "tabs-or-spaces",
                "operator-answer",
                "reviewer",
            )
            assert [
                event for event in store.read("p") if isinstance(event, OperatorAnswered)
            ]

            # A repeat request on the new attempt auto-answers from that leaf;
            # the crash that lands mid-write does not drop the record either.
            _ = await daemon.retry_initiative(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            pane = RacePane(daemon, _fail_attempt(daemon))
            answered = await daemon.auto_answer(
                "p",
                daemon.plan("p").initiatives["a"].attempts[-1].id,
                "tabs-or-spaces",
                runtime=pane,
            )
            assert answered is not None and answered.id == leaf.id
            assert daemon.plan("p").initiatives["a"].state == "failed"
            delivered = [
                event for event in store.read("p") if isinstance(event, TaskNudged)
            ][-1]
            assert (delivered.by, delivered.ground_truth) == (f"daemon:{leaf.id}", False)
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_restart_that_races_settlement_still_keeps_its_record(
    tmp_path: Path,
) -> None:
    """A restart delivered before the settlement keeps its attributable event."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, gated_spec("a"))
            checkpoint = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            assert checkpoint is not None
            pane = RacePane(
                daemon,
                lambda: daemon.approve_checkpoint("p", checkpoint.id, by="reviewer"),
            )
            assert (
                await daemon.restart_process("p", "a", by="lead", runtime=pane)
                == "pane-live"
            )
            plan = daemon.plan("p")
            assert plan.initiatives["a"].state == "settled"
            restarted = [
                event for event in store.read("p") if isinstance(event, ProcessRestarted)
            ]
            assert [(event.attempt_id, event.by) for event in restarted] == [
                (plan.initiatives["a"].attempts[-1].id, "lead")
            ]
            # A restart initiated after the window closed is refused.
            before = len(store.read("p"))
            with pytest.raises(ValueError, match="only a running task has a live pane"):
                _ = await daemon.restart_process("p", "a", runtime=PaneStub())
            assert len(store.read("p")) == before
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
            assert (initiative.attempts[-1].by, initiative.attempts[-1].origin) == (
                "operator",
                "retry",
            )

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


# --- Sprint 5: durable recovery ------------------------------------------------


def stale_running(daemon: Daemon, initiative_id: str) -> str:
    """Append the events a daemon death leaves behind: a running attempt."""
    attempt_id = f"attempt_{uuid4().hex}"
    for event in [
        AttemptStarted(
            plan_id="p",
            at=datetime.now(UTC),
            attempt_id=attempt_id,
            initiative_id=initiative_id,
            assignment=LUNA,
            by="daemon",
        ),
        AttemptProvisioned(
            plan_id="p",
            at=datetime.now(UTC),
            attempt_id=attempt_id,
            worktree_ref=f"worktree-herdsman/p/{initiative_id}/{attempt_id}",
            pane_ref=f"pane-{initiative_id}",
            base_sha="base-sha",
        ),
    ]:
        _ = daemon.append(event)
    return attempt_id


def test_daemon_death_reconciles_without_repeating_completed_work(
    tmp_path: Path,
) -> None:
    """Restart/recovery DAG: settled work replays untouched; a stale attempt
    reattaches to its surviving pane and finishes under the one policy."""
    worktree = "worktree-herdsman/p/b/stale"

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a", writes=["a/"]), spec("b", depends_on=["a"]))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
            events_before = len(store.read("p"))
            _ = stale_running(daemon, "b")
            stale_events = len(store.read("p")) - events_before

            # The daemon dies here. A new daemon on the same store rebuilds
            # the fold ; the settled node has no new work to do.
            reopened = Daemon(store, project_root=tmp_path)
            report = reopened.recovery_report("p")
            assert [entry.initiative_id for entry in report.stale] == ["b"]
            runtime = StubRuntime(live_worktrees=[worktree], live_panes=["pane-b"])

            resumed = await reopened.resume_plan(
                "p", runtime=runtime, collector=StubCollector()
            )
            assert resumed.outcomes == {"b": "reattached"}

            events = store.read("p")
            initiative = reopened.plan("p").initiatives["b"]
            assert initiative.state == "settled"
            # No new attempt started anywhere: reattach, not a rerun. The
            # observation record, the checkpoint, and the settlement are the
            # only events recovery added.
            assert [event.type for event in events[-3:]] == [
                "runtime_observed",
                "checkpoint_recorded",
                "initiative_settled",
            ]
            attempts_a = reopened.plan("p").initiatives["a"].attempts
            assert len(attempts_a) == 1
            # Deterministic and safe to repeat: a second resume writes nothing.
            again = await reopened.resume_plan("p", runtime=runtime)
            assert again.stale == [] and again.outcomes == {}
            assert len(store.read("p")) == events_before + stale_events + 3
        finally:
            store.close()

    asyncio.run(scenario())


def test_resume_closes_a_missing_pane_with_a_fixed_failure(tmp_path: Path) -> None:
    """A pane herdr no longer has is a typed failure, not a silent rerun."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            _ = stale_running(daemon, "a")
            reopened = Daemon(store, project_root=tmp_path)
            # Empty inventory: the pane is gone.
            resumed = await reopened.resume_plan("p", runtime=StubRuntime())
            assert resumed.outcomes == {"a": "failed"}
            initiative = reopened.plan("p").initiatives["a"]
            assert initiative.state == "failed"
            assert initiative.failures[-1].reason == (
                "daemon death: pane pane-a missing"
            )
            # The retry path applies unchanged, and admission is what stops it.
            runtime = StubRuntime()
            checkpoint = await reopened.retry_initiative(
                "p", "a", runtime=runtime, collector=StubCollector()
            )
            assert checkpoint is not None
            assert reopened.plan("p").initiatives["a"].state == "settled"
        finally:
            store.close()

    asyncio.run(scenario())


def test_resume_refuses_when_herdr_is_unreachable_and_assume_missing_closes(
    tmp_path: Path,
) -> None:
    """No classification without herdr; the operator's flag closes anyway."""

    class UnavailableRuntime(StubRuntime):
        @override
        async def inventory(self) -> RuntimeInventory:
            raise RuntimeError("herdr socket read failed")

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            _ = stale_running(daemon, "a")
            reopened = Daemon(store, project_root=tmp_path)
            with pytest.raises(RuntimeError, match="herdr socket read failed"):
                _ = await reopened.resume_plan("p", runtime=UnavailableRuntime())
            assert reopened.plan("p").initiatives["a"].state == "running"
            resumed = await reopened.resume_plan(
                "p", runtime=UnavailableRuntime(), assume_missing=True
            )
            assert resumed.outcomes == {"a": "failed"}
            assert reopened.plan("p").initiatives["a"].state == "failed"
        finally:
            store.close()

    asyncio.run(scenario())


def checkpoint_for(attempt_id: str) -> Checkpoint:
    """Clean recorded evidence standing for what the agent finished."""
    return Checkpoint(
        id=f"cp_{uuid4().hex}",
        attempt_id=attempt_id,
        changed_paths=["src/touched.py"],
        base_sha="base-sha",
        head_sha="head-sha",
        checks=[CheckResult(name="true", passed=True)],
        exit_code=0,
        patch_path=f".herdsman/artifacts/{attempt_id}.patch",
    )


def test_resume_recovers_a_paused_task_whose_attempt_is_still_live(
    tmp_path: Path,
) -> None:
    """Pause holds the scheduler, not the agent: after a daemon death the
    live attempt is stale exactly like a running one. A paused task with no
    live attempt — before any attempt, or after a failure — is not stale."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"), spec("b"), spec("c"))
            attempt_a = stale_running(daemon, "a")
            _ = daemon.pause_initiative("p", "a")  # the attempt keeps running
            _ = stale_running(daemon, "b")
            _ = daemon.append(
                InitiativeFailed(
                    plan_id="p",
                    at=datetime.now(UTC),
                    initiative_id="b",
                    reason="boom",
                )
            )
            _ = daemon.pause_initiative("p", "b")  # live window already closed
            _ = daemon.pause_initiative("p", "c")  # paused before any attempt

            reopened = Daemon(store, project_root=tmp_path)
            report = reopened.recovery_report("p")
            assert [entry.initiative_id for entry in report.stale] == ["a"]

            runtime = StubRuntime(
                live_worktrees=[f"worktree-herdsman/p/a/{attempt_a}"],
                live_panes=["pane-a"],
            )
            resumed = await reopened.resume_plan(
                "p", runtime=runtime, collector=StubCollector()
            )
            assert resumed.outcomes == {"a": "reattached"}
            assert reopened.plan("p").initiatives["a"].state == "settled"
            assert resumed.orphaned_panes == []
            # Repeat-safe: a second resume writes nothing.
            again = await reopened.resume_plan("p", runtime=runtime)
            assert again.stale == [] and again.outcomes == {}
        finally:
            store.close()

    asyncio.run(scenario())


def test_resume_continues_a_checkpoint_that_landed_before_the_crash(
    tmp_path: Path,
) -> None:
    """A CheckpointRecorded that survived the crash is collected work:
    recovery never re-observes the attempt, never closes its pane as
    missing, and mechanically finishes the interrupted policy — settlement
    for clean evidence, or the settle an approval had already started."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"), spec("b"))
            attempt_a = stale_running(daemon, "a")
            _ = daemon.append(
                CheckpointRecorded(
                    plan_id="p",
                    at=datetime.now(UTC),
                    checkpoint=checkpoint_for(attempt_a),
                )
            )
            attempt_b = stale_running(daemon, "b")
            approved = checkpoint_for(attempt_b)
            _ = daemon.append(
                CheckpointRecorded(
                    plan_id="p", at=datetime.now(UTC), checkpoint=approved
                )
            )
            # The approval landed; the crash hit before its settlement did.
            _ = daemon.append(
                CheckpointApproved(
                    plan_id="p", at=datetime.now(UTC), checkpoint_id=approved.id
                )
            )

            reopened = Daemon(store, project_root=tmp_path)
            report = reopened.recovery_report("p")
            assert [entry.initiative_id for entry in report.stale] == ["a", "b"]
            # Empty inventory: both panes are gone, and that must not matter.
            resumed = await reopened.resume_plan("p", runtime=StubRuntime())
            assert resumed.outcomes == {"a": "settled", "b": "settled"}
            plan = reopened.plan("p")
            assert plan.initiatives["a"].state == "settled"
            assert plan.initiatives["b"].state == "settled"
            # No observation, no second checkpoint: only the two settlements.
            types = [event.type for event in store.read("p")]
            assert types[-2:] == ["initiative_settled", "initiative_settled"]
            # Repeat-safe: nothing left to reconcile.
            again = await reopened.resume_plan("p", runtime=StubRuntime())
            assert again.stale == [] and again.outcomes == {}
        finally:
            store.close()

    asyncio.run(scenario())


def test_resume_leaves_approval_required_evidence_pending_review(
    tmp_path: Path,
) -> None:
    """Approval-required evidence recorded before a crash stays pending
    review: recovery writes nothing, and the reviewer's approval settles."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a").model_copy(update={"approval": "required"}))
            attempt_a = stale_running(daemon, "a")
            checkpoint = checkpoint_for(attempt_a)
            _ = daemon.append(
                CheckpointRecorded(
                    plan_id="p", at=datetime.now(UTC), checkpoint=checkpoint
                )
            )
            _ = daemon.pause_initiative("p", "a")

            reopened = Daemon(store, project_root=tmp_path)
            events_before = len(store.read("p"))
            resumed = await reopened.resume_plan("p", runtime=StubRuntime())
            assert resumed.outcomes == {"a": "review-pending"}
            assert reopened.plan("p").initiatives["a"].state == "paused"
            assert store.read("p")[-1].type == "initiative_paused"
            # Repeat-safe: still listed, still unwritten, until review decides.
            again = await reopened.resume_plan("p", runtime=StubRuntime())
            assert again.outcomes == {"a": "review-pending"}
            assert len(store.read("p")) == events_before
            # The reviewer finishes what recovery left pending.
            _ = reopened.approve_checkpoint("p", checkpoint.id, action_id="act-ok")
            assert reopened.plan("p").initiatives["a"].state == "settled"
            final = await reopened.resume_plan("p", runtime=StubRuntime())
            assert final.stale == [] and final.outcomes == {}
        finally:
            store.close()

    asyncio.run(scenario())


def test_recovery_actions_are_idempotent(tmp_path: Path) -> None:
    """A repeated action_id returns the recorded outcome; nothing double-applies."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            assert daemon.plan("p").initiatives["a"].state == "failed"
            events_before = len(store.read("p"))

            # Pause a failed task, repeat the same request: one event only.
            _ = daemon.pause_initiative(
                "p", "a", action_id="act-pause", reason="hold"
            )
            paused = daemon.pause_initiative("p", "a", action_id="act-pause")
            assert daemon.plan("p").initiatives["a"].state == "paused"
            assert len(store.read("p")) == events_before + 1
            assert paused is not None

            # Unpause, then retry with the same action_id twice: one run.
            _ = daemon.unpause_initiative("p", "a", action_id="act-resume")
            capturing = CapturingRuntime()
            first = await daemon.retry_initiative(
                "p",
                "a",
                runtime=capturing,
                collector=StubCollector(),
                action_id="act-retry",
            )
            repeated = await daemon.retry_initiative(
                "p",
                "a",
                runtime=capturing,
                collector=StubCollector(),
                action_id="act-retry",
            )
            assert first is not None and repeated is not None
            assert len(capturing.commands) == 1
            initiative = daemon.plan("p").initiatives["a"]
            assert len(initiative.attempts) == 2
            assert set(daemon.plan("p").action_ids) == {
                "act-pause",
                "act-resume",
                "act-retry",
            }
        finally:
            store.close()

    asyncio.run(scenario())


def test_a_reused_action_id_conflicts_instead_of_succeeding(tmp_path: Path) -> None:
    """An action_id is bound to the request it recorded: the same identity
    returns the prior outcome; a different action, target, or payload on a
    reused key is a conflict that writes nothing."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"), spec("b"))
            _ = await daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            checkpoint = daemon.plan("p").initiatives["a"].latest_checkpoint
            assert checkpoint is not None
            _ = await daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
            other = daemon.plan("p").initiatives["b"].latest_checkpoint
            assert other is not None

            # The same identity repeats: the prior outcome, one event — even
            # when a repeat omits the attribution prose.
            _ = daemon.pause_initiative(
                "p", "a", by="op", reason="hold", action_id="act-1"
            )
            events = len(store.read("p"))
            _ = daemon.pause_initiative(
                "p", "a", by="op", reason="changed", action_id="act-1"
            )
            assert len(store.read("p")) == events

            # A reused key over anything else is a conflict, never a silent
            # success: different target, action, or review payload.
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.pause_initiative("p", "b", action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.unpause_initiative("p", "a", action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = await daemon.cancel_initiative("p", "a", action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = await daemon.retry_initiative("p", "a", action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.approve_checkpoint("p", checkpoint.id, action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.reject_checkpoint("p", checkpoint.id, action_id="act-1")
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.request_changes("p", checkpoint.id, action_id="act-1")
            assert len(store.read("p")) == events

            # A fresh key on the review surface still works (+ its settlement),
            # the same identity repeats to the prior outcome, and reusing it
            # for a different checkpoint of the same action is a conflict.
            _ = daemon.approve_checkpoint("p", other.id, action_id="act-2")
            assert (
                daemon.plan("p").initiatives["b"].checkpoint_decisions[
                    other.id
                ].state
                == "approved"
            )
            _ = daemon.approve_checkpoint("p", other.id, action_id="act-2")
            with pytest.raises(ValueError, match="already recorded"):
                _ = daemon.approve_checkpoint("p", checkpoint.id, action_id="act-2")
            assert len(store.read("p")) == events + 2
        finally:
            store.close()

    asyncio.run(scenario())


def test_retry_packet_carries_the_bounded_failure_delta(tmp_path: Path) -> None:
    """The retry packet carries the original contract plus the last failure's
    bounded delta — never the failed attempt's transcript."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            transcript = "transcript line: " + "x" * 5000
            runtime = StubRuntime(exit_code=1)
            _ = await daemon.run_and_settle(
                "p", "a", runtime=runtime, collector=StubCollector()
            )
            failed = daemon.plan("p").initiatives["a"]
            assert failed.state == "failed"

            capturing = CapturingRuntime(exit_code=0)
            _ = await daemon.retry_initiative(
                "p", "a", runtime=capturing, collector=StubCollector()
            )
            packet = packet_from_command(capturing.commands[-1])
            assert packet["brief"] == "implement a"
            failures = cast("list[str]", packet["failures"])
            # The failed check (normalized) and the settlement reason, one line each.
            assert any(
                line.startswith("[attempt_") and "checkpoint" in line
                for line in failures
            )
            assert all(len(line) <= 400 for line in failures)
            assert transcript not in json.dumps(packet)
        finally:
            store.close()

    asyncio.run(scenario())


def test_repeated_identical_failure_stops_admission_after_the_leaf_carrying_attempt(
    tmp_path: Path,
) -> None:
    """The promoted leaf reaches the next attempt; after it fails identically,
    mechanical retries stop with a typed refusal."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            runtime = StubRuntime(exit_code=1)
            # Attempt 1 fails, attempt 2 fails identically: the signature hits
            # the promotion limit and the fold promotes exactly one leaf.
            _ = await daemon.run_and_settle(
                "p", "a", runtime=runtime, collector=StubCollector()
            )
            _ = await daemon.run_and_settle(
                "p", "a", runtime=runtime, collector=StubCollector(), origin="retry"
            )
            plan = daemon.plan("p")
            signature = next(
                record
                for (owner, _check, _error), record in plan.failure_signatures.items()
                if owner == "a"
            )
            assert signature.count == 2
            assert [
                leaf.origin
                for leaf in plan.memory_leaves
                if leaf.subject == "a.failure"
            ] == ["failure"]
            # The leaf-carrying third attempt is still admitted.
            capturing = CapturingRuntime(exit_code=1)
            _ = await daemon.retry_initiative(
                "p", "a", runtime=capturing, collector=StubCollector()
            )
            assert len(daemon.plan("p").initiatives["a"].attempts) == 3
            # The leaf carried; the identical failure came back; retry stops.
            with pytest.raises(ValueError, match="repeated-failure stopping"):
                _ = await daemon.run_and_settle(
                    "p", "a", runtime=runtime, collector=StubCollector(), origin="retry"
                )
        finally:
            store.close()

    asyncio.run(scenario())


def test_cancel_interrupts_a_live_agent_and_is_terminal(tmp_path: Path) -> None:
    """Cancel stops the tracked run, interrupts the pane once, and the task
    can never be settled, retried, or cancelled again."""

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))

            class HangingRuntime(StubRuntime):
                @override
                async def observe_events(
                    self,
                    plan_id: str,
                    attempt_id: str,
                    pane_ref: str,
                    *,
                    match: str | None = None,
                ) -> AsyncIterator[RuntimeObserved]:
                    _ = await asyncio.Event().wait()
                    yield RuntimeObserved(  # pragma: no cover — cancelled first
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                        attempt_id=attempt_id,
                        kind="pane_exited",
                        detail={"pane_id": pane_ref},
                    )

            pane_stub = PaneStub()
            run_task = asyncio.create_task(
                daemon.run_and_settle(
                    "p", "a", runtime=HangingRuntime(), collector=StubCollector()
                )
            )
            while ("p", "a") not in daemon._run_tasks:  # pyright: ignore[reportPrivateUsage]
                await asyncio.sleep(0)
            plan = await daemon.cancel_initiative(
                "p", "a", by="lead", runtime=pane_stub, reason="wrong direction"
            )
            _ = await asyncio.gather(run_task, return_exceptions=True)
            initiative = plan.initiatives["a"]
            assert initiative.state == "cancelled"
            assert pane_stub.interrupts == [initiative.attempts[-1].pane_ref]
            # The failure record the cancelled run wrote precedes the cancel.
            types = [event.type for event in store.read("p")]
            assert types[-2:] == ["initiative_failed", "initiative_cancelled"]
            with pytest.raises(ValueError, match="cannot be cancelled"):
                _ = await daemon.cancel_initiative("p", "a", runtime=pane_stub)
        finally:
            store.close()

    asyncio.run(scenario())


class DiagnosingCollector(StubCollector):
    """A collector whose diagnostic writes the artifact it points at."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root: Path = root

    @override
    def diagnose(
        self,
        path: Path,
        attempt_id: str,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> str | None:
        del path, base_sha, timeout
        relative = Path(".herdsman") / "artifacts" / f"{attempt_id}.diag.patch"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text("diff --git a b")
        return str(relative)


def test_a_pre_collection_failure_preserves_its_diagnostic_patch(
    tmp_path: Path,
) -> None:
    """The raw diff is written and referenced on the failure event before any
    cleanup, so salvage and discard keep a stable pointer to it."""

    class ExplodingRuntime(StubRuntime):
        @override
        async def observe_events(
            self,
            plan_id: str,
            attempt_id: str,
            pane_ref: str,
            *,
            match: str | None = None,
        ) -> AsyncIterator[RuntimeObserved]:
            raise CompletionError("pane died before any marker")
            yield RuntimeObserved(  # pyright: ignore[reportUnreachable]
                plan_id=plan_id,
                at=datetime.now(UTC),
                attempt_id=attempt_id,
                kind="pane_exited",
                detail={"pane_id": pane_ref},
            )

    async def scenario() -> None:
        store, daemon = local_daemon(tmp_path)
        try:
            _ = seed(daemon, spec("a"))
            with pytest.raises(CompletionError):
                _ = await daemon.run_initiative(
                    "p",
                    "a",
                    runtime=ExplodingRuntime(),
                    collector=DiagnosingCollector(tmp_path),
                )
            initiative = daemon.plan("p").initiatives["a"]
            assert initiative.state == "failed"
            failure = initiative.failures[-1]
            assert failure.evidence, "the diagnostic path must be referenced"
            for relative in failure.evidence:
                assert (tmp_path / relative).is_file()
        finally:
            store.close()

    asyncio.run(scenario())


def test_intervention_and_recovery_routes_wire_requests_and_responses(
    tmp_path: Path,
) -> None:
    """Pause, unpause, cancel, the recovery report, and resume over the API:
    request bodies (including action_id) reach the daemon, responses project
    the fold, and a repeated action_id returns the recorded outcome instead
    of appending a second event."""

    async def scenario() -> None:
        store = EventStore(tmp_path / "events.db")
        daemon = Daemon(store)
        try:
            _ = seed(daemon, spec("a"))
            app = create_app(daemon)

            pause = json.dumps(
                {"by": "op", "reason": "hold", "action_id": "pause-1"}
            ).encode()
            status, body = await _request(
                app, "POST", "/plans/p/initiatives/a/pause", pause
            )
            assert status == 200
            assert json.loads(body)["initiatives"]["a"]["state"] == "paused"
            events_after_pause = len(store.read("p"))
            status, body = await _request(
                app, "POST", "/plans/p/initiatives/a/pause", pause
            )
            assert status == 200
            assert json.loads(body)["initiatives"]["a"]["state"] == "paused"
            assert len(store.read("p")) == events_after_pause

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/unpause",
                json.dumps({"by": "op", "action_id": "unpause-1"}).encode(),
            )
            assert status == 200
            assert json.loads(body)["initiatives"]["a"]["state"] == "pending"
            status, _ = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/unpause",
                json.dumps({"action_id": "unpause-1"}).encode(),
            )
            assert status == 200

            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/cancel",
                json.dumps(
                    {
                        "by": "op",
                        "reason": "wrong direction",
                        "action_id": "cancel-1",
                    }
                ).encode(),
            )
            assert status == 200
            assert json.loads(body)["initiatives"]["a"]["state"] == "cancelled"
            events_after_cancel = len(store.read("p"))
            status, _ = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/cancel",
                json.dumps({"action_id": "cancel-1"}).encode(),
            )
            assert status == 200
            assert len(store.read("p")) == events_after_cancel
            status, body = await _request(
                app,
                "POST",
                "/plans/p/initiatives/a/cancel",
                json.dumps({"action_id": "cancel-2"}).encode(),
            )
            assert status == 409
            assert "cannot be cancelled" in json.loads(body)["detail"]

            status, body = await _request(app, "GET", "/plans/p/recovery")
            assert status == 200
            report = cast(dict[str, object], json.loads(body))
            assert report["plan_id"] == "p"
            assert report["stale"] == []
            assert report["outcomes"] == {}
            status, _ = await _request(app, "GET", "/plans/missing/recovery")
            assert status == 404

            # Resume reconciles only: nothing stale means nothing written, and
            # the route accepts the assume_missing/timeout body untouched.
            status, body = await _request(
                app,
                "POST",
                "/plans/p/resume",
                json.dumps({"assume_missing": True, "timeout": 5.0}).encode(),
            )
            assert status == 200
            report = cast(dict[str, object], json.loads(body))
            assert report["plan_id"] == "p"
            assert report["stale"] == [] and report["outcomes"] == {}
            status, _ = await _request(app, "POST", "/plans/missing/resume", b"{}")
            assert status == 404
        finally:
            store.close()

    asyncio.run(scenario())
