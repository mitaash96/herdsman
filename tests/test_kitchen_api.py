"""Sprint 8 daemon integration: Kitchen selection and HTTP projections."""

import asyncio
import json
import shlex
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from starlette.types import Message, Scope

from herdsman.classes import Assignment, Checkpoint, InitiativeSpec, RuntimeObserved, Usage
from herdsman.daemon import (
    SMOKE_CLEARED, SMOKE_NEVER_RUN, Daemon, _merge_kitchen_templates, create_app,
)
from herdsman.discovery import ProbeResult
from herdsman.effort import effective_effort
from herdsman.herdr import RuntimeInventory
from herdsman.kitchen import Adapter, Kitchen
from herdsman.runtime import (
    SMOKE_MARKER,
    SMOKE_PROMPT,
    SmokeProcess,
    SmokeRunner,
    compile_task_packet,
    executor_command,
)
from herdsman.store import EventStore


AT = datetime(2026, 9, 13, tzinfo=UTC)


def write_kitchen(root: Path, binary: str, *, missing: str | None = None) -> None:
    adapters = [
        {
            "name": "frontier",
            "argv": [binary, "--print", "{prompt}"],
            "model_argv": ["--model"],
        },
        {
            "name": "executor",
            "argv": [missing or binary, "--print", "{prompt}"],
            "model_argv": ["--model"],
        },
    ]
    payload = {
        "version": 1,
        "adapters": adapters,
        "models": [
            {"harness": "frontier", "model": "frontier-model"},
            {"harness": "executor", "model": "cheap-model"},
        ],
        "tiers": {
            "frontier/frontier-model": "frontier",
            "executor/cheap-model": "cheap",
        },
        "defaults": {
            "planner": {"harness": "frontier", "model": "frontier-model"},
            "initiative": {"harness": "executor", "model": "cheap-model"},
        },
    }
    directory = root / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "kitchen.json").write_text(json.dumps(payload), encoding="utf-8")


async def request(
    app: FastAPI, method: str, path: str, body: object | None = None
) -> tuple[int, dict[str, object]]:
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
    return status, cast(dict[str, object], json.loads(response_body))


def executable(root: Path) -> Path:
    path = root / "bin" / "harness"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("#!/bin/sh\n", encoding="utf-8")
    _ = path.chmod(0o755)
    return path


def test_kitchen_api_projects_explicit_readiness_and_catalog_without_probing(
    tmp_path: Path,
) -> None:
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary), missing=str(tmp_path / "absent"))
    calls: list[list[str]] = []

    def runner(argv: Sequence[str], timeout: float) -> ProbeResult:
        calls.append(list(argv))
        assert timeout == 3
        return ProbeResult(returncode=0, stdout="frontier 1.0\n")

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path, discovery_runner=runner)

    async def scenario() -> None:
        app = create_app(daemon)
        status, before = await request(app, "GET", "/kitchen")
        assert status == 200
        assert before["configured"] is True
        readiness = {
            str(item["harness"]): item
            for item in cast(list[dict[str, object]], before["readiness"])
        }
        assert readiness["frontier"]["state"] == "unknown"
        assert readiness["executor"]["state"] == "unknown"
        assert len(cast(list[object], before["models"])) == 2
        assert cast(dict[str, object], before["discovery"])["facts"] == []

        kitchen_before = (tmp_path / ".herdsman" / "kitchen.json").read_bytes()
        status, after = await request(
            app, "POST", "/kitchen/discovery", {"timeout": 3}
        )
        assert status == 200
        assert calls == [[str(binary), "--version"]]
        readiness = {
            str(item["harness"]): item
            for item in cast(list[dict[str, object]], after["readiness"])
        }
        assert readiness["frontier"]["state"] == "ready"
        assert readiness["executor"]["state"] == "unavailable"
        facts = cast(dict[str, object], after["discovery"])["facts"]
        assert cast(list[dict[str, object]], facts)[0]["version"] == "frontier 1.0"
        assert (tmp_path / ".herdsman" / "kitchen.json").read_bytes() == kitchen_before

    try:
        asyncio.run(scenario())
    finally:
        store.close()


class PlannerProcess:
    returncode: int = 0

    def __init__(self, output: bytes) -> None:
        self.output: bytes = output

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.output, b""

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return self.returncode


