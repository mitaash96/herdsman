"""Interactive planner file/session seam; no executor runtime scaffolding."""

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from herdsman.classes import Decomposition, PlanCreated, PlanProposed
from herdsman.daemon import Daemon
from herdsman.herdr import AgentLaunch, HerdrAdapter, HerdrUnavailable
from herdsman.runtime import PiFrontierPlanner, PlannerError
from herdsman.store import EventStore


class _DecompositionPlanner:
    def __init__(self, count: int = 1, split_note: str | None = None) -> None:
        self.count: int = count
        self.split_note: str | None = split_note
        self.contexts: list[str] = []

    def propose(self, context: str) -> object:
        self.contexts.append(context)
        payload: dict[str, object] = {
            "title": "Build the feature",
            "initiatives": [
                {"id": f"member_{index}", "name": f"Member {index}", "brief": "build"}
                for index in range(self.count)
            ],
        }
        if self.split_note is not None:
            payload["split_note"] = self.split_note
        return payload


@pytest.fixture
def planning_daemon(tmp_path: Path) -> Iterator[Daemon]:
    store = EventStore(tmp_path / "events.db")
    try:
        yield Daemon(store, project_root=tmp_path)
    finally:
        store.close()


def _no_library_roles(_kind: str) -> list[object]:
    return []


_LEGACY_ROLE_REQUIREMENTS = (
    'Library refs: ["role/architect", "role/implementer", "role/reviewer", '
    '"role/scout", "role/test-author"]\n'
    "Roles:\n"
    "architect — Architect: makes the binding decision, writes no code\n"
    "implementer — Implementer: makes the change inside its write routes\n"
    "reviewer — Reviewer: judges upstream work against its brief, changes nothing\n"
    "scout — Scout: investigates and returns a decision packet, changes no code\n"
    "test-author — Test author: tests the change against its acceptance, never edits production code\n"
)


@pytest.mark.parametrize("bundled_roles", [False, True])
@pytest.mark.parametrize("explicit_default", [False, True])
@pytest.mark.parametrize("acceptance", ["", "Keep the public API unchanged."])
def test_default_decomposition_preserves_legacy_context_byte_for_byte(
    planning_daemon: Daemon, explicit_default: bool, acceptance: str,
    bundled_roles: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not bundled_roles:
        monkeypatch.setattr(planning_daemon, "library_browse", _no_library_roles)
    planner = _DecompositionPlanner()
    if explicit_default:
        _ = asyncio.run(planning_daemon.create_plan(
            "build", plan_id="p", planner=planner, acceptance=acceptance,
            decomposition="default",
        ))
    else:
        _ = asyncio.run(planning_daemon.create_plan(
            "build", plan_id="p", planner=planner, acceptance=acceptance,
        ))
    expected = "build"
    if acceptance or bundled_roles:
        expected += "\n\nDISPATCH REQUIREMENTS (operator selected):\n"
    if acceptance:
        expected += "Acceptance criteria:\nKeep the public API unchanged.\n"
    if bundled_roles:
        expected += _LEGACY_ROLE_REQUIREMENTS
    assert planner.contexts == [expected]
    created = next(event for event in planning_daemon.store.read("p") if isinstance(event, PlanCreated))
    assert created.decomposition == "default"


@pytest.mark.parametrize("bundled_roles", [False, True])
@pytest.mark.parametrize("acceptance", ["", "Keep the public API unchanged."])
def test_lean_decomposition_adds_exactly_one_requirement_line(
    planning_daemon: Daemon, acceptance: str, bundled_roles: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not bundled_roles:
        monkeypatch.setattr(planning_daemon, "library_browse", _no_library_roles)
    planner = _DecompositionPlanner(count=2)
    _ = asyncio.run(planning_daemon.create_plan(
        "build", plan_id="p", planner=planner, acceptance=acceptance,
        decomposition="lean",
    ))
    expected = "build\n\nDISPATCH REQUIREMENTS (operator selected):\n"
    if acceptance:
        expected += "Acceptance criteria:\nKeep the public API unchanged.\n"
    if bundled_roles:
        expected += _LEGACY_ROLE_REQUIREMENTS
    line = (
        "Decomposition effort: lean — aim for 2–3 initiatives. "
        "If the work does not honestly fit that band, return your best decomposition "
        "anyway and set top-level split_note to one sentence explaining why.\n"
    )
    assert len(line.splitlines()) == 1
    assert planner.contexts == [expected + line]
    created = next(event for event in planning_daemon.store.read("p") if isinstance(event, PlanCreated))
    assert created.decomposition == "lean"


@pytest.mark.parametrize(("level", "count", "expected_note"), [
    ("single", 1, None),
    ("single", 2, "Planner returned 2 members; single asks for 1."),
    ("lean", 1, "Planner returned 1 members; lean asks for 2–3."),
    ("lean", 2, None),
    ("lean", 3, None),
    ("lean", 7, "Planner returned 7 members; lean asks for 2–3."),
    ("wide", 4, "Planner returned 4 members; wide asks for 5–8."),
    ("wide", 5, None),
    ("wide", 8, None),
    ("wide", 9, "Planner returned 9 members; wide asks for 5–8."),
    ("max", 8, "Planner returned 8 members; max asks for 9 or more."),
    ("max", 9, None),
    ("default", 9, None),
])
def test_decomposition_count_mismatch_records_fallback_without_rejecting_or_retrying(
    planning_daemon: Daemon, level: Decomposition, count: int,
    expected_note: str | None,
) -> None:
    planner = _DecompositionPlanner(count=count)
    plan = asyncio.run(planning_daemon.create_plan(
        "build", plan_id="p", planner=planner, decomposition=level,
    ))
    proposals = [event for event in planning_daemon.store.read("p") if isinstance(event, PlanProposed)]
    assert len(planner.contexts) == len(proposals) == 1
    assert len(plan.initiatives) == count
    assert proposals[0].split_note == expected_note


def test_planner_split_note_wins_over_decomposition_fallback(
    planning_daemon: Daemon,
) -> None:
    note = "Seven independent write routes need separate owners."
    planner = _DecompositionPlanner(count=7, split_note=note)
    _ = asyncio.run(planning_daemon.create_plan(
        "build", plan_id="p", planner=planner, decomposition="lean",
    ))
    proposal = next(event for event in planning_daemon.store.read("p") if isinstance(event, PlanProposed))
    assert proposal.split_note == note
    assert len(proposal.initiatives) == 7
    assert len(planner.contexts) == 1


def test_old_plan_created_without_decomposition_replays_as_default(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    payload = {
        "type": "plan_created", "plan_id": "p",
        "at": "2026-10-01T00:00:00Z", "brief": "legacy brief",
    }
    store = EventStore(path)
    try:
        _ = store.db.execute(
            "INSERT INTO events (plan_id, at, type, payload) VALUES (?, ?, ?, ?)",
            ("p", payload["at"], "plan_created", json.dumps(payload)),
        )
    finally:
        store.close()
    replay = EventStore(path)
    try:
        events = replay.read("p")
        assert len(events) == 1
        created = events[0]
        assert isinstance(created, PlanCreated)
        assert created.decomposition == "default"
        assert replay.load("p").brief == "legacy brief"
    finally:
        replay.close()

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
        assert timeout is None or timeout > 0
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
        assert label and (timeout is None or timeout > 0)
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
