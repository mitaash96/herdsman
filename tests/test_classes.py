from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError
from pydantic import TypeAdapter

from herdsman.classes import (
    AttemptStarted,
    Assignment,
    Checkpoint,
    CheckpointRecorded,
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
    SubtaskAdvanced,
)

AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")


def stream() -> list[Event]:
    api = InitiativeSpec(
        id="init_a",
        name="api",
        brief="add a health endpoint",
        assignment=LUNA,
        routes=Routes(reads=["src/**"], writes=["src/api/**"]),
        subtasks=["write the route", "wire it up"],
    )
    tests = InitiativeSpec(
        id="init_c",
        name="tests",
        brief="cover it",
        assignment=LUNA,
        routes=Routes(writes=["tests/**"]),
        depends_on=["init_a"],
    )
    return [
        PlanCreated(plan_id="plan_1", at=AT, brief="add a health endpoint"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[api, tests]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1",
            at=AT,
            attempt_id="att_1",
            initiative_id="init_a",
            assignment=LUNA,
            worktree_ref="wt_1",
            pane_ref="p_9f",
        ),
        SubtaskAdvanced(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            subtask_id="init_a.1", state="done",
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id="cp_1", attempt_id="att_1",
                changed_paths=["src/api/health.py"], exit_code=0,
            ),
        ),
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
        ),
    ]


def test_fold_reconstructs_state():
    plan = Plan.fold(stream())

    assert plan.brief == "add a health endpoint"
    assert plan.approval == "approved"
    assert plan.planner is None  # direct path
    assert plan.initiatives["init_a"].state == "settled"
    assert plan.initiatives["init_c"].state == "pending"

    api = plan.initiatives["init_a"]
    assert [s.id for s in api.subtasks] == ["init_a.1", "init_a.2"]
    assert [s.state for s in api.subtasks] == ["done", "todo"]
    checkpoint = api.attempts[0].checkpoint
    assert checkpoint is not None
    assert checkpoint.changed_paths == ["src/api/health.py"]
    assert api.attempts[0].ended_at == AT


def test_readiness_follows_dependencies():
    events = stream()
    assert Plan.fold(events[:-1]).ready() == []  # init_a still running
    assert Plan.fold(events).ready() == ["init_c"]


def test_plan_approval_is_folded_and_reproposal_requires_approval_again():
    events = stream()[:2]
    approved = PlanApproved(plan_id="plan_1", at=AT, version=1)

    plan = Plan.fold(events + [approved])
    assert plan.approval == "approved"

    revised = InitiativeSpec(
        id="init_a", name="revised api", brief="revised", assignment=LUNA
    )
    pending = Plan.fold(events + [approved, PlanProposed(
        plan_id="plan_1", at=AT, version=2, initiatives=[revised]
    )])
    assert pending.approval == "pending"


def test_plan_approval_rejects_invalid_transitions():
    proposed = stream()[:2]

    with pytest.raises(ValueError, match="already approved"):
        _ = Plan.fold(proposed + [
            PlanApproved(plan_id="plan_1", at=AT, version=1),
            PlanApproved(plan_id="plan_1", at=AT, version=1),
        ])

    with pytest.raises(ValueError, match="current version is 1"):
        _ = Plan.fold(proposed + [PlanApproved(
            plan_id="plan_1", at=AT, version=2
        )])

    with pytest.raises(ValueError, match="no proposed initiatives"):
        _ = Plan.fold([
            PlanCreated(plan_id="plan_1", at=AT, brief="x"),
            PlanApproved(plan_id="plan_1", at=AT, version=1),
        ])


def test_proposed_fixture_dag_is_accepted():
    root_a = InitiativeSpec(
        id="init_a", name="a", brief="a", assignment=LUNA
    )
    root_b = InitiativeSpec(
        id="init_b", name="b", brief="b", assignment=LUNA
    )
    join = InitiativeSpec(
        id="init_c", name="c", brief="c", assignment=LUNA,
        depends_on=["init_a", "init_b"],
    )

    plan = Plan.fold([
        PlanCreated(plan_id="plan_1", at=AT, brief="x"),
        PlanProposed(
            plan_id="plan_1", at=AT, version=1,
            initiatives=[root_a, root_b, join],
        ),
    ])

    assert plan.ready() == ["init_a", "init_b"]


def test_proposed_plan_rejects_unknown_dependencies():
    orphan = InitiativeSpec(
        id="init_orphan",
        name="orphan",
        brief="depends on an absent initiative",
        assignment=LUNA,
        depends_on=["missing"],
    )

    with pytest.raises(ValidationError, match="unknown initiative missing"):
        _ = PlanProposed(
            plan_id="plan_1", at=AT, version=1, initiatives=[orphan]
        )


def test_proposed_plan_rejects_duplicate_ids():
    first = InitiativeSpec(id="init_a", name="a", brief="a", assignment=LUNA)
    duplicate = InitiativeSpec(id="init_a", name="b", brief="b", assignment=LUNA)

    with pytest.raises(ValidationError, match="duplicate initiative init_a"):
        _ = PlanProposed(
            plan_id="plan_1", at=AT, version=1,
            initiatives=[first, duplicate],
        )


