"""Fleet and attention substrate: rollups, classification, digest, archive."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    Checkpoint,
    CheckpointApproved,
    CheckpointRecorded,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    Plan,
    PlanApproved,
    PlanArchived,
    PlanCreated,
    PlanProposed,
    PlanUnarchived,
    Routes,
    RuntimeObserved,
    Usage,
)
from herdsman.fleet import (
    DEFAULT_STALL_SECONDS,
    attention,
    digest,
    fleet,
    notifications,
    run_rollup,
    run_status,
)

AT = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")


def spec(
    initiative_id: str, approval: Literal["automatic", "required"] = "automatic"
) -> InitiativeSpec:
    return InitiativeSpec(
        id=initiative_id,
        name=initiative_id,
        brief=f"do {initiative_id}",
        assignment=LUNA,
        routes=Routes(writes=[f"src/{initiative_id}/**"]),
        approval=approval,
    )


def proposed(*specs: InitiativeSpec, plan_id: str = "plan_1") -> list[Event]:
    return [
        PlanCreated(plan_id=plan_id, at=AT, brief="ship it"),
        PlanProposed(plan_id=plan_id, at=AT, version=1, initiatives=list(specs)),
    ]


def approved(*specs: InitiativeSpec, plan_id: str = "plan_1") -> list[Event]:
    return [
        *proposed(*specs, plan_id=plan_id),
        PlanApproved(plan_id=plan_id, at=AT, version=1),
    ]


def started(
    initiative_id: str,
    attempt_id: str,
    *,
    at: datetime = AT,
    plan_id: str = "plan_1",
    origin: Literal["run", "retry"] = "run",
) -> AttemptStarted:
    return AttemptStarted(
        plan_id=plan_id,
        at=at,
        attempt_id=attempt_id,
        initiative_id=initiative_id,
        assignment=LUNA,
        origin=origin,
    )


def fold(events: Sequence[Event]) -> Plan:
    return Plan.fold(events)


# --- archive -----------------------------------------------------------------


def test_archive_and_unarchive_fold_from_events():
    events = [*approved(spec("a")), PlanArchived(plan_id="plan_1", at=AT)]
    assert fold(events).archived is True

    events.append(PlanUnarchived(plan_id="plan_1", at=AT))
    assert fold(events).archived is False


def test_archive_refuses_contradictions():
    plan = fold([*approved(spec("a")), PlanArchived(plan_id="plan_1", at=AT)])
    with pytest.raises(ValueError, match="already archived"):
        _ = Plan.step(plan, PlanArchived(plan_id="plan_1", at=AT))
    with pytest.raises(ValueError, match="not archived"):
        _ = Plan.step(
            fold(approved(spec("a"))), PlanUnarchived(plan_id="plan_1", at=AT)
        )


def test_old_streams_replay_unarchived():
    """Archive state is additive: a Sprint 9 stream folds identically."""
    assert fold(approved(spec("a"))).archived is False


def test_archived_runs_leave_active_navigation_but_stay_counted():
    live = run_rollup(fold(approved(spec("a"))), approved(spec("a")))
    filed_events = [
        *approved(spec("b"), plan_id="plan_2"),
        PlanArchived(plan_id="plan_2", at=AT),
    ]
    filed = run_rollup(fold(filed_events), filed_events)

    active = fleet([live, filed])
    assert [run.plan_id for run in active.runs] == ["plan_1"]
    assert active.archived == 1
    assert active.total_runs == 1

    everything = fleet([live, filed], include_archived=True)
    assert {run.plan_id for run in everything.runs} == {"plan_1", "plan_2"}
    assert everything.archived == 1


# --- rollups -----------------------------------------------------------------


def test_run_status_precedence():
    assert run_status(fold(proposed(spec("a")))) == "awaiting_approval"
    assert run_status(fold(approved(spec("a")))) == "idle"

    running = [*approved(spec("a"), spec("b")), started("a", "att_1")]
    assert run_status(fold(running)) == "running"

    failed = [
        *approved(spec("a"), spec("b")),
        started("a", "att_1"),
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="a", reason="boom"),
    ]
    assert run_status(fold(failed)) == "failed"


def test_settled_run_projection():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="a", checkpoint_id="cp_1"
        ),
    ]
    rollup = run_rollup(fold(events), events)

    assert rollup.status == "settled"
    assert rollup.progress == 1.0
    assert rollup.counts["settled"] == 1
    assert rollup.attention == []


def test_progress_counts_cancelled_work_against_the_brief():
    events = [
        *approved(spec("a"), spec("b")),
        started("a", "att_1"),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
        InitiativeSettled(
            plan_id="plan_1", at=AT, initiative_id="a", checkpoint_id="cp_1"
        ),
    ]
    rollup = run_rollup(fold(events), events)
    assert rollup.progress == 0.5
    assert rollup.total == 2


def test_fleet_sums_across_plans_and_orders_newest_first():
    one = approved(spec("a"))
    two = [
        PlanCreated(plan_id="plan_2", at=AT + timedelta(hours=1), brief="later"),
        PlanProposed(
            plan_id="plan_2",
            at=AT + timedelta(hours=1),
            version=1,
            initiatives=[spec("b"), spec("c")],
        ),
        PlanApproved(plan_id="plan_2", at=AT + timedelta(hours=1), version=1),
    ]
    view = fleet([run_rollup(fold(one), one), run_rollup(fold(two), two)])

    assert [run.plan_id for run in view.runs] == ["plan_2", "plan_1"]
    assert view.counts["pending"] == 3
    assert view.total_runs == 2


# --- attention ---------------------------------------------------------------


def test_plan_gate_is_attention_only_once_a_version_exists():
    bare: list[Event] = [PlanCreated(plan_id="plan_1", at=AT, brief="ship it")]
    assert attention(fold(bare), bare) == []

    events = proposed(spec("a"))
    items = attention(fold(events), events)
    assert [item.kind for item in items] == ["plan_gate"]
    assert items[0].action.path == "/plans/plan_1/approve"
    assert items[0].link.path == "/run?plan=plan_1"
    assert items[0].blocking is True


def test_approved_plan_has_no_gate():
    events = approved(spec("a"))
    assert attention(fold(events), events) == []


def test_checkpoint_awaiting_review_is_attention_and_clears_on_approval():
    events = [
        *approved(spec("a", approval="required")),
        started("a", "att_1"),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT + timedelta(minutes=5),
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
    ]
    items = attention(fold(events), events)
    assert [item.kind for item in items] == ["checkpoint_review"]
    item = items[0]
    assert item.checkpoint_id == "cp_1"
    assert item.since == AT + timedelta(minutes=5)
    assert item.action.path == "/plans/plan_1/checkpoints/cp_1/approve"
    assert item.link.path == "/run?plan=plan_1&initiative=a&checkpoint=cp_1"

    events.append(
        CheckpointApproved(plan_id="plan_1", at=AT, checkpoint_id="cp_1")
    )
    assert attention(fold(events), events) == []


def test_failed_initiative_is_attention_with_a_retry_target():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        InitiativeFailed(
            plan_id="plan_1",
            at=AT + timedelta(minutes=2),
            initiative_id="a",
            reason="checks failed",
        ),
    ]
    items = attention(fold(events), events)
    assert [item.kind for item in items] == ["failed"]
    assert items[0].action.path == "/plans/plan_1/initiatives/a/retry"
    assert "checks failed" in items[0].summary
    assert items[0].blocking is True


def test_blocked_on_user_reads_the_live_runtime_status():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        RuntimeObserved(
            plan_id="plan_1",
            at=AT + timedelta(minutes=1),
            attempt_id="att_1",
            kind="pane_status",
            detail={"status": "blocked"},
        ),
    ]
    items = attention(fold(events), events, now=AT + timedelta(minutes=2))
    assert [item.kind for item in items] == ["blocked_on_user"]
    assert items[0].action.path == "/plans/plan_1/attempts/att_1/answer"
    assert items[0].attempt_id == "att_1"
    assert items[0].blocking is True


def test_routine_progress_is_not_attention():
    """A working agent, observed constantly, needs nobody."""
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        *[
            RuntimeObserved(
                plan_id="plan_1",
                at=AT + timedelta(seconds=n),
                attempt_id="att_1",
                kind="pane_output",
                detail={"status": "working"},
            )
            for n in range(1, 10)
        ],
    ]
    now = AT + timedelta(seconds=10)
    assert attention(fold(events), events, now=now) == []
    assert notifications(attention(fold(events), events, now=now)) == []


def test_stall_needs_silence_and_never_notifies():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        RuntimeObserved(
            plan_id="plan_1",
            at=AT + timedelta(minutes=1),
            attempt_id="att_1",
            kind="pane_output",
            detail={"status": "working"},
        ),
    ]
    quiet = AT + timedelta(minutes=1, seconds=DEFAULT_STALL_SECONDS - 1)
    assert attention(fold(events), events, now=quiet) == []

    late = AT + timedelta(minutes=1, seconds=DEFAULT_STALL_SECONDS)
    items = attention(fold(events), events, now=late)
    assert [item.kind for item in items] == ["stalled"]
    assert items[0].blocking is False
    assert notifications(items) == []
    assert items[0].action.path == "/plans/plan_1/initiatives/a/nudge"


def test_stall_measures_from_the_attempt_when_nothing_was_observed():
    events = [*approved(spec("a")), started("a", "att_1")]
    late = AT + timedelta(seconds=DEFAULT_STALL_SECONDS + 1)
    assert [item.kind for item in attention(fold(events), events, now=late)] == [
        "stalled"
    ]


def test_blocked_wins_over_stalled_for_one_attempt():
    """One attempt yields one item, so a blocker is never double-counted."""
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        RuntimeObserved(
            plan_id="plan_1",
            at=AT + timedelta(minutes=1),
            attempt_id="att_1",
            kind="pane_status",
            detail={"status": "waiting"},
        ),
    ]
    late = AT + timedelta(days=1)
    items = attention(fold(events), events, now=late)
    assert [item.kind for item in items] == ["blocked_on_user"]


def test_every_item_carries_exactly_one_action_and_one_link():
    events = [
        *approved(spec("a"), spec("b", approval="required"), spec("c")),
        started("a", "att_1"),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=1), initiative_id="a",
            reason="boom",
        ),
        started("b", "att_2", at=AT + timedelta(minutes=2)),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT + timedelta(minutes=3),
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_2", exit_code=0),
        ),
        started("c", "att_3", at=AT + timedelta(minutes=4)),
        RuntimeObserved(
            plan_id="plan_1",
            at=AT + timedelta(minutes=5),
            attempt_id="att_3",
            kind="pane_status",
            detail={"status": "blocked"},
        ),
    ]
    items = attention(fold(events), events, now=AT + timedelta(minutes=6))

    assert {item.kind for item in items} == {
        "failed",
        "checkpoint_review",
        "blocked_on_user",
    }
    for item in items:
        assert item.action.path.startswith("/plans/plan_1/")
        assert item.link.path.startswith("/run?plan=plan_1")
    assert len({item.key for item in items}) == len(items)


def test_attention_order_is_deterministic_and_oldest_first():
    events = [
        *approved(spec("a"), spec("b")),
        started("a", "att_1"),
        started("b", "att_2"),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=9), initiative_id="b",
            reason="late",
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=1), initiative_id="a",
            reason="early",
        ),
    ]
    plan = fold(events)
    first = attention(plan, events, now=AT + timedelta(minutes=10))
    second = attention(fold(events), list(reversed(events[-2:])) + events[:-2],
                       now=AT + timedelta(minutes=10))

    assert [item.initiative_id for item in first] == ["a", "b"]
    assert [item.key for item in first] == [item.key for item in second]


def test_keys_are_stable_across_polls_and_new_per_failure():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="a", reason="one"),
    ]
    first = attention(fold(events), events)[0].key
    assert attention(fold(events), events)[0].key == first

    events += [
        started("a", "att_2", at=AT + timedelta(minutes=1), origin="retry"),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=2), initiative_id="a",
            reason="two",
        ),
    ]
    assert attention(fold(events), events)[0].key != first


def test_notifications_are_the_blocking_subset_in_the_same_order():
    events = [
        *approved(spec("a"), spec("b")),
        started("a", "att_1"),
        InitiativeFailed(
            plan_id="plan_1", at=AT + timedelta(minutes=1), initiative_id="a",
            reason="boom",
        ),
        started("b", "att_2", at=AT + timedelta(minutes=2)),
    ]
    items = attention(fold(events), events, now=AT + timedelta(days=1))
    assert [item.kind for item in items] == ["failed", "stalled"]
    assert [item.kind for item in notifications(items)] == ["failed"]


def test_fleet_merges_and_orders_attention_across_runs():
    one = proposed(spec("a"))
    two = [
        PlanCreated(plan_id="plan_2", at=AT - timedelta(hours=1), brief="older"),
        PlanProposed(
            plan_id="plan_2",
            at=AT - timedelta(hours=1),
            version=1,
            initiatives=[spec("b")],
        ),
    ]
    view = fleet([run_rollup(fold(one), one), run_rollup(fold(two), two)])

    assert [item.plan_id for item in view.attention] == ["plan_2", "plan_1"]
    assert view.attention == view.notifications


def test_archived_run_attention_stays_out_of_the_fleet():
    live = proposed(spec("a"))
    filed = [
        *proposed(spec("b"), plan_id="plan_2"),
        PlanArchived(plan_id="plan_2", at=AT),
    ]
    view = fleet([run_rollup(fold(live), live), run_rollup(fold(filed), filed)])

    assert [item.plan_id for item in view.attention] == ["plan_1"]


# --- digest ------------------------------------------------------------------


def test_digest_drops_routine_traffic():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        RuntimeObserved(
            plan_id="plan_1", at=AT, attempt_id="att_1", kind="pane_output"
        ),
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="a", reason="boom"),
    ]
    kinds = [entry.type for entry in digest(events)]

    assert "runtime_observed" not in kinds
    assert kinds == [
        "plan_created",
        "plan_proposed",
        "plan_approved",
        "attempt_started",
        "initiative_failed",
    ]


def test_digest_is_since_bounded_and_chronological():
    events = [
        *approved(spec("a"), spec("b")),
        started("a", "att_1", at=AT + timedelta(minutes=1)),
        started("b", "att_2", at=AT + timedelta(minutes=2)),
    ]
    entries = digest(events, since=AT + timedelta(minutes=1))

    assert [entry.attempt_id for entry in entries] == ["att_2"]
    assert entries[0].link.path == "/run?plan=plan_1&initiative=b"


def test_digest_merges_plans_and_keeps_the_newest_under_a_limit():
    one = approved(spec("a"))
    two = [
        PlanCreated(plan_id="plan_2", at=AT + timedelta(minutes=1), brief="two"),
        PlanProposed(
            plan_id="plan_2",
            at=AT + timedelta(minutes=1),
            version=1,
            initiatives=[spec("b")],
        ),
    ]
    entries = digest([*one, *two], limit=2)

    assert [entry.type for entry in entries] == ["plan_created", "plan_proposed"]
    assert [entry.plan_id for entry in entries] == ["plan_2", "plan_2"]

    with pytest.raises(ValueError, match="limit must be positive"):
        _ = digest([*one, *two], limit=0)


def test_digest_summaries_are_deterministic():
    events = [
        *approved(spec("a")),
        started("a", "att_1"),
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="a", reason="boom"),
    ]
    assert digest(events) == digest(list(events))
    assert digest(events)[-1].summary == "initiative failed a: boom"

    recorded = [
        *approved(spec("a")),
        started("a", "att_1"),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
    ]
    assert digest(recorded)[-1].summary == "checkpoint recorded cp_1"


# --- spend -------------------------------------------------------------------


def _planned_with_usage(
    *, cap: int | None = None, tokens: int = 1200, plan_id: str = "plan_1"
) -> list[Event]:
    """A proposed plan whose planning call was really measured."""
    return [
        PlanCreated(plan_id=plan_id, at=AT, brief="ship it"),
        PlanProposed(
            plan_id=plan_id,
            at=AT,
            version=1,
            initiatives=[spec("a")],
            token_cap=cap,
            usage=Usage(
                input_tokens=tokens,
                output_tokens=0,
                source="harness",
                phase="actual",
                category="planning",
                provenance="harness actual",
            ),
        ),
        PlanApproved(plan_id=plan_id, at=AT, version=1),
    ]


def test_spend_reports_the_admission_burn_and_its_provenance():
    events = _planned_with_usage(tokens=1200)
    spend = run_rollup(fold(events), events).spend

    assert spend.accounted == 1200
    assert spend.sources == ["actual"]
    # No cap was declared, and that is not a cap of zero.
    assert spend.cap is None
    assert spend.remaining is None


def test_unmeasured_spend_names_no_source_so_zero_is_never_read_as_measured():
    events = approved(spec("a"))
    spend = run_rollup(fold(events), events).spend

    assert spend.accounted == 0
    assert spend.sources == []


def test_available_spend_is_the_declared_cap_less_the_burn():
    events = _planned_with_usage(cap=5000, tokens=1200)
    spend = run_rollup(fold(events), events).spend

    assert spend.cap == 5000
    assert spend.remaining == 3800


def test_fleet_spend_keeps_capped_and_uncapped_runs_apart():
    capped = _planned_with_usage(cap=5000, tokens=1200, plan_id="plan_1")
    uncapped = [
        *_planned_with_usage(tokens=800, plan_id="plan_2"),
    ]
    view = fleet(
        [run_rollup(fold(capped), capped), run_rollup(fold(uncapped), uncapped)]
    )

    # Every run's burn counts; only the capped run's ceiling does.
    assert view.spend.accounted == 2000
    assert view.spend.capped_runs == 1
    assert view.spend.cap == 5000
    assert view.spend.remaining == 3800
    assert view.spend.sources == ["actual"]


def test_a_fleet_with_no_declared_cap_reports_no_ceiling_rather_than_zero():
    events = _planned_with_usage(tokens=900)
    view = fleet([run_rollup(fold(events), events)])

    assert view.spend.accounted == 900
    assert view.spend.capped_runs == 0
    assert view.spend.cap is None
    assert view.spend.remaining is None


def test_archived_runs_are_out_of_fleet_spend_with_everything_else():
    active = _planned_with_usage(tokens=900, plan_id="plan_1")
    gone = [
        *_planned_with_usage(tokens=4000, plan_id="plan_2"),
        PlanArchived(plan_id="plan_2", at=AT),
    ]
    view = fleet([run_rollup(fold(active), active), run_rollup(fold(gone), gone)])

    assert view.spend.accounted == 900
    assert view.archived == 1
