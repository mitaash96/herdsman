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


async def post(app: FastAPI, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    sent: list[Message] = []
    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    async def send(message: Message) -> None:
        sent.append(message)
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/plans", "raw_path": b"/plans",
        "query_string": b"", "headers": [(b"content-type", b"application/json")],
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
            return {"initiatives": [{"id": "first", "name": "First", "brief": brief}]}

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
    _ = daemon.library_create("role", "builder", body="Build carefully")

    class Planner:
        def propose(self, brief: str) -> object:
            assert "role/builder" in brief
            return {"initiatives": [{"id": "a", "name": "A", "brief": "work", "role": "builder"}]}

    try:
        plan = asyncio.run(daemon.create_plan("work", planner=Planner(), plan_id="selected", assets=["role/builder"], roles={"builder": Assignment(harness="pi", model="chosen")}))
        assert plan.initiatives["a"].spec.assets == ["role/builder"]
        assert plan.initiatives["a"].spec.assignment.model == "chosen"
        assert plan.approval == "pending"
    finally:
        store.close()
