import sqlite3
from pathlib import Path

import pytest

from herdsman.classes import (
    InitiativeFailed,
    InitiativePaused,
    InitiativeResumed,
    Plan,
    PlanApproved,
    SubtaskAdvanced,
)
from herdsman.store import EventStore
from tests.test_classes import AT, failing_round, signature_base, stream


def test_a_plan_survives_a_restart(tmp_path: Path):
    """Gate 0 exit: restart reconstructs the plan without replaying work."""
    events = stream()
    path = tmp_path / ".herdsman" / "events.db"

    store = EventStore(path)
    seqs = [store.append(ev).seq for ev in events]
    store.close()

    reopened = EventStore(path)
    assert reopened.plans() == ["plan_1"]
    assert reopened.read("plan_1") == [
        ev.model_copy(update={"seq": seq}) for ev, seq in zip(events, seqs)
    ]
    assert seqs == sorted(seqs)

    plan = reopened.load("plan_1")
    assert plan == Plan.fold(events)
    assert plan.initiatives["init_a"].state == "settled"
    assert plan.ready() == ["init_c"]
    reopened.close()


def test_approval_survives_a_restart(tmp_path: Path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    _ = store.append(stream()[0])
    _ = store.append(stream()[1])
    _ = store.append(PlanApproved(plan_id="plan_1", at=AT, version=1))
    store.close()

    reopened = EventStore(path)
    try:
        assert reopened.load("plan_1").approval == "approved"
        assert isinstance(reopened.read("plan_1")[-1], PlanApproved)
    finally:
        reopened.close()


def test_insert_failure_does_not_advance_cached_projection(tmp_path: Path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:2]:
            _ = store.append(event)
        _ = store.db.execute(
            """CREATE TRIGGER reject_approval BEFORE INSERT ON events
            WHEN NEW.type = 'plan_approved'
            BEGIN SELECT RAISE(FAIL, 'forced failure'); END"""
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
            _ = store.append(PlanApproved(plan_id="plan_1", at=AT, version=1))

        live_events = store.read("plan_1")
        live_plan = store.load("plan_1")
        assert live_plan.approval == "pending"
    finally:
        store.close()

    reopened = EventStore(path)
    try:
        assert reopened.read("plan_1") == live_events
        assert reopened.load("plan_1") == live_plan
        assert reopened.load("plan_1").approval == "pending"
    finally:
        reopened.close()


def test_invalid_approval_is_never_written(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    try:
        for event in stream()[:2]:
            _ = store.append(event)

        with pytest.raises(ValueError, match="current version is 1"):
            _ = store.append(PlanApproved(plan_id="plan_1", at=AT, version=2))

        assert [event.type for event in store.read("plan_1")] == [
            "plan_created", "plan_proposed"
        ]
    finally:
        store.close()


def test_an_unfoldable_event_is_never_written(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    for ev in stream():
        _ = store.append(ev)

    with pytest.raises(ValueError):
        _ = store.append(
            SubtaskAdvanced(
                plan_id="plan_1", at=AT, initiative_id="nope",
                subtask_id="nope.1", state="done",
            )
        )

    assert len(store.read("plan_1")) == len(stream())
    assert store.load("plan_1") == Plan.fold(stream())


# --- Sprint 5: durable recovery substrate ------------------------------------


def test_a_repeated_action_request_is_never_written(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    try:
        for ev in stream()[:2]:
            _ = store.append(ev)
        _ = store.append(
            PlanApproved(plan_id="plan_1", at=AT, version=1, action_id="act_1")
        )

        with pytest.raises(ValueError, match="already recorded as plan_approved"):
            _ = store.append(
                PlanApproved(plan_id="plan_1", at=AT, version=1, action_id="act_1")
            )

        assert [ev.action_id for ev in store.read("plan_1")] == [None, None, "act_1"]
    finally:
        store.close()


def test_recovery_lifecycle_and_idempotency_survive_a_restart(tmp_path: Path):
    """Pause/resume folds and the action_id index are rebuilt from disk, so a
    reopened store refuses a repeated request exactly like the live one."""
    events = stream()[:-1] + [
        InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_a"),
        InitiativeResumed(plan_id="plan_1", at=AT, initiative_id="init_a"),
    ]
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for ev in events:
            _ = store.append(ev)
    finally:
        store.close()

    reopened = EventStore(path)
    try:
        plan = reopened.load("plan_1")
        assert plan.initiatives["init_a"].state == "failed"  # history exists
        assert plan.action_ids == {}
        assert plan == Plan.fold(events)

        failure = InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            reason="daemon death: pane p_9f missing", action_id="act_1",
        )
        _ = reopened.append(failure)
        with pytest.raises(ValueError, match="already recorded"):
            _ = reopened.append(failure)
        assert [ev.action_id for ev in reopened.read("plan_1")].count("act_1") == 1
    finally:
        reopened.close()


def test_repeated_failure_promotion_survives_a_restart(tmp_path: Path):
    """The promoted leaf and signature counts are fold output, so a reopened
    store projects them identically without any persisted leaf record."""
    events = signature_base("AssertionError: row 42 missing") + failing_round(
        "att_2", "uv run pytest -q", "assertionerror: row 7 missing"
    )
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for ev in events:
            _ = store.append(ev)
    finally:
        store.close()

    reopened = EventStore(path)
    try:
        plan = reopened.load("plan_1")
        leaves = [leaf for leaf in plan.memory_leaves if leaf.origin == "failure"]
        assert len(leaves) == 1
        assert leaves[0].subject == "init_a.failure"
        assert "att_1" in leaves[0].claim and "att_2" in leaves[0].claim
        record = plan.failure_signatures[
            ("init_a", "uv run pytest -q", "assertionerror: row # missing")
        ]
        assert record.count == 2
        assert record.attempts == ["att_1", "att_2"]
    finally:
        reopened.close()
