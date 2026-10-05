"""Interactive planner file/session seam; no executor runtime scaffolding."""

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from herdsman.classes import PlanProposed
from herdsman.daemon import Daemon
from herdsman.herdr import AgentLaunch, HerdrAdapter, HerdrUnavailable
from herdsman.runtime import PiFrontierPlanner, PlannerError
from herdsman.store import EventStore


def test_proposal_and_recalibration_record_versioned_sessions_and_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches: list[AgentLaunch] = []
    focused: list[str] = []

    async def visible(
        _self: HerdrAdapter, launch: AgentLaunch, *, label: str, timeout: float,
        on_pane: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        launches.append(launch)
        version = len(launches)
        assert label == "planner p"
        assert timeout > 0
        path = tmp_path / ".herdsman/planner" / f"p-{version}.json"
        assert str(path) in Path(launch.prompt.removeprefix("Read ").split(" ", 1)[0]).read_text()
        if on_pane is not None:
            on_pane(f"w{version}:p1")
        payload: dict[str, object] = {"initiatives": [{
            "id": "a", "name": "a", "brief": f"version {version}",
            "assignment": {"harness": "pi", "model": "default"},
        }]}
        if version == 1:
            payload["title"] = "  Build the feature  "
        _ = path.write_text(json.dumps(payload))
        return {"agent_session": {
            "agent": "pi", "kind": "path", "value": f"/session-{version}.jsonl",
            "source": "herdr:pi",
        }}

    async def focus(_self: HerdrAdapter, pane: str) -> None:
        focused.append(pane)

    monkeypatch.setattr(HerdrAdapter, "run_agent_visible", visible)
    monkeypatch.setattr(HerdrAdapter, "focus_pane", focus)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    try:
        _ = asyncio.run(daemon.create_plan("build", plan_id="p"))
        _ = asyncio.run(daemon.recalibrate("p", reason="revise"))
        assert asyncio.run(daemon.focus_planner("p")) == "w2:p1"
        assert focused == ["w2:p1"]
        proposals = [event for event in store.read("p") if isinstance(event, PlanProposed)]
        assert [event.session.value for event in proposals if event.session] == [
            "/session-1.jsonl", "/session-2.jsonl",
        ]
        plan = store.load("p")
        assert [event.title for event in proposals] == ["Build the feature", None]
        assert plan.title == "Build the feature"
        assert [(entry.version, entry.value) for entry in plan.planner_sessions] == [
            (1, "/session-1.jsonl"), (2, "/session-2.jsonl"),
        ]
        assert plan.model_dump(mode="json")["planner_sessions"][1]["version"] == 2
        assert "CONTEXT=" in Path(launches[1].prompt.removeprefix("Read ").split(" ", 1)[0]).read_text()
    finally:
        store.close()


@pytest.mark.parametrize("payload", [
    {"initiatives": [{"id": "a", "name": "a", "brief": "build"}]},
    {"title": "  ", "initiatives": [{"id": "a", "name": "a", "brief": "build"}]},
])
def test_fresh_planner_missing_title_persists_nothing(
    payload: dict[str, object], tmp_path: Path
) -> None:
    class Planner:
        def propose(self, _brief: str) -> object:
            return payload

    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    try:
        with pytest.raises(PlannerError, match="non-blank title"):
            _ = asyncio.run(daemon.create_plan("build", plan_id="p", planner=Planner()))
        assert store.read("p") == []
    finally:
        store.close()


@pytest.mark.skipif(os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1", reason="needs live herdr")
def test_real_herdr_planner_writes_file_and_reports_session(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    path = tmp_path / ".herdsman/planner/p-live-1.json"
    planner = PiFrontierPlanner(
        project_root=tmp_path, model="openai-codex/gpt-6.1-sol", effort="medium",
        output_path=path, pane=lambda launch, timeout: daemon._planner_pane("p-live", launch, timeout),  # pyright: ignore[reportPrivateUsage]
    )

    async def scenario() -> None:
        try:
            result = await planner._invoke(  # pyright: ignore[reportPrivateUsage]
                'Return JSON only, with "initiatives": []. Do not inspect the project or change other files.'
            )
            assert result == {"initiatives": []}
            assert planner.last_session is not None and planner.last_session.value
            assert path.is_file()
        finally:
            pane = daemon._planner_panes.get("p-live")  # pyright: ignore[reportPrivateUsage]
            if pane is not None:
                process = await asyncio.create_subprocess_exec("herdr", "workspace", "close", pane.split(":")[0])
                assert await process.wait() == 0

    try:
        asyncio.run(asyncio.wait_for(scenario(), 180))
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
def test_an_exposed_planner_pane_never_falls_back_headless(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def visible(
        _self: HerdrAdapter, _launch: AgentLaunch, *, label: str, timeout: float,
        on_pane: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        assert label and timeout > 0
        assert on_pane is not None
        on_pane("w1:p1")
        if failure == "timeout":
            raise asyncio.TimeoutError
        if failure == "cancel":
            raise asyncio.CancelledError
        raise HerdrUnavailable("lost acknowledgement after allocation")

    async def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("must not launch a duplicate planner")

    monkeypatch.setattr(HerdrAdapter, "run_agent_visible", visible)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store, project_root=tmp_path)
    try:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else PlannerError):
            _ = asyncio.run(daemon.create_plan("build", plan_id="p"))
        assert store.read("p") == []
        assert daemon._planner_panes["p"] == "w1:p1"  # pyright: ignore[reportPrivateUsage]
    finally:
        store.close()
