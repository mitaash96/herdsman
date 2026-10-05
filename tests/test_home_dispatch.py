"""Dispatch HTTP creates a gate without launching workers; failed planning writes nothing."""
import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from herdsman.classes import Assignment
from herdsman.daemon import Daemon, create_app
from herdsman.runtime import PlannerError
from herdsman.store import EventStore


async def post(app: FastAPI, body: dict[str, object], path: str = "/plans", query: bytes = b"") -> tuple[int, dict[str, object]]:
    sent: list[Message] = []
    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    async def send(message: Message) -> None:
        sent.append(message)
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "headers": [(b"content-type", b"application/json")],
        "client": ("test", 1), "server": ("test", 80),
    }
    await app(scope, receive, send)
    status = cast(int, next(item["status"] for item in sent if item["type"] == "http.response.start"))
    raw = b"".join(cast(bytes, item.get("body", b"")) for item in sent if item["type"] == "http.response.body")
    return status, cast(dict[str, object], json.loads(raw))


def test_dispatch_http_stub_planner_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    prompts: list[str] = []

    class Planner:
        async def propose(self, brief: str) -> object:
            prompts.append(brief)
            if brief.startswith("fail"):
                raise PlannerError("stub planner failed")
            return {"title": "Test plan", "initiatives": [{"id": "first", "name": "First", "brief": brief}]}

    def planner_stub(**kwargs: object) -> Planner:
        del kwargs
        return Planner()

    monkeypatch.setattr("herdsman.daemon.PiFrontierPlanner", planner_stub)
    app = create_app(daemon)
    try:
        status, failure = asyncio.run(post(app, {"brief": "fail here"}))
        assert status == 400 and failure["detail"] == "stub planner failed"
        assert store.plans() == []
        status, created = asyncio.run(post(app, {
            "brief": "build it", "acceptance": "tests pass", "token_cap": 1000,
            "planner": {"harness": "pi", "model": "stub"},
        }))
        assert status == 200
        plan_id = cast(str, created["id"])
        assert "Acceptance criteria:\ntests pass" in prompts[-1]
        assert created["approval"] == "pending" and created["token_cap"] == 1000
        assert [event.type for event in store.read(plan_id)] == ["plan_created", "plan_proposed"]
        assert daemon.fleet().attention[0].kind == "plan_gate"
        assert created["brief"] == "build it"
    finally:
        store.close()


