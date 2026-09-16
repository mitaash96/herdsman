"""Sprint 8 daemon integration: Kitchen selection and HTTP projections."""

import asyncio
import json
import shlex
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from herdsman.classes import Assignment, Checkpoint, RuntimeObserved, Usage
from herdsman.daemon import Daemon, create_app
from herdsman.discovery import ProbeResult
from herdsman.herdr import RuntimeInventory
from herdsman.kitchen import Kitchen
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