def test_daemon_uses_project_planner_and_executor_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))
    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **kwargs: object) -> PlannerProcess:
        del kwargs
        calls.append(list(argv))
        return PlannerProcess(
            b'{"initiatives":[{"id":"one","name":"one","brief":"ship"}]}'
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    async def scenario() -> None:
        plan = await daemon.create_plan("ship it", plan_id="plan")
        assert plan.planner == Assignment(harness="frontier", model="frontier-model")
        assert plan.initiatives["one"].spec.assignment == Assignment(
            harness="executor", model="cheap-model"
        )
        assert calls[0][0] == str(binary)
        assert calls[0][2:4] == ["--model", "frontier-model"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_api_refuses_stale_save_and_writes_only_canonical_file(
    tmp_path: Path,
) -> None:
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))

    def runner(argv: Sequence[str], timeout: float) -> ProbeResult:
        del argv
        assert timeout == 3
        return ProbeResult(returncode=0, stdout="old-version\n")

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path, discovery_runner=runner)

    async def scenario() -> None:
        from herdsman.daemon import create_app

        app = create_app(daemon)
        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        stale_revision = str(current["revision"])
        status, discovered = await request(
            app, "POST", "/kitchen/discovery", {"timeout": 3}
        )
        assert status == 200
        readiness = {
            str(item["harness"]): item
            for item in cast(list[dict[str, object]], discovered["readiness"])
        }
        assert readiness["executor"]["state"] == "ready"
        assert readiness["executor"]["version"] == "old-version"
        loaded = Kitchen.load(tmp_path)
        _ = loaded.model_copy(update={"frontier_tiers": ["frontier", "premium"]}).save(
            tmp_path
        )
        replacement = loaded.model_copy(
            update={
                "frontier_tiers": ["frontier", "other"],
                "adapters": [
                    adapter.model_copy(
                        update=(
                            {
                                "argv": [
                                    str(tmp_path / "bin" / "new-harness"),
                                    "--print",
                                    "{prompt}",
                                ]
                            }
                            if adapter.name == "executor"
                            else {}
                        )
                    )
                    for adapter in loaded.adapters
                ],
            }
        )
        status, body = await request(
            app,
            "PUT",
            "/kitchen",
            {"kitchen": replacement.model_dump(mode="json"), "expect_revision": stale_revision},
        )
        assert status == 409
        assert "changed since it was read" in str(body["detail"])
        status, rejected = await request(app, "GET", "/kitchen")
        assert status == 200
        rejected_readiness = {
            str(item["harness"]): item
            for item in cast(list[dict[str, object]], rejected["readiness"])
        }
        assert rejected_readiness["executor"]["state"] == "ready"
        assert rejected_readiness["executor"]["version"] == "old-version"
        assert cast(dict[str, object], rejected["discovery"])["facts"]

        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        revision = str(current["revision"])
        before = {
            path.relative_to(tmp_path).as_posix(): path.read_bytes()
            for path in (tmp_path / ".herdsman").iterdir()
            if path.name != "kitchen.json"
        }
        status, saved = await request(
            app,
            "PUT",
            "/kitchen",
            {"kitchen": replacement.model_dump(mode="json"), "expect_revision": revision},
        )
        assert status == 200
        assert saved["revision"] != revision
        assert cast(list[str], saved["frontier_tiers"]) == ["frontier", "other"]
        assert cast(dict[str, object], saved["discovery"])["facts"] == []
        readiness = {
            str(item["harness"]): item
            for item in cast(list[dict[str, object]], saved["readiness"])
        }
        assert readiness["executor"]["state"] == "unknown"
        assert readiness["executor"].get("version") is None
        assert {
            path.relative_to(tmp_path).as_posix(): path.read_bytes()
            for path in (tmp_path / ".herdsman").iterdir()
            if path.name != "kitchen.json"
        } == before
        assert json.loads(
            (tmp_path / ".herdsman" / "kitchen.json").read_text(encoding="utf-8")
        )["frontier_tiers"] == ["frontier", "other"]

    try:
        asyncio.run(scenario())
    finally:
        store.close()


class Planner:
    async def propose(self, brief: str) -> object:
        return {
            "initiatives": [
                {
                    "id": "one",
                    "name": "one",
                    "brief": brief,
                    "routes": {"writes": ["one/"]},
                }
            ]
        }


class Runtime:
    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self.commands: list[str] = []

    async def create_worktree(self, branch: str) -> str:
        del branch
        return "worktree"

    async def worktree_path(self, worktree_ref: str) -> Path:
        del worktree_ref
        return self.root

    async def run(self, worktree_ref: str, command: str, *, match: str | None = None) -> str:
        del worktree_ref, match
        self.commands.append(command)
        return "pane"

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ) -> AsyncIterator[RuntimeObserved]:
        del pane_ref, match
        yield RuntimeObserved(
            plan_id=plan_id,
            at=AT,
            attempt_id=attempt_id,
            kind="pane_output_matched",
            detail={
                "text": (
                    'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'
                    '{"input_tokens":1,"output_tokens":1,"source":"harness"}}'
                )
            },
        )

    async def remove_worktree(self, worktree_ref: str) -> None:
        del worktree_ref
        return None

    async def aclose(self) -> None:
        return None

    async def inventory(self) -> RuntimeInventory:
        return RuntimeInventory((), ())


class Collector:
    def capture_base(self, path: Path, *, inputs: Sequence[Path] = (), timeout: float | None = None) -> str:
        del path, inputs, timeout
        return "base"

    def diagnose(self, path: Path, attempt_id: str, *, base_sha: str, timeout: float | None = None) -> str | None:
        del path, attempt_id, base_sha, timeout
        return None

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: object,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        del path, completion, base_sha, timeout
        return Checkpoint(
            id="checkpoint",
            attempt_id=attempt_id,
            exit_code=0,
            usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
        )


def test_configured_planner_and_executor_assignments_are_snapshotted_separately(
    tmp_path: Path,
) -> None:
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    runtime = Runtime(tmp_path)

    async def scenario() -> None:
        plan = await daemon.create_plan("ship it", planner=Planner(), plan_id="plan")
        assert plan.planner == Assignment(harness="frontier", model="frontier-model")
        assert plan.initiatives["one"].spec.assignment == Assignment(
            harness="executor", model="cheap-model"
        )
        _ = daemon.approve_plan("plan")
        _ = await daemon.run_and_settle(
            "plan", "one", runtime=runtime, collector=Collector(), checks=()
        )
        attempt = daemon.plan("plan").initiatives["one"].attempts[0]
        assert attempt.assignment == Assignment(harness="executor", model="cheap-model")
        argv = shlex.split(runtime.commands[0])
        assert argv[0] == str(binary)
        assert "cheap-model" in argv

    try:
        asyncio.run(scenario())
    finally:
        store.close()


# --- POST /kitchen/smoke and the wire boundary -------------------------------


def smoke_app(
    root: Path,
    smoke_runner: SmokeRunner,
    *,
    discovery_runner: Callable[[Sequence[str], float], ProbeResult] | None = None,
) -> tuple[EventStore, FastAPI]:
    """A Kitchen-configured daemon with an injected smoke seam, ready to drive."""
    binary = executable(root)
    write_kitchen(root, str(binary))
    store = EventStore(root / "events.db")
    daemon = Daemon(
        store,
        project_root=root,
        smoke_runner=smoke_runner,
        discovery_runner=discovery_runner,
    )
    return store, create_app(daemon)


def read_document(path: Path) -> dict[str, object]:
    """The stored Kitchen document, typed for assertions."""
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def adapters_of(document: dict[str, object]) -> dict[object, dict[str, object]]:
    """The document's adapters keyed by name, typed for assertions."""
    rows = cast(list[dict[str, object]], document["adapters"])
    return {row["name"]: row for row in rows}


