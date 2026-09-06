import asyncio
import json
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
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
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
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