def test_proposed_plan_rejects_cycles():
    first = InitiativeSpec(
        id="init_a", name="a", brief="a", assignment=LUNA, depends_on=["init_b"]
    )
    second = InitiativeSpec(
        id="init_b", name="b", brief="b", assignment=LUNA, depends_on=["init_a"]
    )

    with pytest.raises(ValidationError, match="dependencies must be acyclic"):
        _ = PlanProposed(
            plan_id="plan_1", at=AT, version=1, initiatives=[first, second]
        )

    self_referencing = InitiativeSpec(
        id="init_c", name="c", brief="c", assignment=LUNA, depends_on=["init_c"]
    )
    with pytest.raises(ValidationError, match="dependencies must be acyclic"):
        _ = PlanProposed(
            plan_id="plan_1", at=AT, version=1, initiatives=[self_referencing]
        )


def test_fold_is_deterministic_across_a_restart():
    """Gate 0 exit: restart reconstructs state without replaying work."""
    events = stream()
    assert Plan.fold(events) == Plan.fold(events)


def test_events_round_trip_through_the_discriminated_union():
    adapter = TypeAdapter(list[Event])
    events = stream()
    revived = adapter.validate_python(adapter.dump_python(events))
    assert revived == events
    assert Plan.fold(revived) == Plan.fold(events)


def test_unknown_references_are_rejected():
    bad = [
        PlanCreated(plan_id="plan_1", at=AT, brief="x"),
        SubtaskAdvanced(
            plan_id="plan_1", at=AT, initiative_id="nope",
            subtask_id="nope.1", state="done",
        ),
    ]
    try:
        _ = Plan.fold(bad)
    except ValueError as exc:
        assert "unknown initiative" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_attempt_requires_plan_approval():
    api = InitiativeSpec(id="init_a", name="a", brief="a", assignment=LUNA)
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="a", planner=LUNA),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[api]),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="init_a",
            assignment=LUNA,
        ),
    ]

    with pytest.raises(ValueError, match="approved"):
        _ = Plan.fold(events)


def test_settlement_requires_a_recorded_checkpoint():
    events = stream()[:-1] + [
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="nope"
        )
    ]

    with pytest.raises(ValueError):
        _ = Plan.fold(events)


def test_settlement_checkpoint_must_belong_to_the_initiative():
    other_attempt = AttemptStarted(
        plan_id="plan_1",
        at=AT,
        attempt_id="att_c",
        initiative_id="init_c",
        assignment=LUNA,
    )
    other_checkpoint = CheckpointRecorded(
        plan_id="plan_1",
        at=AT,
        checkpoint=Checkpoint(id="cp_c", attempt_id="att_c", exit_code=0),
    )
    events = stream()[:4] + [other_attempt, other_checkpoint] + [
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_c"
        )
    ]

    with pytest.raises(ValueError):
        _ = Plan.fold(events)


def test_reproposal_rejects_the_current_version():
    events = stream()[:2]

    with pytest.raises(ValueError, match="version must advance"):
        _ = Plan.fold(events + [
            PlanProposed(
                plan_id="plan_1",
                at=AT,
                version=1,
                initiatives=[cast(PlanProposed, events[1]).initiatives[0]],
            )
        ])


def test_settlement_rejects_a_duplicate_and_accepts_retained_failed_evidence():
    events = stream()
    with pytest.raises(ValueError, match="only a running or failed"):
        _ = Plan.fold(events + [
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            )
        ])

    with pytest.raises(ValueError, match="only a running or failed"):
        _ = Plan.fold(stream()[:-1] + [
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            ),
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            ),
        ])

    # Dirty evidence is retained rather than discarded, so the operator has to
    # be able to accept it by hand and release whatever depends on it.
    overridden = Plan.fold(stream()[:-1] + [
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="checks failed"
        ),
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
        ),
    ])
    assert overridden.initiatives["init_a"].state == "settled"
    assert overridden.ready() == ["init_c"]


def test_reproposal_removes_omitted_initiatives_and_preserves_survivors():
    revised_api = InitiativeSpec(
        id="init_a",
        name="revised api",
        brief="replace the health endpoint",
        assignment=LUNA,
        routes=Routes(writes=["src/api/**"]),
    )
    events = stream() + [
        PlanProposed(
            plan_id="plan_1", at=AT, version=2, initiatives=[revised_api]
        )
    ]

    plan = Plan.fold(events)

    assert set(plan.initiatives) == {"init_a"}
    initiative = plan.initiatives["init_a"]
    assert initiative.spec.brief == "replace the health endpoint"
    assert initiative.state == "settled"
    assert [attempt.id for attempt in initiative.attempts] == ["att_1"]
    assert initiative.attempts[0].checkpoint is not None


def test_fold_rejects_events_from_another_plan():
    events = stream() + [
        RuntimeObserved(
            plan_id="plan_2", at=AT, attempt_id="att_1", kind="heartbeat"
        )
    ]

    with pytest.raises(ValueError):
        _ = Plan.fold(events)


def test_event_nested_specs_are_immutable():
    proposed = stream()[1]
    assert isinstance(proposed, PlanProposed)

    with pytest.raises((TypeError, ValidationError)):
        proposed.initiatives[0].brief = "mutated"

    with pytest.raises((TypeError, ValidationError)):
        _ = proposed.initiatives[0].subtasks.append("mutated")
