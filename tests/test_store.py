from pathlib import Path

import pytest

from herdsman.classes import Plan, SubtaskAdvanced
from herdsman.store import EventStore
from tests.test_classes import AT, stream


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