def smoke_stub(
    processes: list[SmokeProcess],
) -> tuple[SmokeRunner, list[tuple[str, str, float]]]:
    """A smoke seam returning the given outcomes in order and recording calls."""
    seen: list[tuple[str, str, float]] = []

    async def runner(
        harness: str, model: str, root: Path, timeout: float
    ) -> SmokeProcess:
        del root
        seen.append((harness, model, timeout))
        return processes[min(len(seen) - 1, len(processes) - 1)]

    return runner, seen


FRONTIER_PAIR = {"harness": "frontier", "model": "frontier-model"}


def test_kitchen_smoke_returns_structured_passed_failed_refused_and_timed_out_results(
    tmp_path: Path,
) -> None:
    """All four outcomes arrive as HTTP 200 structured results with exactly the
    ruled fields and the ruled detail source per state."""
    runner, seen = smoke_stub(
        [
            SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\nquietly\n"),
            SmokeProcess(returncode=0, stdout="I cannot comply with that.\n"),
            SmokeProcess(returncode=1, stderr="provider rejected the key\n"),
            SmokeProcess(returncode=None, timed_out=True),
        ]
    )
    store, app = smoke_app(tmp_path, runner)

    async def scenario() -> None:
        details: dict[str, str] = {}
        for expected in ("passed", "failed", "refused", "timed_out"):
            status, body = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)
            assert status == 200
            assert set(body) == {
                "harness", "model", "state", "detail", "duration", "at",
            }
            assert body["state"] == expected
            assert body["harness"] == "frontier"
            assert body["model"] == "frontier-model"
            assert isinstance(body["duration"], float) and body["duration"] >= 0
            _ = datetime.fromisoformat(cast(str, body["at"]))
            details[expected] = cast(str, body["detail"])
        # passed reports the first stdout line; refused reports stderr; a timed
        # run with no output states that instead of borrowing an exit state.
        assert details["passed"] == SMOKE_MARKER
        assert details["failed"] == "I cannot comply with that."
        assert details["refused"] == "provider rejected the key"
        assert details["timed_out"] == "The adapter produced no output."
        assert [call[0] for call in seen] == ["frontier"] * 4
        assert [call[2] for call in seen] == [30.0] * 4  # the ruled default

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_rejects_unknown_catalog_pair_and_invalid_timeout(
    tmp_path: Path,
) -> None:
    """Every malformed or unenumerated request is the daemon's own 400 -- never
    a framework 422 -- and no rejected request launches anything."""
    runner, seen = smoke_stub(
        [SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n")]
    )
    store, app = smoke_app(tmp_path, runner)

    async def scenario() -> None:
        base = dict(FRONTIER_PAIR)
        rejected: list[tuple[object, str]] = [
            ({**base, "model": "ghost-model"}, "not a configured model pair"),
            ({"harness": "ghost", "model": "m"}, "not a configured model pair"),
            ({**base, "timeout": 0.5}, "timeout"),
            ({**base, "timeout": 121}, "timeout"),
            ({**base, "timeout": "30"}, "timeout"),
            ({**base, "timeout": True}, "timeout"),
            ({**base, "timeout": float("nan")}, "timeout"),
            ({**base, "prompt": "do my work"}, "unexpected field"),
            ({"harness": "frontier"}, "model"),
            ({"model": "frontier-model"}, "harness"),
            ({"harness": "  ", "model": "frontier-model"}, "harness"),
            (["not", "an", "object"], "JSON object"),
            (None, "JSON"),
        ]
        for body, fragment in rejected:
            status, response = await request(
                app, "POST", "/kitchen/smoke", body
            )
            assert status == 400, (body, response)
            assert fragment in str(response["detail"]), (body, response)
        assert seen == []  # a rejected request never reaches the adapter

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_redacts_then_truncates_detail_without_returning_command_or_prompt(
    tmp_path: Path,
) -> None:
    """Detail is redacted BEFORE it is bounded: a secret spanning the 200-code-
    point cap arrives fully redacted, and neither command nor prompt can leak."""
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    # The secret ends past the cap: truncate-first would cut it in half and
    # defeat the value-shape test that recognizes it. The leading space gives
    # the pattern a word boundary, as any real line would.
    line = "x" * 184 + " " + secret + " " + "y" * 60
    runner, _ = smoke_stub(
        [SmokeProcess(returncode=0, stdout=line + "\n" + SMOKE_MARKER + "\n")]
    )
    store, app = smoke_app(tmp_path, runner)

    async def scenario() -> None:
        status, body = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)
        assert status == 200
        assert body["state"] == "passed"  # marker elsewhere in stdout still counts
        detail = cast(str, body["detail"])
        assert "[redacted]" in detail
        assert secret not in detail
        assert len(detail) <= 200
        text = json.dumps(body)
        assert str(tmp_path / "bin" / "harness") not in text  # never the command
        assert SMOKE_PROMPT not in text  # never the probe prompt
        assert SMOKE_MARKER not in detail  # detail is the first line, not the marker

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_projects_latest_result_per_pair_and_explicit_never_run_sentence(
    tmp_path: Path,
) -> None:
    """Smoke rides on GET /kitchen: empty-with-the-sentence before anything ran,
    one row per pair in deterministic order, latest outcome only."""
    runner, _ = smoke_stub(
        [
            SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n"),
            SmokeProcess(returncode=1, stderr="boom\n"),
            SmokeProcess(returncode=0, stdout="no marker here\n"),
        ]
    )
    store, app = smoke_app(tmp_path, runner)

    async def scenario() -> None:
        status, body = await request(app, "GET", "/kitchen")
        assert status == 200
        smoke = cast(dict[str, object], body["smoke"])
        assert cast(list[object], smoke["results"]) == []
        assert smoke["absence"] == SMOKE_NEVER_RUN

        _ = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)
        _ = await request(
            app, "POST", "/kitchen/smoke", {"harness": "executor", "model": "cheap-model"}
        )
        status, body = await request(app, "GET", "/kitchen")
        smoke = cast(dict[str, object], body["smoke"])
        results = cast(list[dict[str, object]], smoke["results"])
        assert [(row["harness"], row["model"]) for row in results] == [
            ("executor", "cheap-model"),
            ("frontier", "frontier-model"),
        ]  # deterministic (harness, model) order
        assert results[1]["state"] == "passed"
        assert smoke["absence"] is None

        _ = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)
        status, body = await request(app, "GET", "/kitchen")
        results = cast(
            list[dict[str, object]], cast(dict[str, object], body["smoke"])["results"]
        )
        assert len(results) == 2  # same pair replaces, never accumulates
        frontier = next(row for row in results if row["harness"] == "frontier")
        assert frontier["state"] == "failed"  # the LATEST outcome

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_allows_concurrent_calls_and_latest_completion_wins(
    tmp_path: Path,
) -> None:
    """No server-side guard: both probes run, and the one that COMPLETES last
    publishes last even though it was submitted first."""
    gate = asyncio.Event()
    calls = 0

    async def runner(
        harness: str, model: str, root: Path, timeout: float
    ) -> SmokeProcess:
        nonlocal calls
        del harness, model, root, timeout
        calls += 1
        if calls == 1:
            _ = await gate.wait()
            return SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n")
        gate.set()
        return SmokeProcess(returncode=1, stderr="second finished first\n")

    store, app = smoke_app(tmp_path, runner)

    async def scenario() -> None:
        first, second = await asyncio.gather(
            request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR),
            request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR),
        )
        assert first[0] == 200 and second[0] == 200
        status, body = await request(app, "GET", "/kitchen")
        assert status == 200
        results = cast(
            list[dict[str, object]], cast(dict[str, object], body["smoke"])["results"]
        )
        assert len(results) == 1
        assert results[0]["state"] == "passed"
        assert results[0]["detail"] == SMOKE_MARKER

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_survives_discovery_and_resets_only_after_successful_save(
    tmp_path: Path,
) -> None:
    """Discovery does not invalidate measured outcomes; a refused save keeps
    them; a successful save -- even a no-op -- clears them all, and the empty
    projection then says cleared rather than never-run."""
    runner, _ = smoke_stub(
        [SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n")]
    )
    store, app = smoke_app(
        tmp_path,
        runner,
        discovery_runner=lambda argv, timeout: (
            ProbeResult(returncode=0, stdout="frontier 1.0\n")
        ),
    )

    async def scenario() -> None:
        _ = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)

        status, _ = await request(app, "POST", "/kitchen/discovery", {"timeout": 3})
        assert status == 200
        status, body = await request(app, "GET", "/kitchen")
        smoke = cast(dict[str, object], body["smoke"])
        assert len(cast(list[object], smoke["results"])) == 1  # survived discovery

        document = Kitchen.load(tmp_path).model_dump(mode="json")
        status, _ = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": "stale00000000000"},
        )
        assert status == 409
        status, body = await request(app, "GET", "/kitchen")
        smoke = cast(dict[str, object], body["smoke"])
        assert len(cast(list[object], smoke["results"])) == 1  # survived refusal

        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        status, saved = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": current["revision"]},
        )
        assert status == 200
        smoke = cast(dict[str, object], saved["smoke"])
        assert cast(list[object], smoke["results"]) == []
        assert smoke["absence"] == SMOKE_CLEARED

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_smoke_is_lost_when_daemon_is_recreated(tmp_path: Path) -> None:
    """Smoke results are daemon memory, like discovery facts: a restart never
    resurrects a result that was never persisted."""
    runner, _ = smoke_stub(
        [SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n")]
    )
    store, app = smoke_app(tmp_path, runner)

    async def first_daemon() -> None:
        status, body = await request(app, "POST", "/kitchen/smoke", FRONTIER_PAIR)
        assert status == 200
        status, body = await request(app, "GET", "/kitchen")
        smoke = cast(dict[str, object], body["smoke"])
        assert len(cast(list[object], smoke["results"])) == 1

    try:
        asyncio.run(first_daemon())
    finally:
        store.close()

    restarted_store = EventStore(tmp_path / "events.db")
    restarted = Daemon(
        restarted_store,
        project_root=tmp_path,
        smoke_runner=runner,
    )
    restarted_app = create_app(restarted)

    async def second_daemon() -> None:
        status, body = await request(restarted_app, "GET", "/kitchen")
        assert status == 200
        smoke = cast(dict[str, object], body["smoke"])
        assert cast(list[object], smoke["results"]) == []
        assert smoke["absence"] == SMOKE_NEVER_RUN

    try:
        asyncio.run(second_daemon())
    finally:
        restarted_store.close()


def test_agent_args_are_a_launch_template_and_merge_when_omitted(tmp_path: Path) -> None:
    kitchen = Kitchen.model_validate({
        "adapters": [{
            "name": "claude-code", "argv": ["claude", "-p", "{prompt}"],
            "agent_args": ["--dangerously-skip-permissions"],
        }]
    })
    assert kitchen.adapters[0].agent_args == ["--dangerously-skip-permissions"]
    merged = _merge_kitchen_templates(
        {"adapters": [{"name": "claude-code", "capabilities": {}}]}, kitchen
    )
    assert merged["adapters"][0]["agent_args"] == ["--dangerously-skip-permissions"]


def test_agent_args_reject_the_prompt_placeholder() -> None:
    with pytest.raises(ValidationError, match="agent_args"):
        _ = Adapter(name="x", argv=["x", "{prompt}"], agent_args=["{prompt}"])


def test_kitchen_wire_omits_argv_and_model_argv_from_all_kitchen_responses(
    tmp_path: Path,
) -> None:
    """Launch templates never reach the client on any route that returns a
    Kitchen document -- while the stored file keeps them intact."""
    runner, _ = smoke_stub(
        [SmokeProcess(returncode=0, stdout=f"{SMOKE_MARKER}\n")]
    )
    store, app = smoke_app(tmp_path, runner)
    binary_text = str(tmp_path / "bin" / "harness")
    path = tmp_path / ".herdsman" / "kitchen.json"

    def assert_no_templates(payload: dict[str, object]) -> None:
        for adapter in cast(list[dict[str, object]], payload["adapters"]):
            assert "argv" not in adapter
            assert "model_argv" not in adapter
            assert "agent_args" not in adapter
            assert adapter["name"] in {"frontier", "executor"}
            assert "capabilities" in adapter  # the useful half survives

    async def scenario() -> None:
        # Before discovery runs, nothing on the wire can even name the binary.
        status, payload = await request(app, "GET", "/kitchen")
        assert status == 200
        assert_no_templates(payload)
        assert binary_text not in json.dumps(payload)

        # Discovery facts keep carrying `executable` -- K1's observed fact --
        # while the launch template itself stays off the wire.
        status, payload = await request(app, "POST", "/kitchen/discovery", {"timeout": 3})
        assert status == 200
        assert_no_templates(payload)

        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        status, saved = await request(
            app, "PUT", "/kitchen",
            {
                "kitchen": Kitchen.load(tmp_path).model_dump(mode="json"),
                "expect_revision": current["revision"],
            },
        )
        assert status == 200
        assert_no_templates(saved)

        stored = read_document(path)
        by_name = adapters_of(stored)
        assert by_name["frontier"]["argv"] == [binary_text, "--print", "{prompt}"]
        assert by_name["frontier"]["model_argv"] == ["--model"]  # strip is wire-only

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_preserves_omitted_templates_and_replaces_explicit_templates(
    tmp_path: Path,
) -> None:
    """Merge-on-absent: a client that never received a template keeps it by
    omitting it; an explicit field (even an empty model_argv) replaces it."""
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        document = read_document(path)
        by_name = adapters_of(document)
        del by_name["frontier"]["argv"]
        del by_name["frontier"]["model_argv"]
        new_argv = [str(tmp_path / "bin" / "second"), "--print", "{prompt}"]
        by_name["executor"]["argv"] = new_argv
        by_name["executor"]["model_argv"] = []  # explicit empty replaces

        status, _ = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": current["revision"]},
        )
        assert status == 200
        stored = read_document(path)
        stored_by_name = adapters_of(stored)
        assert stored_by_name["frontier"]["argv"] == [
            str(binary), "--print", "{prompt}",
        ]  # omitted -> preserved
        assert stored_by_name["frontier"]["model_argv"] == ["--model"]
        assert stored_by_name["executor"]["argv"] == new_argv  # explicit -> replaced
        assert stored_by_name["executor"]["model_argv"] == []

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_requires_argv_for_new_adapter(tmp_path: Path) -> None:
    """A brand-new adapter supplies its own template: preserved fields exist
    only for adapters that already had them, and nothing is written on refusal."""
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        document = read_document(path)
        _ = cast(list[dict[str, object]], document["adapters"]).append(
            {"name": "fresh", "model_argv": ["--model"]}
        )
        before = path.read_bytes()

        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": current["revision"]},
        )
        assert status == 400  # the daemon's own validation error, not a 422
        assert "argv" in str(body["detail"])
        assert path.read_bytes() == before  # nothing written on refusal

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_rejects_credential_shaped_template_without_echoing_it(
    tmp_path: Path,
) -> None:
    """An explicit replacement carrying a credential-shaped value is refused
    with a generic error that names the adapter and field, never the value."""
    binary = executable(tmp_path)
    write_kitchen(tmp_path, str(binary))
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        status, current = await request(app, "GET", "/kitchen")
        assert status == 200
        before = path.read_bytes()
        revision = current["revision"]
        template_document = read_document(path)

        cases = [
            # option + adjacent value (the argv-shaped credential form)
            [str(tmp_path / "bin" / "x"), "--print", "--api-key",
             "hunter2CorrectHorseBattery9", "{prompt}"],
            # and a secret-shaped value with no option-name help
            [str(tmp_path / "bin" / "x"), "--print",
             "sk-live-9f8e7d6c5b4a3210x", "{prompt}"],
        ]
        for argv in cases:
            document = cast(
                dict[str, object], json.loads(json.dumps(template_document))
            )
            by_name = adapters_of(document)
            by_name["executor"]["argv"] = argv
            status, body = await request(
                app, "PUT", "/kitchen",
                {"kitchen": document, "expect_revision": revision},
            )
            assert status == 400
            assert "credential-shaped" in str(body["detail"])
            assert "executor" in str(body["detail"])
            for value in argv[3:4]:
                assert value not in json.dumps(body)
            assert path.read_bytes() == before  # nothing written on refusal

    try:
        asyncio.run(scenario())
    finally:
        store.close()