def test_role_selection_is_applied_to_proposal(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    _ = daemon.library_create("role", "builder", title="Careful builder", body="Build carefully")

    class Planner:
        def propose(self, brief: str) -> object:
            assert "role/builder" in brief
            assert "builder — Careful builder" in brief
            assert "role/implementer" not in brief
            return {"title": "Test plan", "initiatives": [{"id": "a", "name": "A", "brief": "work", "role": "builder"}]}

    try:
        plan = asyncio.run(daemon.create_plan("work", planner=Planner(), plan_id="selected", assets=["role/builder"], roles={"builder": Assignment(harness="pi", model="chosen")}))
        assert plan.initiatives["a"].spec.assets == ["role/builder"]
        assert plan.initiatives["a"].spec.assignment.model == "chosen"
        assert plan.approval == "pending"
    finally:
        store.close()


def test_default_roles_and_kitchen_assignments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from herdsman.kitchen import Adapter, Kitchen, Defaults, ModelEntry, Resolution

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    _ = daemon.library_create("role", "custom", title="Project specialist")
    _ = daemon.library_create("skill", "helper", body="Help build")
    _ = daemon.library_create("role", "retired")
    _ = daemon.library_archive("role/retired")
    _ = Kitchen(adapters=[Adapter(name="pi", argv=["pi", "{prompt}"])],
        models=[ModelEntry(harness="pi", model=name) for name in ("general", "special")],
        defaults=Defaults(
        initiative=Assignment(harness="pi", model="general"),
        roles={"custom": Assignment(harness="pi", model="special")},
    )).save(tmp_path)

    resolved: list[str | None] = []
    original = Kitchen.resolve_assignment

    def resolve(self: Kitchen, *, role: str | None = None, override: Assignment | None = None) -> Resolution:
        resolved.append(role)
        return original(self, role=role, override=override)

    monkeypatch.setattr(Kitchen, "resolve_assignment", resolve)

    class Planner:
        def propose(self, brief: str) -> object:
            assert "custom — Project specialist" in brief
            assert "role/implementer" in brief
            assert "role/retired" not in brief
            refs = cast(list[str], json.loads(brief.split("Library refs: ", 1)[1].splitlines()[0]))
            assert set(refs) == {asset.ref for asset in daemon.library_browse("role")} | {"skill/helper"}
            return {"title": "Test plan", "initiatives": [
                {"id": "a", "name": "A", "brief": "work", "role": "custom"},
                {"id": "b", "name": "B", "brief": "work", "role": "implementer"},
            ]}

    try:
        plan = asyncio.run(daemon.create_plan("work", planner=Planner(), assets=["skill/helper"]))
        assert resolved == ["custom", "implementer"]
        assert plan.initiatives["a"].spec.assignment.model == "special"
        assert plan.initiatives["b"].spec.assignment.model == "general"
        assert plan.initiatives["b"].spec.role == "implementer"
        assert "skill/helper" in plan.initiatives["a"].spec.assets
        assert "skill/helper" in plan.initiatives["b"].spec.assets
    finally:
        store.close()


def test_approve_background_run_and_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    class Planner:
        def propose(self, brief: str) -> object:
            return {"title": "Test plan", "initiatives": [{"id": "a", "name": "A", "brief": brief}]}

    async def scenario() -> None:
        plan = await daemon.create_plan("work", planner=Planner())
        app = create_app(daemon)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def run(plan_id: str) -> object:
            assert plan_id == plan.id
            started.set()
            try:
                _ = await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(daemon, "run_plan", run)
        plain = await daemon.create_plan("plain", planner=Planner())
        status, payload = await post(app, {}, f"/plans/{plain.id}/approve")
        path = f"/plans/{plan.id}/approve"
        assert status == 200 and payload["approval"] == "approved"
        assert not started.is_set() and not daemon.plan_run_active(plan.id)
        status, payload = await asyncio.wait_for(post(app, {}, path, b"version=1&run=true"), 1)
        assert status == 200 and payload["approval"] == "approved"
        _ = await started.wait()
        status, _ = await post(app, {}, path, b"run=true")
        assert status == 409
        status, _ = await post(app, {}, f"/plans/{plan.id}/run")
        assert status == 409
        async with app.router.lifespan_context(app):
            pass
        assert cancelled.is_set() and not daemon.plan_run_active(plan.id)
        started.clear()
        cancelled.clear()

        async def whole_run(plan_id: str, **kwargs: object) -> object:
            del kwargs
            return await run(plan_id)

        monkeypatch.setattr(daemon, "run_plan", whole_run)
        request = asyncio.create_task(post(app, {}, f"/plans/{plan.id}/run"))
        _ = await started.wait()
        status, _ = await post(app, {}, path, b"run=true")
        assert status == 409
        await daemon.shutdown()
        _ = await asyncio.gather(request, return_exceptions=True)
        assert cancelled.is_set() and not daemon.plan_run_active(plan.id)

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_background_run_crash_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    async def crash(plan_id: str) -> object:
        del plan_id
        raise RuntimeError("scheduler crashed")

    monkeypatch.setattr(daemon, "run_plan", crash)

    async def scenario() -> None:
        daemon.start_plan_run("plan")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not daemon.plan_run_active("plan")

    try:
        asyncio.run(scenario())
        assert "background plan run failed: plan" in caplog.text
        assert "scheduler crashed" in caplog.text
    finally:
        store.close()


def test_smallest_pipeline_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from herdsman.runtime import PiFrontierPlanner

    prompts: list[str] = []

    async def invoke(self: PiFrontierPlanner, prompt: str) -> object:
        del self
        prompts.append(prompt)
        return {}

    monkeypatch.setattr(PiFrontierPlanner, "_invoke", invoke)
    _ = asyncio.run(PiFrontierPlanner(project_root=tmp_path).propose("build"))
    prompt = prompts[0]
    for requirement in (
        "Default every initiative to implementer", "real unknown/open design choice",
        "reviewer only when acceptance criteria", "test-author only when the brief names behaviour",
        "Never declare write routes for scout/architect/reviewer: the daemon assigns them",
    ):
        assert requirement in prompt


def test_dispatch_and_approve_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner
    from herdsman import cli

    calls: list[tuple[str, object, float]] = []

    def post_json(url: str, body: object, *, timeout: float) -> str:
        calls.append((url, body, timeout))
        return '{"id":"plan_1","version":2}'

    monkeypatch.setattr(cli, "_post_json", post_json)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["dispatch", "build"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": "plan_1", "version": 2}
    assert len(calls) == 1 and calls[0][1] == {"brief": "build"}
    result = runner.invoke(cli.app, ["dispatch", "-", "--yes"], input="stdin brief")
    assert result.exit_code == 0, result.output
    assert calls[-2][1] == {"brief": "stdin brief"}
    assert calls[-1][0].endswith("/plans/plan_1/approve?version=2&run=true")
    result = runner.invoke(cli.app, ["approve", "plan_1", "--version", "2", "--run"])
    assert result.exit_code == 0, result.output
    assert calls[-1][0].endswith("/approve?version=2&run=true")
    result = runner.invoke(cli.app, ["approve", "plan_1"])
    assert result.exit_code == 0, result.output
    assert calls[-1][0].endswith("/approve")


@pytest.mark.parametrize("configured", [False, True])
def test_default_role_without_kitchen_defaults_keeps_planner_assignment(
    tmp_path: Path, configured: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from herdsman.kitchen import Adapter, Kitchen, ModelEntry, Resolution

    if configured:
        _ = Kitchen(
            adapters=[Adapter(name="pi", argv=["pi", "{prompt}"])],
            models=[ModelEntry(harness="pi", model="planner-chosen")],
        ).save(tmp_path)
    else:
        assert not (tmp_path / ".herdsman" / "kitchen.json").exists()

    def unexpected_resolution(self: Kitchen, *, role: str | None = None, override: Assignment | None = None) -> Resolution:
        del self, role, override
        pytest.fail("roles without Kitchen defaults must retain the proposal assignment")

    monkeypatch.setattr(Kitchen, "resolve_assignment", unexpected_resolution)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    assignment = Assignment(harness="pi", model="planner-chosen")

    class Planner:
        def propose(self, brief: str) -> object:
            assert "role/implementer" in brief
            return {"title": "Test plan", "initiatives": [{
                "id": "a", "name": "A", "brief": "work", "role": "implementer",
                "assignment": assignment.model_dump(mode="json"),
            }]}

    try:
        plan = asyncio.run(daemon.create_plan("work", planner=Planner()))
        assert plan.initiatives["a"].spec.assignment == assignment
        assert plan.initiatives["a"].spec.role == "implementer"
        assert plan.approval == "pending"
    finally:
        store.close()


def test_default_roles_do_not_bind_a_contract_to_legacy_proposals(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)

    class Planner:
        def propose(self, brief: str) -> object:
            del brief
            return {"title": "Test plan", "initiatives": [{"id": "a", "name": "A", "brief": "work"}]}

    try:
        plan = asyncio.run(daemon.create_plan("work", planner=Planner()))
        assert plan.initiatives["a"].spec.role is None
        assert plan.initiatives["a"].spec.assets == []
        approved = daemon.approve_plan(plan.id)
        assert approved.contract_for("a") is None
    finally:
        store.close()
