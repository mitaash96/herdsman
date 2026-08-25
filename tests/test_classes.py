from datetime import UTC, datetime

from pydantic import TypeAdapter

from herdsman.classes import (
    AttemptStarted,
    Assignment,
    Checkpoint,
    CheckpointRecorded,
    Event,
    InitiativeSettled,
    InitiativeSpec,
    Plan,
    PlanCreated,
    PlanProposed,
    Routes,
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
        Plan.fold(bad)
    except ValueError as exc:
        assert "unknown initiative" in str(exc)
    else:
        raise AssertionError("expected a ValueError")
