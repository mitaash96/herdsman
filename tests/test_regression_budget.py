from datetime import UTC, datetime
from pathlib import Path

import pytest

from herdsman.classes import (
    Assignment,
    Event,
    Initiative,
    InitiativeSpec,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
)
from herdsman.daemon import Daemon
from herdsman.store import EventStore


AT = datetime(2026, 9, 18, tzinfo=UTC)
ASSIGNMENT = Assignment(harness="luna", model="cheap")


def events(nodes: int, observations: int = 0) -> list[Event]:
    specs = [
        InitiativeSpec(
            id=f"i{number}",
            name=f"initiative {number}",
            brief="do it",
            assignment=ASSIGNMENT,
            routes=Routes(writes=[f"src/{number}.py"]),
        )
        for number in range(nodes)
    ]
    return [
        PlanCreated(plan_id="p", at=AT, brief="ship it"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=specs),
        PlanApproved(plan_id="p", at=AT, version=1),
        *[
            RuntimeObserved(
                plan_id="p",
                at=AT,
                attempt_id="routine",
                kind="heartbeat",
            )
            for _ in range(observations)
        ],
    ]


def store_events(path: Path, recorded: list[Event]) -> None:
    store = EventStore(path)
    try:
        for event in recorded:
            _ = store.append(event)
    finally:
        store.close()


def test_routine_operator_queries_record_zero_model_tokens(tmp_path: Path) -> None:
    """Reading and waiting are projections, never hidden model work."""
    path = tmp_path / "events.db"
    store_events(path, events(8, observations=20))
    store = EventStore(path)
    daemon = Daemon(store, project_root=tmp_path)
    try:
        before = store.read("p")

        _ = daemon.plan("p")
        _ = daemon.replay("p")
        graph = daemon.graph("p")
        _ = daemon.status("p")  # status is also the CLI wait source
        _ = daemon.digest("p")
        fleet = daemon.fleet(now=AT)
        _ = daemon.while_away()
        assert store.plans() == ["p"]  # the local discovery/doctor-shaped read

        ledger = daemon.tokens("p")
        assert store.read("p") == before
        assert ledger.entries == []
        assert ledger.productive_tokens == ledger.orchestration_tokens == 0
        assert graph.overhead.productive_tokens == graph.overhead.orchestration_tokens == 0
        assert fleet.spend.accounted == 0
        assert fleet.spend.sources == []
    finally:
        store.close()


def test_long_history_folds_once_and_readiness_is_one_pass_per_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History and node growth stay one pass; CI timing is intentionally irrelevant."""
    path = tmp_path / "events.db"
    recorded = events(48, observations=512)
    store_events(path, recorded)

    step_calls = 0
    readiness_calls = 0
    original_step = Plan.step
    original_dependencies_released = Plan.dependencies_released

    def counted_step(
        cls: type[Plan], plan: Plan | None, event: Event
    ) -> Plan:
        nonlocal step_calls
        del cls
        step_calls += 1
        return original_step(plan, event)

    def counted_dependencies_released(
        self: Plan, initiative: Initiative
    ) -> bool:
        nonlocal readiness_calls
        readiness_calls += 1
        return original_dependencies_released(self, initiative)

    monkeypatch.setattr(Plan, "step", classmethod(counted_step))
    monkeypatch.setattr(Plan, "dependencies_released", counted_dependencies_released)

    store = EventStore(path)
    try:
        graph = Daemon(store, project_root=tmp_path).graph("p")
        assert len(graph.nodes) == 48
        assert step_calls == len(recorded)
        assert readiness_calls == len(graph.nodes)
    finally:
        store.close()