# --- reasoning-effort levels and pools ---------------------------------------


def effort_home(root: Path) -> Path:
    """A fake home whose pi store declares one model's levels."""
    home = root / "home"
    path = home / ".pi" / "agent" / "models-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(
            {
                "openai-codex": {
                    "models": [
                        {
                            "id": "gpt-5.6-luna",
                            "provider": "openai-codex",
                            "reasoning": True,
                            "thinkingLevelMap": {"xhigh": "xhigh", "max": "max", "minimal": "low"},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return home


def write_effort_kitchen(
    root: Path, *, efforts: dict[str, list[str]] | None = None
) -> None:
    payload = {
        "version": 1,
        "adapters": [
            {"name": "pi", "argv": ["/usr/bin/pi", "--print", "{prompt}"], "model_argv": ["--model"]}
        ],
        "models": [
            {"harness": "pi", "model": "gpt-5.6-luna"},
            {"harness": "pi", "model": "unlisted"},
        ],
        "defaults": {
            "planner": {"harness": "pi", "model": "gpt-5.6-luna"},
            "initiative": {"harness": "pi", "model": "gpt-5.6-luna"},
        },
        "efforts": efforts or {},
    }
    directory = root / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "kitchen.json").write_text(json.dumps(payload), encoding="utf-8")


PI_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]


def test_kitchen_api_reports_local_effort_levels_for_declared_pairs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_effort_kitchen(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)

    async def scenario() -> None:
        status, body = await request(app, "GET", "/kitchen")
        assert status == 200
        # A declared pair with a catalog hit is present in harness order; the
        # unlisted model is absent, never a guessed list.
        assert body["effort_levels"] == {"pi/gpt-5.6-luna": PI_LEVELS}
        assert body["efforts"] == {}
        assert cast(dict[str, object], body["discovery"])["models"] == []

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_accepts_only_discovered_effort_subsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_effort_kitchen(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def put(document: dict[str, object], revision: object) -> tuple[int, dict[str, object]]:
        return await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": revision},
        )

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        document = read_document(path)
        document["efforts"] = {"pi/gpt-5.6-luna": ["high", "max"]}
        status, body = await put(document, current["revision"])
        assert status == 200
        assert body["efforts"] == {"pi/gpt-5.6-luna": ["high", "max"]}

        # A level the harness does not support is refused, naming the pool.
        _, current = await request(app, "GET", "/kitchen")
        document = read_document(path)
        document["efforts"] = {"pi/gpt-5.6-luna": ["ultra"]}
        status, body = await put(document, current["revision"])
        assert status == 400
        assert "does not support effort ultra" in str(body["detail"])

        # A declared pair with no discovered levels has nothing to select from.
        _, current = await request(app, "GET", "/kitchen")
        document = read_document(path)
        document["efforts"] = {"pi/unlisted": ["low"]}
        status, body = await put(document, current["revision"])
        assert status == 400
        assert "no reasoning effort levels are known for pi/unlisted" in str(body["detail"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


class EffortPlanner:
    """A planner that names one initiative on an explicit effort."""

    def __init__(self, effort: str) -> None:
        self.effort: str = effort

    async def propose(self, brief: str) -> object:
        return {
            "initiatives": [
                {
                    "id": "one",
                    "name": "one",
                    "brief": brief,
                    "assignment": {
                        "harness": "pi",
                        "model": "gpt-5.6-luna",
                        "effort": self.effort,
                    },
                }
            ]
        }


def test_explicit_effort_outside_the_pool_is_refused_on_dispatch_reassign_and_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_effort_kitchen(tmp_path, efforts={"pi/gpt-5.6-luna": ["low", "high"]})
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    async def scenario() -> None:
        # Dispatch: a role assignment carrying an unsupported level.
        with pytest.raises(ValueError, match=r"does not support effort 'max'"):
            _ = await daemon.create_plan(
                "ship",
                planner=Planner(),
                roles={"implementer": Assignment(harness="pi", model="gpt-5.6-luna", effort="max")},
                plan_id="bad-role",
            )
        # Dispatch: the planner proposal itself carries an unsupported level.
        with pytest.raises(ValueError, match=r"does not support effort 'max'"):
            _ = await daemon.create_plan(
                "ship", planner=EffortPlanner("max"), plan_id="bad-proposal"
            )
        # A level inside the selected pool is accepted and snapshotted.
        plan = await daemon.create_plan("ship", planner=EffortPlanner("high"), plan_id="ok")
        assert plan.initiatives["one"].spec.assignment.effort == "high"
        # Reassign: same rule, on the next-attempt override.
        with pytest.raises(ValueError, match=r"does not support effort 'max'"):
            _ = daemon.reassign_initiative(
                "ok", "one",
                Assignment(harness="pi", model="gpt-5.6-luna", effort="max"),
            )

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def write_assignment_kitchen(
    root: Path, *, efforts: dict[str, list[str]] | None = None
) -> None:
    """A pi pair with discovered levels plus a claude pair with none."""
    payload = {
        "version": 1,
        "adapters": [
            {"name": "pi", "argv": ["/usr/bin/pi", "--print", "{prompt}"], "model_argv": ["--model"]},
            {"name": "claude-code", "argv": ["/usr/bin/claude", "--print", "{prompt}"], "model_argv": ["--model"]},
        ],
        "models": [
            {"harness": "pi", "model": "gpt-5.6-luna"},
            {"harness": "claude-code", "model": "opus"},
        ],
        "defaults": {
            "planner": {"harness": "pi", "model": "gpt-5.6-luna"},
            "initiative": {"harness": "pi", "model": "gpt-5.6-luna"},
        },
        "efforts": efforts or {},
    }
    directory = root / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "kitchen.json").write_text(json.dumps(payload), encoding="utf-8")


def test_kitchen_put_validates_effort_on_every_declared_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_assignment_kitchen(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    def mutate(base: dict[str, object], change: object) -> dict[str, object]:
        document = cast(dict[str, object], json.loads(json.dumps(base)))
        cast(Callable[[dict[str, object]], None], change)(document)
        return document

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        base = read_document(path)
        revision = current["revision"]

        def planner(document: dict[str, object]) -> None:
            defaults = cast(dict[str, object], document["defaults"])
            defaults["planner"] = {"harness": "pi", "model": "gpt-5.6-luna", "effort": "ultra"}

        def initiative(document: dict[str, object]) -> None:
            defaults = cast(dict[str, object], document["defaults"])
            defaults["initiative"] = {"harness": "pi", "model": "gpt-5.6-luna", "effort": "ultra"}

        def role(document: dict[str, object]) -> None:
            defaults = cast(dict[str, object], document["defaults"])
            defaults["roles"] = {
                "implementer": {"harness": "pi", "model": "gpt-5.6-luna", "effort": "ultra"}
            }

        def fallback(document: dict[str, object]) -> None:
            document["fallbacks"] = [
                {
                    "primary": {"harness": "pi", "model": "gpt-5.6-luna", "effort": "ultra"},
                    "candidates": [{"harness": "claude-code", "model": "opus"}],
                }
            ]

        def unknown_pair(document: dict[str, object]) -> None:
            defaults = cast(dict[str, object], document["defaults"])
            defaults["planner"] = {"harness": "claude-code", "model": "opus", "effort": "high"}

        for label, change in (
            ("planner", planner),
            ("initiative", initiative),
            ("role", role),
            ("fallback", fallback),
        ):
            status, body = await request(
                app, "PUT", "/kitchen",
                {"kitchen": mutate(base, change), "expect_revision": revision},
            )
            assert status == 400, label
            assert "does not support effort 'ultra'" in str(body["detail"]), label

        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": mutate(base, unknown_pair), "expect_revision": revision},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])

        # A level inside the discovered pool still saves.
        def accepted(document: dict[str, object]) -> None:
            defaults = cast(dict[str, object], document["defaults"])
            defaults["planner"] = {"harness": "pi", "model": "gpt-5.6-luna", "effort": "high"}

        status, _ = await request(
            app, "PUT", "/kitchen",
            {"kitchen": mutate(base, accepted), "expect_revision": revision},
        )
        assert status == 200

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_normalizes_pool_order_so_the_highest_level_is_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_effort_kitchen(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        document = read_document(path)
        document["efforts"] = {"pi/gpt-5.6-luna": ["max", "low", "high"]}
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": current["revision"]},
        )
        assert status == 200
        # Rewritten in the harness's own order, not the client's.
        assert body["efforts"] == {"pi/gpt-5.6-luna": ["low", "high", "max"]}
        loaded = Kitchen.load(tmp_path)
        assert loaded.efforts == {"pi/gpt-5.6-luna": ["low", "high", "max"]}
        assert effective_effort(loaded, Assignment(harness="pi", model="gpt-5.6-luna")) == "max"
        # The executor launch takes the same normalized highest level.
        packet = compile_task_packet(
            InitiativeSpec(
                id="one",
                name="one",
                brief="ship",
                assignment=Assignment(harness="pi", model="gpt-5.6-luna"),
            )
        )
        argv = shlex.split(executor_command(packet, project_root=tmp_path))
        assert argv[argv.index("--thinking") + 1] == "max"

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_keeps_an_unchanged_pool_without_a_local_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing cache must not block every save: only new or changed entries
    are checked against discovery."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))  # no pi/claude caches
    write_assignment_kitchen(tmp_path, efforts={"claude-code/opus": ["low", "high"]})
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        status, body = await request(app, "GET", "/kitchen")
        assert status == 200
        assert body["effort_levels"] == {}  # nothing discoverable for either pair

        # Unchanged: accepted as written, because discovery cannot speak for it.
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": read_document(path), "expect_revision": body["revision"]},
        )
        assert status == 200
        assert body["efforts"] == {"claude-code/opus": ["low", "high"]}

        # Changed: refused, since there is nothing to validate it against.
        document = read_document(path)
        document["efforts"] = {"claude-code/opus": ["high"]}
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": body["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


class NeverPlanner:
    """A planner that records whether it was ever invoked."""

    def __init__(self) -> None:
        self.called: bool = False

    async def propose(self, brief: str) -> object:
        self.called = True
        return {"initiatives": [{"id": "one", "name": "one", "brief": brief}]}

    async def recalibrate(self, context: str) -> object:
        self.called = True
        return {"initiatives": [{"id": "one", "name": "one", "brief": context}]}


def test_recalibrate_refuses_an_invalid_planner_effort_before_the_planner_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(effort_home(tmp_path)))
    write_effort_kitchen(tmp_path)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    path = tmp_path / ".herdsman" / "kitchen.json"

    async def scenario() -> None:
        _ = await daemon.create_plan("ship", planner=Planner(), plan_id="plan")
        # A hand-edited document the PUT boundary never saw.
        document = read_document(path)
        defaults = cast(dict[str, object], document["defaults"])
        defaults["planner"] = {"harness": "pi", "model": "gpt-5.6-luna", "effort": "ultra"}
        _ = path.write_text(json.dumps(document), encoding="utf-8")

        never = NeverPlanner()
        with pytest.raises(ValueError, match=r"does not support effort 'ultra'"):
            _ = await daemon.recalibrate("plan", planner=never)
        assert never.called is False

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_the_planner_launch_uses_the_selected_pools_highest_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner assignment has no explicit level, so the launch takes the
    selected pool's highest."""
    payload = {
        "version": 1,
        "adapters": [
            {"name": "frontier", "argv": ["/usr/bin/pi", "--print", "{prompt}"], "model_argv": ["--model"]}
        ],
        "models": [{"harness": "frontier", "model": "gpt-5.6-luna"}],
        "defaults": {
            "planner": {"harness": "frontier", "model": "gpt-5.6-luna"},
            "initiative": {"harness": "frontier", "model": "gpt-5.6-luna"},
        },
        "efforts": {"frontier/gpt-5.6-luna": ["low", "high"]},
    }
    directory = tmp_path / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "kitchen.json").write_text(json.dumps(payload), encoding="utf-8")
    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **_kwargs: object) -> PlannerProcess:
        calls.append(list(argv))
        return PlannerProcess(b'{"initiatives":[{"id":"one","name":"one","brief":"ship"}]}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    async def scenario() -> None:
        _ = await daemon.create_plan("ship", plan_id="plan")

    try:
        asyncio.run(scenario())
        assert calls[0][2:6] == ["--model", "gpt-5.6-luna", "--thinking", "high"]
    finally:
        store.close()


def test_kitchen_put_refuses_a_new_effort_on_a_grandfathered_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5 grandfathers the pool entry only: an explicit assignment effort is
    always checked against discovered levels, so a stale cacheless pool cannot
    authorize a new level on an unsupported pair."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))  # no caches at all
    write_assignment_kitchen(tmp_path, efforts={"claude-code/opus": ["low", "high"]})
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)
    path = tmp_path / ".herdsman" / "kitchen.json"

    def document_from(base: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(base)))

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        base = read_document(path)
        before = path.read_bytes()

        # The stale pool is unchanged, but a NEW planner effort is not allowed.
        planner = document_from(base)
        defaults = cast(dict[str, object], planner["defaults"])
        defaults["planner"] = {"harness": "claude-code", "model": "opus", "effort": "high"}
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": planner, "expect_revision": current["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])
        assert path.read_bytes() == before  # nothing written

        # Same rule for a fallback candidate's explicit effort.
        fallback = document_from(base)
        fallback["fallbacks"] = [
            {
                "primary": {"harness": "pi", "model": "gpt-5.6-luna"},
                "candidates": [
                    {"harness": "claude-code", "model": "opus", "effort": "high"}
                ],
            }
        ]
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": fallback, "expect_revision": current["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])
        assert path.read_bytes() == before

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_keeps_an_unchanged_explicit_effort_without_a_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored explicit effort was validated when it arrived: a catalog that
    later disappears must not make every save fail. A changed effort is still
    checked against discovery."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    write_assignment_kitchen(tmp_path, efforts={"claude-code/opus": ["low", "high"]})
    path = tmp_path / ".herdsman" / "kitchen.json"
    document = read_document(path)
    cast(dict[str, object], document["defaults"])["planner"] = {
        "harness": "claude-code", "model": "opus", "effort": "high",
    }
    _ = path.write_text(json.dumps(document), encoding="utf-8")

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        assert current["effort_levels"] == {}

        # Unchanged assignment and pool: accepted, nothing about it re-checked.
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": read_document(path), "expect_revision": current["revision"]},
        )
        assert status == 200
        assert body["efforts"] == {"claude-code/opus": ["low", "high"]}

        # Changed effort: refused, since there is nothing to validate it against.
        before = path.read_bytes()
        changed = read_document(path)
        cast(dict[str, object], cast(dict[str, object], changed["defaults"])["planner"])[
            "effort"
        ] = "low"
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": changed, "expect_revision": body["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])
        assert path.read_bytes() == before

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def codex_home(root: Path) -> Path:
    """A fake home whose codex cache declares one model's levels."""
    home = root / "home"
    path = home / ".codex" / "models_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6-sol",
                        "supported_reasoning_levels": [
                            {"effort": "low"}, {"effort": "high"}, {"effort": "max"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return home


def test_kitchen_put_refuses_a_pool_narrowed_below_an_unchanged_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discovered pair is always validated: an unchanged effort is refused
    once the incoming pool no longer contains it."""
    monkeypatch.setenv("HOME", str(codex_home(tmp_path)))
    payload = {
        "version": 1,
        "adapters": [
            {"name": "codex", "argv": ["/usr/bin/codex", "--print", "{prompt}"], "model_argv": ["--model"]}
        ],
        "models": [{"harness": "codex", "model": "gpt-6-sol"}],
        "defaults": {
            "planner": {"harness": "codex", "model": "gpt-6-sol", "effort": "high"},
            "initiative": {"harness": "codex", "model": "gpt-6-sol"},
        },
        "efforts": {"codex/gpt-6-sol": ["low", "high"]},
    }
    directory = tmp_path / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "kitchen.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        before = path.read_bytes()
        document = read_document(path)
        document["efforts"] = {"codex/gpt-6-sol": ["low"]}
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": document, "expect_revision": current["revision"]},
        )
        assert status == 400
        assert "does not support effort 'high'" in str(body["detail"])
        assert path.read_bytes() == before

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_grandfathers_two_unchanged_cacheless_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every unchanged candidate is grandfathered by its whole assignment, not
    by a shared fallback label; a replaced candidate is refused."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))  # no caches
    payload = {
        "version": 1,
        "adapters": [
            {"name": "claude-code", "argv": ["/usr/bin/claude", "--print", "{prompt}"], "model_argv": ["--model"]}
        ],
        "models": [
            {"harness": "claude-code", "model": "haiku"},
            {"harness": "claude-code", "model": "opus"},
            {"harness": "claude-code", "model": "sonnet"},
        ],
        "defaults": {
            "planner": {"harness": "claude-code", "model": "haiku"},
            "initiative": {"harness": "claude-code", "model": "haiku"},
        },
        "fallbacks": [
            {
                "primary": {"harness": "claude-code", "model": "haiku"},
                "candidates": [
                    {"harness": "claude-code", "model": "opus", "effort": "high"},
                    {"harness": "claude-code", "model": "sonnet", "effort": "low"},
                ],
            }
        ],
    }
    directory = tmp_path / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "kitchen.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        assert current["effort_levels"] == {}

        # Both unchanged candidates survive an absent catalog.
        status, _ = await request(
            app, "PUT", "/kitchen",
            {"kitchen": read_document(path), "expect_revision": current["revision"]},
        )
        assert status == 200

        # Replacing one candidate's effort is a new assignment: refused.
        before = path.read_bytes()
        changed = read_document(path)
        chain = cast(list[dict[str, object]], changed["fallbacks"])[0]
        candidates = cast(list[dict[str, object]], chain["candidates"])
        candidates[0]["effort"] = "low"
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": changed, "expect_revision": current["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])
        assert path.read_bytes() == before

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_kitchen_put_refuses_a_stale_pool_when_the_adapter_executable_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing cache is only a missing cache while the same binary is asked:
    swapping the pair's executable makes its support unknown, so nothing is
    grandfathered against it."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    write_assignment_kitchen(tmp_path, efforts={"claude-code/opus": ["low", "high"]})
    path = tmp_path / ".herdsman" / "kitchen.json"
    document = read_document(path)
    cast(dict[str, object], document["defaults"])["planner"] = {
        "harness": "claude-code", "model": "opus", "effort": "high",
    }
    _ = path.write_text(json.dumps(document), encoding="utf-8")

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    app = create_app(daemon)

    async def scenario() -> None:
        _, current = await request(app, "GET", "/kitchen")
        before = path.read_bytes()
        changed = read_document(path)
        adapters = cast(list[dict[str, object]], changed["adapters"])
        claude = next(item for item in adapters if item["name"] == "claude-code")
        claude["argv"] = ["/usr/bin/unknown", "--print", "{prompt}"]
        status, body = await request(
            app, "PUT", "/kitchen",
            {"kitchen": changed, "expect_revision": current["revision"]},
        )
        assert status == 400
        assert "no reasoning effort levels are known for claude-code/opus" in str(body["detail"])
        assert path.read_bytes() == before

    try:
        asyncio.run(scenario())
    finally:
        store.close()
