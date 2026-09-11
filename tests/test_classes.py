from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError
from pydantic import TypeAdapter

from herdsman.classes import (
    ArtifactRef,
    AttemptProvisioned,
    AttemptStarted,
    Assignment,
    Checkpoint,
    CheckpointApproved,
    CheckpointChangesRequested,
    CheckpointRecorded,
    CheckpointRejected,
    CheckResult,
    Contract,
    ContractError,
    Event,
    InitiativeCancelled,
    InitiativeFailed,
    InitiativeFailure,
    InitiativePaused,
    InitiativeResumed,
    InitiativeSettled,
    InitiativeSpec,
    MAX_ATTEMPTS,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
    SubtaskAdvanced,
    OperatorAnswered,
    MemoryLeaf,
    ProcessRestarted,
    TaskNudged,
    TaskRedirected,
    TaskReassigned,
    Usage,
    action_fingerprint,
    frozen_work,
    normalize_error,
)
from herdsman.store import EventStore

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
    with pytest.raises(ValueError, match="only a running, failed, or paused"):
        _ = Plan.fold(events + [
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            )
        ])

    with pytest.raises(ValueError, match="only a running, failed, or paused"):
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


def test_reproposal_preserves_survivors_and_refuses_omitted_completed_work():
    settled = cast(PlanProposed, stream()[1]).initiatives[0]
    revised_tests = InitiativeSpec(
        id="init_c",
        name="tests",
        brief="cover the retry path",
        assignment=LUNA,
        routes=Routes(writes=["tests/**"]),
        depends_on=["init_a"],
    )
    # Omitting settled work is refused: completed work is not the planner's to
    # drop, and its completed claims have to keep their recorded node.
    orphaned = revised_tests.model_copy(update={"depends_on": []})
    with pytest.raises(ValueError, match="keeps its id"):
        _ = Plan.fold(stream() + [
            PlanProposed(
                plan_id="plan_1", at=AT, version=2, initiatives=[orphaned]
            )
        ])

    # Re-declaring it unchanged and revising the unfinished node is accepted;
    # the survivor keeps its runtime state and only planner content changes.
    plan = Plan.fold(stream() + [
        PlanProposed(
            plan_id="plan_1", at=AT, version=2,
            initiatives=[settled, revised_tests], reason="recalibrate",
        )
    ])

    assert set(plan.initiatives) == {"init_a", "init_c"}
    assert plan.initiatives["init_c"].spec.brief == "cover the retry path"
    assert plan.initiatives["init_c"].state == "pending"
    assert plan.approval == "pending"
    settled_node = plan.initiatives["init_a"]
    assert settled_node.state == "settled"
    assert [attempt.id for attempt in settled_node.attempts] == ["att_1"]
    assert settled_node.attempts[0].checkpoint is not None
    assert [sub.id for sub in settled_node.completed_claims] == ["init_a.1"]


def test_fold_rejects_events_from_another_plan():
    events = stream() + [
        RuntimeObserved(
            plan_id="plan_2", at=AT, attempt_id="att_1", kind="heartbeat"
        )
    ]

    with pytest.raises(ValueError):
        _ = Plan.fold(events)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_usage_rejects_negative_token_counts(field: str) -> None:
    payload = {"input_tokens": 0, "output_tokens": 0, "source": "harness"}
    payload[field] = -1

    with pytest.raises(ValidationError):
        _ = Usage.model_validate(payload)


def test_checkpoint_patch_path_is_scoped_or_legacy_none() -> None:
    assert Checkpoint(id="cp_1", attempt_id="att_1").patch_path is None
    for path in (
        ".herdsman/artifacts/cp.patch",
        "./.herdsman/artifacts/nested/cp.patch",
    ):
        assert Checkpoint(id="cp_1", attempt_id="att_1", patch_path=path).patch_path == path


@pytest.mark.parametrize(
    "path",
    [
        "../outside.patch",
        "/tmp/outside.patch",
        "C:/outside.patch",
        ".herdsman/artifacts/../../outside.patch",
        ".herdsman/other.patch",
        "other/.herdsman/artifacts/cp.patch",
        ".herdsman/artifacts\\..\\outside.patch",
    ],
)
def test_checkpoint_rejects_unsafe_patch_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        _ = Checkpoint(id="cp_1", attempt_id="att_1", patch_path=path)


def test_artifact_ref_keeps_legacy_none_and_rejects_unsafe_paths() -> None:
    assert ArtifactRef(initiative_id="a", checkpoint_id="cp_1").patch_path is None
    with pytest.raises(ValidationError):
        _ = ArtifactRef(
            initiative_id="a", checkpoint_id="cp_1", patch_path="../outside.patch"
        )


def test_stream_events_round_trip_through_the_discriminated_union():
    adapter = TypeAdapter(list[Event])
    events = stream() + [
        CheckpointApproved(plan_id="plan_1", at=AT, checkpoint_id="cp_1", by="rev"),
        CheckpointRejected(plan_id="plan_1", at=AT, checkpoint_id="cp_1", reason="no"),
        CheckpointChangesRequested(plan_id="plan_1", at=AT, checkpoint_id="cp_1"),
    ]
    revived = adapter.validate_python(adapter.dump_python(events))
    assert revived == events


def gated_stream() -> list[Event]:
    """A producer whose contract requires approval, feeding one consumer."""
    api = InitiativeSpec(
        id="init_a",
        name="api",
        brief="add a health endpoint",
        assignment=LUNA,
        routes=Routes(writes=["src/api/**"]),
        approval="required",
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
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id="cp_1",
                attempt_id="att_1",
                changed_paths=["src/api/health.py"],
                exit_code=0,
            ),
        ),
    ]


def test_required_approval_blocks_settlement_and_downstream_readiness():
    """Sprint 3 exit: a gated consumer stays blocked until approval lands."""
    events = gated_stream()
    plan = Plan.fold(events)

    assert plan.initiatives["init_a"].state == "running"
    assert plan.ready() == []
    with pytest.raises(ValueError, match="requires approval"):
        _ = Plan.fold(
            events
            + [
                InitiativeSettled(
                    plan_id="plan_1",
                    at=AT,
                    initiative_id="init_a",
                    checkpoint_id="cp_1",
                )
            ]
        )

    released = Plan.fold(
        events
        + [
            CheckpointApproved(plan_id="plan_1", at=AT, checkpoint_id="cp_1", by="rev"),
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            ),
        ]
    )
    assert released.initiatives["init_a"].state == "settled"
    assert released.ready() == ["init_c"]


def test_automatic_policy_settles_clean_evidence_and_records_policy_approval():
    """The default contract keeps the Sprint 2 behavior, with an audit trail."""
    plan = Plan.fold(stream())

    decision = plan.initiatives["init_a"].checkpoint_decisions["cp_1"]
    assert decision.state == "approved"
    assert decision.decided_by == "policy"
    assert decision.decided_at == AT
    assert plan.ready() == ["init_c"]


def test_rejection_is_auditable_and_a_revision_can_be_recorded():
    events = gated_stream()
    rejected_at = AT + timedelta(seconds=30)
    revised_at = AT + timedelta(seconds=60)
    rejection = CheckpointRejected(
        plan_id="plan_1", at=rejected_at, checkpoint_id="cp_1", reason="wrong scope"
    )
    revision = CheckpointRecorded(
        plan_id="plan_1",
        at=revised_at,
        checkpoint=Checkpoint(
            id="cp_2",
            attempt_id="att_1",
            changed_paths=["src/api/health.py", "src/api/routes.py"],
            exit_code=0,
        ),
    )

    plan = Plan.fold(events + [rejection])
    initiative = plan.initiatives["init_a"]
    assert initiative.state == "failed"
    assert initiative.checkpoint_decisions["cp_1"].state == "rejected"
    assert initiative.checkpoint_decisions["cp_1"].reason == "wrong scope"
    assert initiative.checkpoint_versions[0].id == "cp_1"

    revised = Plan.fold(events + [rejection, revision])
    initiative = revised.initiatives["init_a"]
    assert [version.id for version in initiative.checkpoint_versions] == ["cp_1", "cp_2"]
    assert initiative.latest_checkpoint is not None
    assert initiative.latest_checkpoint.id == "cp_2"
    # The rejected version stays addressable and the attempt serves the latest.
    assert initiative.checkpoint_decisions["cp_1"].state == "rejected"
    assert initiative.attempts[0].checkpoint is not None
    assert initiative.attempts[0].checkpoint.id == "cp_2"
    assert initiative.attempts[0].ended_at == AT  # the first record ends the attempt

    settled = Plan.fold(
        events
        + [
            rejection,
            revision,
            CheckpointApproved(plan_id="plan_1", at=revised_at, checkpoint_id="cp_2"),
            InitiativeSettled(
                plan_id="plan_1", at=revised_at, initiative_id="init_a", checkpoint_id="cp_2"
            ),
        ]
    )
    assert settled.initiatives["init_a"].state == "settled"
    assert settled.ready() == ["init_c"]


def test_review_verdicts_reject_invalid_transitions():
    events = gated_stream()
    later = AT + timedelta(seconds=30)

    with pytest.raises(ValueError, match="already approved"):
        _ = Plan.fold(
            events
            + [
                CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
                CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
            ]
        )
    with pytest.raises(ValueError, match="already rejected"):
        _ = Plan.fold(
            events
            + [
                CheckpointRejected(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
                CheckpointRejected(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
            ]
        )
    with pytest.raises(ValueError, match="record a revised checkpoint"):
        _ = Plan.fold(
            events
            + [
                CheckpointRejected(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
                CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
            ]
        )
    with pytest.raises(ValueError, match="only while review is pending"):
        _ = Plan.fold(
            events
            + [
                CheckpointChangesRequested(
                    plan_id="plan_1", at=later, checkpoint_id="cp_1"
                ),
                CheckpointChangesRequested(
                    plan_id="plan_1", at=later, checkpoint_id="cp_1"
                ),
            ]
        )
    with pytest.raises(ValueError, match="unknown checkpoint"):
        _ = Plan.fold(
            events + [CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_x")]
        )

    # Approving is still possible after a change request: the reviewer may
    # accept the evidence after all.
    accepted = Plan.fold(
        events
        + [
            CheckpointChangesRequested(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
            CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
        ]
    )
    assert accepted.initiatives["init_a"].checkpoint_decisions["cp_1"].state == "approved"


def test_settlement_refuses_rejected_evidence_under_any_policy():
    """Rejection is a human verdict; the operator override settles dirty
    evidence, never rejected evidence — the revision path is the recovery."""
    events = stream()[:-1]  # automatic policy, checkpoint recorded, not settled
    later = AT + timedelta(seconds=30)

    with pytest.raises(ValueError, match="was rejected"):
        _ = Plan.fold(
            events
            + [
                CheckpointRejected(plan_id="plan_1", at=later, checkpoint_id="cp_1"),
                InitiativeSettled(
                    plan_id="plan_1", at=later, initiative_id="init_a", checkpoint_id="cp_1"
                ),
            ]
        )


def test_revision_requires_rejection_or_requested_changes():
    events = gated_stream()
    later = AT + timedelta(seconds=30)
    duplicate = CheckpointRecorded(
        plan_id="plan_1",
        at=later,
        checkpoint=Checkpoint(id="cp_2", attempt_id="att_1", exit_code=0),
    )

    with pytest.raises(ValueError, match="revision"):
        _ = Plan.fold(events + [duplicate])  # pending review

    approved = events + [
        CheckpointApproved(plan_id="plan_1", at=later, checkpoint_id="cp_1")
    ]
    with pytest.raises(ValueError, match="revision"):
        _ = Plan.fold(approved + [duplicate])

    requested = events + [
        CheckpointChangesRequested(plan_id="plan_1", at=later, checkpoint_id="cp_1")
    ]
    revised = Plan.fold(requested + [duplicate])
    assert [v.id for v in revised.initiatives["init_a"].checkpoint_versions] == [
        "cp_1",
        "cp_2",
    ]


def test_duplicate_checkpoint_ids_are_refused_even_after_supersession():
    """Superseded versions keep their ids reserved: history stays addressable."""
    events = gated_stream() + [
        CheckpointRejected(
            plan_id="plan_1", at=AT + timedelta(seconds=30), checkpoint_id="cp_1"
        )
    ]
    revision = CheckpointRecorded(
        plan_id="plan_1",
        at=AT + timedelta(seconds=60),
        checkpoint=Checkpoint(id="cp_2", attempt_id="att_1", exit_code=0),
    )
    recycled = CheckpointRecorded(
        plan_id="plan_1",
        at=AT + timedelta(seconds=90),
        checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
    )

    with pytest.raises(ValueError, match="duplicate checkpoint cp_1"):
        _ = Plan.fold(events + [revision, recycled])


def test_later_rejection_taints_consumers_that_already_ran():
    """Sprint 3 exit: supersession/rejection surfaces deterministic attention.

    A consumer that already ran on the rejected evidence stays tainted even
    after the producer recovers with an approved revision; a consumer whose
    last attempt started after the recovery is clean.
    """
    late_consumer = InitiativeSpec(
        id="init_e",
        name="late",
        brief="consumes after the recovery",
        assignment=LUNA,
        routes=Routes(writes=["src/e/**"]),
        depends_on=["init_a"],
    )
    t1 = AT + timedelta(seconds=5)   # init_a settles (automatic approval)
    t2 = AT + timedelta(seconds=10)  # init_c's attempt starts on approved cp_1
    t3 = AT + timedelta(seconds=20)  # cp_1 rejected after the consumer ran
    t4 = AT + timedelta(seconds=30)  # revised cp_2 approved (recovery)
    t5 = AT + timedelta(seconds=40)  # init_e runs on the recovered evidence

    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="chain"),
        PlanProposed(
            plan_id="plan_1",
            at=AT,
            version=1,
            initiatives=[
                cast(PlanProposed, stream()[1]).initiatives[0],
                cast(PlanProposed, stream()[1]).initiatives[1],
                late_consumer,
            ],
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_a", initiative_id="init_a",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_a", exit_code=0),
        ),
        InitiativeSettled(plan_id="plan_1", at=t1, initiative_id="init_a", checkpoint_id="cp_1"),
        AttemptStarted(
            plan_id="plan_1", at=t2, attempt_id="att_c", initiative_id="init_c",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=t2,
            checkpoint=Checkpoint(id="cp_c", attempt_id="att_c", exit_code=0),
        ),
        CheckpointRejected(plan_id="plan_1", at=t3, checkpoint_id="cp_1", reason="wrong scope"),
        CheckpointRecorded(
            plan_id="plan_1", at=t3,
            checkpoint=Checkpoint(id="cp_2", attempt_id="att_a", exit_code=0),
        ),
        CheckpointApproved(plan_id="plan_1", at=t4, checkpoint_id="cp_2"),
    ]

    released = Plan.fold(events[:8])
    assert released.attention() == []

    tainted = Plan.fold(events[:9])
    items = tainted.attention()
    assert len(items) == 1
    assert items[0].initiative_id == "init_c"
    assert items[0].producer_id == "init_a"
    assert items[0].checkpoint_id == "cp_1"
    assert "rejected" in items[0].reason

    # The consumer built on cp_1, so approving the revision does not clean it;
    # it must re-run on the recovered evidence.
    still = Plan.fold(events)
    assert still.attention() == items

    # A consumer whose last attempt started after the recovery is clean.
    after = Plan.fold(
        events
        + [
            AttemptStarted(
                plan_id="plan_1", at=t5, attempt_id="att_e", initiative_id="init_e",
                assignment=LUNA,
            ),
            CheckpointRecorded(
                plan_id="plan_1", at=t5,
                checkpoint=Checkpoint(id="cp_e", attempt_id="att_e", exit_code=0),
            ),
        ]
    )
    assert [item.initiative_id for item in after.attention()] == ["init_c"]


def test_attention_names_only_consumers_that_ran():
    """A pending consumer is blocked by readiness, not tainted: nothing ran."""
    later = AT + timedelta(seconds=30)
    rejected = Plan.fold(
        stream()
        + [CheckpointRejected(plan_id="plan_1", at=later, checkpoint_id="cp_1")]
    )
    assert rejected.attention() == []
    assert rejected.ready() == []  # blocked until a revised checkpoint is approved


def test_taint_propagates_through_a_tainted_dependency():
    """A consumer of a tainted consumer inherits the attention item."""
    mid = InitiativeSpec(
        id="init_b",
        name="mid",
        brief="build on init_a",
        assignment=LUNA,
        routes=Routes(writes=["src/b/**"]),
        depends_on=["init_a"],
    )
    leaf = InitiativeSpec(
        id="init_d",
        name="leaf",
        brief="build on init_b",
        assignment=LUNA,
        routes=Routes(writes=["src/d/**"]),
        depends_on=["init_b"],
    )
    t1 = AT + timedelta(seconds=5)   # init_a settles
    t2 = AT + timedelta(seconds=10)  # init_b's attempt
    t3 = AT + timedelta(seconds=15)  # init_b settles
    t4 = AT + timedelta(seconds=20)  # init_d's attempt
    t5 = AT + timedelta(seconds=25)  # cp_1 rejected

    api = cast(PlanProposed, stream()[1]).initiatives[0]
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="chain"),
        PlanProposed(
            plan_id="plan_1", at=AT, version=1, initiatives=[api, mid, leaf],
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_a", initiative_id="init_a",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_a", exit_code=0),
        ),
        InitiativeSettled(plan_id="plan_1", at=t1, initiative_id="init_a", checkpoint_id="cp_1"),
        AttemptStarted(
            plan_id="plan_1", at=t2, attempt_id="att_b", initiative_id="init_b",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=t2,
            checkpoint=Checkpoint(id="cp_b", attempt_id="att_b", exit_code=0),
        ),
        InitiativeSettled(plan_id="plan_1", at=t3, initiative_id="init_b", checkpoint_id="cp_b"),
        AttemptStarted(
            plan_id="plan_1", at=t4, attempt_id="att_d", initiative_id="init_d",
            assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=t4,
            checkpoint=Checkpoint(id="cp_d", attempt_id="att_d", exit_code=0),
        ),
        CheckpointRejected(plan_id="plan_1", at=t5, checkpoint_id="cp_1"),
    ]

    items = Plan.fold(events).attention()
    assert [(item.initiative_id, item.producer_id) for item in items] == [
        ("init_b", "init_a"),
        ("init_d", "init_a"),
    ]
    assert items[1].reason.startswith("depends on tainted init_b")


def contracted_stream(checks: list[CheckResult]) -> list[Event]:
    """An explicit contract whose required check the checkpoint may not satisfy."""
    api = InitiativeSpec(
        id="init_a",
        name="api",
        brief="add a health endpoint",
        assignment=LUNA,
        routes=Routes(writes=["src/api/**"]),
        contract=Contract(id="c", required_checks=["uv run pytest -q"]),
    )
    return [
        PlanCreated(plan_id="plan_1", at=AT, brief="contracted work"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[api]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1",
            initiative_id="init_a", assignment=LUNA,
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id="cp_1",
                attempt_id="att_1",
                changed_paths=["src/api/health.py"],
                exit_code=0,
                checks=checks,
                usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
            ),
        ),
    ]


def test_the_fold_refuses_settlement_of_a_contract_violating_checkpoint() -> None:
    """F1 regression: the fold is the contract gate, before InitiativeSettled
    can apply — a direct append or replay cannot bypass required checks."""
    settle = InitiativeSettled(
        plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
    )
    with pytest.raises(ContractError, match="missing-check") as excinfo:
        _ = Plan.fold(
            contracted_stream([CheckResult(name="true", passed=True)]) + [settle]
        )
    assert [violation.code for violation in excinfo.value.violations] == [
        "missing-check"
    ]

    # A checkpoint satisfying the contract settles unchanged.
    settled = Plan.fold(
        contracted_stream([CheckResult(name="uv run pytest -q", passed=True)])
        + [settle]
    )
    assert settled.initiatives["init_a"].state == "settled"
# --- task interventions: versioned brief/assignment, leaves, guards ----------

def redirected_stream() -> list[Event]:
    """init_a running on att_1, then redirected to brief version 2, then failed."""
    return stream()[:-1] + [
        TaskRedirected(
            plan_id="plan_1",
            at=AT,
            initiative_id="init_a",
            brief="add a health endpoint that also reports version",
            reason="operator wants version reporting",
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="stalled"
        ),
    ]

def test_redirect_versions_the_brief_and_preserves_attempt_history():
    plan = Plan.fold(redirected_stream())
    initiative = plan.initiatives["init_a"]

    assert [v.version for v in initiative.brief_versions] == [2]
    assert initiative.current_brief == "add a health endpoint that also reports version"
    assert initiative.spec.brief == "add a health endpoint"  # planner content intact

    # The first attempt keeps the brief and assignment it actually ran on.
    assert initiative.attempts[0].brief_version == 1
    assert initiative.attempts[0].assignment == LUNA

def test_retry_starts_on_the_current_brief_and_assignment():
    retried = Plan.fold(
        redirected_stream()
        + [
            AttemptStarted(
                plan_id="plan_1",
                at=AT,
                attempt_id="att_2",
                initiative_id="init_a",
                assignment=LUNA,
                brief_version=2,
                origin="retry",
            )
        ]
    )
    initiative = retried.initiatives["init_a"]
    assert initiative.state == "running"
    assert initiative.attempts[1].brief_version == 2

    # A stale start is refused: the packet must be compiled from the current brief.
    with pytest.raises(ValueError, match="current brief version 2"):
        _ = Plan.fold(
            redirected_stream()
            + [
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA, brief_version=1,
                    origin="retry",
                )
            ]
        )

def test_retry_after_failure_is_allowed_but_a_second_live_attempt_is_not():
    retried = Plan.fold(
        stream()[:-1]
        + [
            InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x"),
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2",
                initiative_id="init_a", assignment=LUNA, origin="retry",
            ),
        ]
    )
    assert retried.initiatives["init_a"].state == "running"
    assert len(retried.initiatives["init_a"].attempts) == 2

    with pytest.raises(ValueError, match="running"):
        _ = Plan.fold(
            stream()[:-1]
            + [
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA,
                )
            ]
        )

def test_redirect_is_refused_on_a_settled_task():
    with pytest.raises(ValueError, match="settled"):
        _ = Plan.fold(stream() + [TaskRedirected(
            plan_id="plan_1", at=AT, initiative_id="init_a", brief="rewritten",
        )])
    with pytest.raises(ValueError, match="empty"):
        _ = Plan.fold(stream() + [TaskRedirected(
            plan_id="plan_1", at=AT, initiative_id="init_c", brief="  ",
        )])

def test_reassignment_preserves_attempt_history():
    other = Assignment(harness="luna", model="frontier-1")
    reassigned = Plan.fold(
        stream()[:-1]
        + [
            TaskReassigned(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                assignment=other, reason="cheap model stalls",
            )
        ]
    )
    initiative = reassigned.initiatives["init_a"]
    assert initiative.assignment_override == other
    assert initiative.current_assignment == other
    assert initiative.attempts[0].assignment == LUNA  # history untouched
    assert initiative.attempts[0].by == "daemon"  # an ordinary run
    assert initiative.attempts[0].origin == "run"

    # Reassignment is no longer luna-bound: the fold accepts any harness and
    # refuses only the current assignment. A task on an unconfigured harness
    # still fails loudly, later, at command compilation.
    cross = Plan.fold(
        stream()[:-1]
        + [
            TaskReassigned(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                assignment=Assignment(harness="claude", model="frontier-1"),
            )
        ]
    )
    assert cross.initiatives["init_a"].assignment_override == Assignment(
        harness="claude", model="frontier-1"
    )

    # A new attempt must start on the current assignment, so a stale packet
    # compiled before the reassignment cannot start.
    with pytest.raises(ValueError, match="does not match"):
        _ = Plan.fold(
            stream()[:-1]
            + [
                TaskReassigned(
                    plan_id="plan_1", at=AT, initiative_id="init_a", assignment=other
                ),
                InitiativeFailed(
                    plan_id="plan_1", at=AT, initiative_id="init_a", reason="stalled"
                ),
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA, origin="retry",
                ),
            ]
        )

    with pytest.raises(ValueError, match="already assigned"):
        _ = Plan.fold(
            stream()[:-1]
            + [
                TaskReassigned(
                    plan_id="plan_1", at=AT, initiative_id="init_a", assignment=other
                ),
                TaskReassigned(
                    plan_id="plan_1", at=AT, initiative_id="init_a", assignment=other
                ),
            ]
        )

    with pytest.raises(ValueError, match="settled"):
        _ = Plan.fold(stream() + [TaskReassigned(
            plan_id="plan_1", at=AT, initiative_id="init_a", assignment=other
        )])


def test_a_retry_attempt_is_attributable_and_distinguished_from_a_run():
    """The new attempt names its actor and origin in persisted, replayed state."""
    retried = Plan.fold(
        stream()[:-1]
        + [
            InitiativeFailed(
                plan_id="plan_1", at=AT, initiative_id="init_a", reason="stalled"
            ),
            AttemptStarted(
                plan_id="plan_1",
                at=AT,
                attempt_id="att_2",
                initiative_id="init_a",
                assignment=LUNA,
                by="lead",
                origin="retry",
            ),
        ]
    )
    attempts = retried.initiatives["init_a"].attempts
    assert [(attempt.id, attempt.by, attempt.origin) for attempt in attempts] == [
        ("att_1", "daemon", "run"),
        ("att_2", "lead", "retry"),
    ]


def test_nudge_targets_only_the_live_attempt():
    nudge = TaskNudged(
        plan_id="plan_1", at=AT, initiative_id="init_a",
        attempt_id="att_1", text="focus on the failing check",
    )
    nudged = Plan.fold(stream()[:-1] + [nudge])
    assert nudged.memory_leaves == []  # not ground truth, event-only audit

    with pytest.raises(ValueError, match="settled"):
        _ = Plan.fold(stream() + [nudge])
    with pytest.raises(ValueError, match="live attempt"):
        _ = Plan.fold(stream()[:-1] + [
            TaskNudged(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                attempt_id="att_9", text="focus",
            )
        ])
    with pytest.raises(ValueError, match="empty"):
        _ = Plan.fold(stream()[:-1] + [
            TaskNudged(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                attempt_id="att_1", text="  ",
            )
        ])
    with pytest.raises(ValueError, match="pending"):
        _ = Plan.fold(stream()[:-1] + [
            TaskNudged(
                plan_id="plan_1", at=AT, initiative_id="init_c",
                attempt_id="att_1", text="focus",
            )
        ])

def test_a_delivery_initiated_while_live_folds_after_the_attempt_settles():
    """The window proof: initiation inside the live window folds, after it refuses.

    A pane delivery is recorded after the pane write, so a delivery begun
    against the validated live attempt can race the settlement landing
    during the write. Its event's `at` is the initiation time, so the fold
    admits it exactly when that time falls inside the attempt's live window;
    anything initiated after the settlement is retroactive and refused.
    """
    settled_at = AT + timedelta(seconds=1)
    raced = stream()[:-1] + [
        InitiativeSettled(
            plan_id="plan_1",
            at=settled_at,
            initiative_id="init_a",
            checkpoint_id="cp_1",
        ),
        TaskNudged(
            plan_id="plan_1",
            at=AT,
            initiative_id="init_a",
            attempt_id="att_1",
            text="focus on the failing check",
            ground_truth=True,
        ),
    ]
    folded = Plan.fold(raced)
    assert folded.initiatives["init_a"].state == "settled"
    assert folded.memory_leaves[-1].subject == "init_a.nudge"
    assert folded.memory_leaves[-1].claim == "focus on the failing check"
    # An initiation at or after the settlement is retroactive.
    for late in (settled_at, settled_at + timedelta(seconds=1)):
        with pytest.raises(ValueError, match="settled"):
            _ = Plan.fold(
                stream()[:-1]
                + [
                    InitiativeSettled(
                        plan_id="plan_1",
                        at=settled_at,
                        initiative_id="init_a",
                        checkpoint_id="cp_1",
                    ),
                    TaskNudged(
                        plan_id="plan_1",
                        at=late,
                        initiative_id="init_a",
                        attempt_id="att_1",
                        text="focus",
                    ),
                ]
            )

def test_ground_truth_nudge_and_redirect_and_answer_project_leaves():
    leaves = Plan.fold(
        stream()[:-1]
        + [
            TaskRedirected(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                brief="add a versioned health endpoint", reason="version reporting",
            ),
            TaskNudged(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                attempt_id="att_1", text="docs/ is gitignored; use notes/",
                ground_truth=True,
            ),
            OperatorAnswered(
                plan_id="plan_1", at=AT, attempt_id="att_1",
                subject="docs-dir-missing", answer="read notes/product.md instead",
            ),
        ]
    ).memory_leaves

    assert [(leaf.id, leaf.subject, leaf.origin) for leaf in leaves] == [
        ("leaf_1", "init_a.brief", "redirect"),
        ("leaf_2", "init_a.nudge", "nudge"),
        ("leaf_3", "docs-dir-missing", "operator-answer"),
    ]
    assert leaves[0].claim == "add a versioned health endpoint"
    assert leaves[2].claim == "read notes/product.md instead"


def test_redirect_leaf_claim_is_the_brief_so_packets_carry_the_correction():
    plan = Plan.fold(
        stream()[:-1]
        + [
            TaskRedirected(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                brief="add a versioned health endpoint", reason="version reporting",
            ),
        ]
    )
    leaf = plan.memory_leaves[-1]
    assert leaf.claim == "add a versioned health endpoint"
    assert leaf.claim == plan.initiatives["init_a"].current_brief


def test_a_checkpoint_targeted_redirect_derives_the_brief_deterministically():
    stream_events = stream()  # att_1 already recorded a checkpoint
    recorded = cast(CheckpointRecorded, stream_events[-2])
    checkpoint = recorded.checkpoint
    assert checkpoint is not None
    redirected = Plan.fold(
        stream_events[:-1]
        + [
            TaskRedirected(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                checkpoint_id=checkpoint.id, reason="continue from the latest work",
            ),
        ]
    )
    brief = redirected.initiatives["init_a"].current_brief
    assert brief == redirected.memory_leaves[-1].claim
    assert checkpoint.id in brief
    assert brief.startswith(f"Continue from checkpoint {checkpoint.id}")

    with pytest.raises(ValueError, match="unknown checkpoint"):
        _ = Plan.fold(
            stream_events[:-1]
            + [
                TaskRedirected(
                    plan_id="plan_1", at=AT, initiative_id="init_a",
                    checkpoint_id="cp_nope",
                ),
            ]
        )
    with pytest.raises(ValueError, match="not both"):
        _ = Plan.fold(
            stream_events[:-1]
            + [
                TaskRedirected(
                    plan_id="plan_1", at=AT, initiative_id="init_a",
                    brief="both", checkpoint_id=checkpoint.id,
                ),
            ]
        )
    with pytest.raises(ValueError, match="cannot be empty"):
        _ = Plan.fold(
            stream_events[:-1]
            + [TaskRedirected(plan_id="plan_1", at=AT, initiative_id="init_a")]
        )

def test_memory_leaves_are_deterministic_across_a_restart():
    events = stream()[:-1] + [
        TaskNudged(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            attempt_id="att_1", text="docs/ is gitignored; use notes/",
            ground_truth=True,
        ),
        OperatorAnswered(
            plan_id="plan_1", at=AT, attempt_id="att_1",
            subject="docs-dir-missing", answer="read notes/product.md instead",
        ),
    ]
    assert Plan.fold(events).memory_leaves == Plan.fold(events).memory_leaves

def test_operator_answer_requires_a_live_request():
    with pytest.raises(ValueError, match="settled"):
        _ = Plan.fold(stream() + [OperatorAnswered(
            plan_id="plan_1", at=AT, attempt_id="att_1",
            subject="s", answer="a",
        )])
    with pytest.raises(ValueError, match="unknown attempt"):
        _ = Plan.fold(stream()[:-1] + [OperatorAnswered(
            plan_id="plan_1", at=AT, attempt_id="nope",
            subject="s", answer="a",
        )])
    with pytest.raises(ValueError, match="subject and an answer"):
        _ = Plan.fold(stream()[:-1] + [OperatorAnswered(
            plan_id="plan_1", at=AT, attempt_id="att_1",
            subject="s", answer="  ",
        )])

def test_intervention_events_round_trip_through_the_discriminated_union():
    adapter = TypeAdapter(list[Event])
    events = stream()[:-1] + [
        TaskNudged(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            attempt_id="att_1", text="focus", ground_truth=True,
        ),
        OperatorAnswered(
            plan_id="plan_1", at=AT, attempt_id="att_1", subject="s", answer="a",
        ),
        TaskRedirected(
            plan_id="plan_1", at=AT, initiative_id="init_a", brief="rewritten",
        ),
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x"),
        TaskReassigned(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            assignment=Assignment(harness="luna", model="frontier-1"),
        ),
    ]
    revived = adapter.validate_python(adapter.dump_python(events))
    assert revived == events
    assert Plan.fold(revived) == Plan.fold(events)


def test_process_restarted_is_an_auditable_live_attempt_event():
    event = ProcessRestarted(plan_id="plan_1", at=AT, attempt_id="att_1", by="lead")
    folded = Plan.fold(stream()[:-1] + [event])
    assert folded.initiatives["init_a"].attempts[-1].pane_ref is not None

    with pytest.raises(ValueError, match="unknown attempt"):
        _ = Plan.fold(stream()[:-1] + [
            ProcessRestarted(plan_id="plan_1", at=AT, attempt_id="att_9")
        ])
    with pytest.raises(ValueError, match="live attempt only"):
        _ = Plan.fold(stream() + [event])


# --- Sprint 5: durable recovery substrate ------------------------------------


def failing_round(
    attempt_id: str, name: str, summary: str, *, reason: str = "checks failed"
) -> list[Event]:
    """One retry round on the failed init_a: start, record one failed check,
    fail the initiative. The base stream must leave init_a failed with fewer
    than MAX_ATTEMPTS attempts."""
    return [
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id=attempt_id,
            initiative_id="init_a", assignment=LUNA, origin="retry",
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id=f"cp_{attempt_id}",
                attempt_id=attempt_id,
                exit_code=1,
                checks=[CheckResult(name=name, passed=False, summary=summary)],
            ),
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason=reason
        ),
    ]


def signature_base(summary: str, check: str = "uv run pytest -q") -> list[Event]:
    """init_a running on att_1, recording one failed check, then failing."""
    return stream()[:-2] + [
        CheckpointRecorded(
            plan_id="plan_1",
            at=AT,
            checkpoint=Checkpoint(
                id="cp_1",
                attempt_id="att_1",
                exit_code=1,
                checks=[CheckResult(name=check, passed=False, summary=summary)],
            ),
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="checks failed"
        ),
    ]


def failure_leaves(plan: Plan) -> list[MemoryLeaf]:
    return [leaf for leaf in plan.memory_leaves if leaf.origin == "failure"]


def test_two_identical_failure_signatures_promote_exactly_one_leaf():
    base = signature_base("AssertionError: row 42 missing")
    # Same failure modulo case, whitespace, and digits: normalizes equal.
    second = base + failing_round(
        "att_2", "uv run pytest -q", "assertionerror:   row 7 missing"
    )

    promoted = Plan.fold(second)
    leaves = failure_leaves(promoted)
    assert len(leaves) == 1
    leaf = leaves[0]
    assert leaf.subject == "init_a.failure"
    assert leaf.by == "policy"
    assert leaf.claim == (
        "check 'uv run pytest -q' failed in attempts att_1, att_2: "
        + "assertionerror: row # missing"
    )
    record = promoted.failure_signatures[
        ("init_a", "uv run pytest -q", "assertionerror: row # missing")
    ]
    assert record.count == 2
    assert record.attempts == ["att_1", "att_2"]

    # A third identical failure adds nothing: the leaf appears exactly once.
    third = Plan.fold(second + failing_round(
        "att_3", "uv run pytest -q", "ASSERTIONERROR: row 0 missing"
    ))
    assert failure_leaves(third) == leaves
    assert third.failure_signatures[
        ("init_a", "uv run pytest -q", "assertionerror: row # missing")
    ].count == 3


def test_a_differing_check_or_error_does_not_promote():
    base = signature_base("AssertionError: row 42 missing")
    different_check = Plan.fold(base + failing_round("att_2", "lint", "E501 line too long"))
    different_error = Plan.fold(base + failing_round("att_2", "uv run pytest -q", "timeout"))

    assert failure_leaves(different_check) == []
    assert failure_leaves(different_error) == []


def test_a_failure_without_checks_uses_the_reason_as_the_signature():
    reason = "daemon death: pane p_9f missing"
    base = stream()[:-1] + [
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason=reason)
    ]
    promoted = Plan.fold(
        base
        + [
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2",
                initiative_id="init_a", assignment=LUNA, origin="retry",
            ),
            InitiativeFailed(
                plan_id="plan_1", at=AT, initiative_id="init_a", reason=reason
            ),
        ]
    )

    leaves = failure_leaves(promoted)
    assert len(leaves) == 1
    assert leaves[0].claim == (
        "failure repeated in attempts att_1, att_2: daemon death: pane p_#f missing"
    )
    assert promoted.failure_signatures[
        ("init_a", "error", "daemon death: pane p_#f missing")
    ].count == 2


def test_failure_leaves_are_deterministic_across_a_restart():
    events = signature_base("AssertionError: row 42 missing") + failing_round(
        "att_2", "uv run pytest -q", "assertionerror: row 7 missing"
    )
    assert Plan.fold(events) == Plan.fold(events)
    assert Plan.fold(events).failure_signatures == Plan.fold(events).failure_signatures


def test_normalize_error_is_deterministic_and_bounded():
    assert normalize_error("  AssertionError:  ROW\t42 ") == "assertionerror: row #"
    assert normalize_error("row 42 and 7") == "row # and #"
    assert normalize_error("x" * 500) == "x" * 200
    assert normalize_error("") == ""


def test_pause_holds_the_scheduler_but_a_live_attempt_still_settles():
    running = stream()[:-1]  # att_1 live, cp_1 recorded, not settled
    pause = InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_a")

    paused = Plan.fold(running + [pause])
    assert paused.initiatives["init_a"].state == "paused"

    # The scheduler's stop rule: no new attempt starts while paused.
    with pytest.raises(ValueError, match="only a pending or failed"):
        _ = Plan.fold(
            running
            + [
                pause,
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA, origin="retry",
                ),
            ]
        )

    # The live attempt keeps its window and settles under the existing policy.
    settled = Plan.fold(
        running
        + [
            pause,
            InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1"
            ),
        ]
    )
    assert settled.initiatives["init_a"].state == "settled"
    assert settled.ready() == ["init_c"]
    # A delivery initiated against the still-live attempt stays attributable.
    assert Plan.fold(
        running
        + [
            pause,
            TaskNudged(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                attempt_id="att_1", text="focus",
            ),
        ]
    ).initiatives["init_a"].state == "paused"


def test_resume_computes_its_state_from_attempt_history():
    paused_pending = stream() + [
        InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_c")
    ]
    resumed = Plan.fold(
        paused_pending
        + [InitiativeResumed(plan_id="plan_1", at=AT, initiative_id="init_c")]
    )
    assert resumed.initiatives["init_c"].state == "pending"

    paused_failed = stream()[:-1] + [
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x"),
        InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_a"),
    ]
    resumed_failed = Plan.fold(
        paused_failed
        + [InitiativeResumed(plan_id="plan_1", at=AT, initiative_id="init_a")]
    )
    assert resumed_failed.initiatives["init_a"].state == "failed"

    with pytest.raises(ValueError, match="only a paused initiative"):
        _ = Plan.fold(
            stream()
            + [InitiativeResumed(plan_id="plan_1", at=AT, initiative_id="init_c")]
        )
    with pytest.raises(ValueError, match="only a pending, failed, or running"):
        _ = Plan.fold(
            paused_pending
            + [InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_c")]
        )


def test_cancel_is_terminal_and_closes_the_live_window():
    running = stream()[:-1]
    cancel = InitiativeCancelled(
        plan_id="plan_1", at=AT + timedelta(seconds=1), initiative_id="init_a"
    )
    cancelled = Plan.fold(running + [cancel])
    initiative = cancelled.initiatives["init_a"]
    assert initiative.state == "cancelled"
    assert [attempt.id for attempt in initiative.attempts] == ["att_1"]

    with pytest.raises(ValueError, match="only a pending or failed"):
        _ = Plan.fold(
            running
            + [
                cancel,
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA, origin="retry",
                ),
            ]
        )
    with pytest.raises(ValueError, match="only an active or retryable"):
        _ = Plan.fold(
            running + [cancel, TaskRedirected(
                plan_id="plan_1", at=AT, initiative_id="init_a", brief="rewritten",
            )]
        )
    with pytest.raises(ValueError, match="only an active or retryable"):
        _ = Plan.fold(
            running + [cancel, TaskReassigned(
                plan_id="plan_1", at=AT, initiative_id="init_a",
                assignment=Assignment(harness="luna", model="frontier-1"),
            )]
        )
    with pytest.raises(ValueError, match="only a running, failed, or paused"):
        _ = Plan.fold(
            running + [cancel, InitiativeSettled(
                plan_id="plan_1", at=AT, initiative_id="init_a", checkpoint_id="cp_1",
            )]
        )
    with pytest.raises(ValueError, match="cannot be cancelled"):
        _ = Plan.fold(running + [cancel, cancel])
    with pytest.raises(ValueError, match="only a paused initiative"):
        _ = Plan.fold(
            running + [cancel, InitiativeResumed(
                plan_id="plan_1", at=AT, initiative_id="init_a",
            )]
        )

    # The window closed with the cancel: a delivery initiated after it is a
    # retroactive intervention and stays refused; one initiated before it (a
    # pane write racing the cancel) still folds.
    with pytest.raises(ValueError, match="only a running task"):
        _ = Plan.fold(
            running + [cancel, TaskNudged(
                plan_id="plan_1", at=AT + timedelta(seconds=2),
                initiative_id="init_a", attempt_id="att_1", text="late",
            )]
        )
    raced = Plan.fold(
        running + [cancel, TaskNudged(
            plan_id="plan_1", at=AT,
            initiative_id="init_a", attempt_id="att_1", text="in flight",
        )]
    )
    assert raced.initiatives["init_a"].state == "cancelled"

    # A paused task's window is still open; cancel reaches it and closes it.
    paused = running + [InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_a")]
    cancelled_paused = Plan.fold(
        paused
        + [InitiativeCancelled(
            plan_id="plan_1", at=AT + timedelta(seconds=1), initiative_id="init_a"
        )]
    )
    assert cancelled_paused.initiatives["init_a"].state == "cancelled"
    with pytest.raises(ValueError, match="only a running task"):
        _ = Plan.fold(
            paused
            + [
                InitiativeCancelled(
                    plan_id="plan_1", at=AT + timedelta(seconds=1),
                    initiative_id="init_a",
                ),
                TaskNudged(
                    plan_id="plan_1", at=AT + timedelta(seconds=2),
                    initiative_id="init_a", attempt_id="att_1", text="late",
                ),
            ]
        )


def test_cancel_stops_a_branch_without_releasing_downstream():
    cancelled = Plan.fold(
        stream()[:-1]
        + [InitiativeCancelled(plan_id="plan_1", at=AT, initiative_id="init_a")]
    )
    assert cancelled.ready() == []
    assert cancelled.initiatives["init_c"].state == "pending"


def test_a_failure_projects_its_reason_and_evidence():
    failed = Plan.fold(
        stream()[:-1]
        + [
            InitiativeFailed(
                plan_id="plan_1",
                at=AT,
                initiative_id="init_a",
                reason="daemon death: pane p_1 missing",
                evidence=[".herdsman/artifacts/att_1.diag.patch"],
            )
        ]
    )
    assert failed.initiatives["init_a"].failures == [
        InitiativeFailure(
            reason="daemon death: pane p_1 missing",
            evidence=[".herdsman/artifacts/att_1.diag.patch"],
        )
    ]


def test_an_action_id_records_its_outcome_once():
    failure = InitiativeFailed(
        plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
        action_id="act_1",
    )
    plan = Plan.fold(stream()[:-1] + [failure])
    assert plan.action_ids == {
        "act_1": f"initiative_failed:{action_fingerprint(failure)}"
    }

    with pytest.raises(ValueError, match="already recorded as initiative_failed"):
        _ = Plan.fold(stream()[:-1] + [failure, failure])

    # A different action id on the same event shape is a distinct request.
    replayed = Plan.fold(
        stream()[:-1] + [failure, failure.model_copy(update={"action_id": "act_2"})]
    )
    assert replayed.action_ids == {
        "act_1": f"initiative_failed:{action_fingerprint(failure)}",
        "act_2": f"initiative_failed:{action_fingerprint(failure)}",
    }


def test_the_action_id_index_is_bound_to_the_request_and_rebuilds_on_replay():
    """The fingerprint covers the action and its structural payload, not
    timing or attribution prose, and a replay of the same events rebuilds it
    identically — no side table involved."""
    failure = InitiativeFailed(
        plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
        action_id="act_1",
    )
    replayed_at = failure.model_copy(update={"at": AT, "seq": 99})
    # Timing and prose are not the request: the identity hashes identically.
    assert action_fingerprint(replayed_at) == action_fingerprint(failure)
    assert action_fingerprint(
        failure.model_copy(update={"reason": "y", "by": "someone"})
    ) == action_fingerprint(failure)
    # A different target is a different request.
    assert action_fingerprint(
        failure.model_copy(update={"initiative_id": "init_b"})
    ) != action_fingerprint(failure)
    # Replay of the recorded stream reproduces the index byte for byte.
    folded = Plan.fold(stream()[:-1] + [failure])
    replayed = Plan.fold(
        [ev.model_copy(update={"seq": n}) for n, ev in enumerate(stream()[:-1])]
        + [failure.model_copy(update={"seq": len(stream()) - 1})]
    )
    assert replayed.action_ids == folded.action_ids


def test_recovery_events_round_trip_through_the_discriminated_union():
    adapter = TypeAdapter(list[Event])
    events = stream()[:-1] + [
        InitiativePaused(plan_id="plan_1", at=AT, initiative_id="init_a"),
        InitiativeResumed(plan_id="plan_1", at=AT, initiative_id="init_a"),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
            evidence=[".herdsman/artifacts/att_1.diag.patch"],
            action_id="act_1",
        ),
        InitiativeCancelled(plan_id="plan_1", at=AT, initiative_id="init_a"),
    ]
    revived = adapter.validate_python(adapter.dump_python(events))
    assert revived == events
    assert Plan.fold(revived) == Plan.fold(events)


def test_the_attempt_ceiling_is_fold_enforced():
    assert MAX_ATTEMPTS == 3
    failed = stream()[:-1] + [
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x1")
    ]

    def retry(n: int) -> AttemptStarted:
        return AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id=f"att_{n}",
            initiative_id="init_a", assignment=LUNA, origin="retry",
        )

    at_cap = Plan.fold(
        failed
        + [
            retry(2),
            InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x2"),
            retry(3),
        ]
    )
    assert len(at_cap.initiatives["init_a"].attempts) == 3

    with pytest.raises(ValueError, match="attempt ceiling of 3"):
        _ = Plan.fold(
            failed
            + [
                retry(2),
                InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x2"),
                retry(3),
                InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x3"),
                retry(4),
            ]
        )


def test_a_new_attempt_on_failed_work_must_be_a_retry():
    failed = stream()[:-1] + [
        InitiativeFailed(plan_id="plan_1", at=AT, initiative_id="init_a", reason="x")
    ]
    with pytest.raises(ValueError, match="origin='retry'"):
        _ = Plan.fold(
            failed
            + [
                AttemptStarted(
                    plan_id="plan_1", at=AT, attempt_id="att_2",
                    initiative_id="init_a", assignment=LUNA,
                )
            ]
        )
    retried = Plan.fold(
        failed
        + [
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2",
                initiative_id="init_a", assignment=LUNA, origin="retry",
            )
        ]
    )
    assert retried.initiatives["init_a"].state == "running"


def test_attempt_provisioning_persists_the_diff_base():
    provisioned = Plan.fold(
        stream()[:4]
        + [
            AttemptProvisioned(
                plan_id="plan_1", at=AT, attempt_id="att_1",
                worktree_ref="wt_1", base_sha="abc123",
            )
        ]
    )
    assert provisioned.initiatives["init_a"].attempts[0].base_sha == "abc123"

    # Older streams without a base replay as None.
    legacy = Plan.fold(
        stream()[:4]
        + [
            AttemptProvisioned(
                plan_id="plan_1", at=AT, attempt_id="att_1", worktree_ref="wt_1"
            )
        ]
    )
    assert legacy.initiatives["init_a"].attempts[0].base_sha is None


def test_failure_evidence_paths_must_be_preserved_artifacts():
    failed = Plan.fold(
        stream()[:-1]
        + [
            InitiativeFailed(
                plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
                evidence=[".herdsman/artifacts/att_1.diag.patch"],
            )
        ]
    )
    assert failed.initiatives["init_a"].state == "failed"

    with pytest.raises(ValidationError):
        _ = InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
            evidence=["../outside.patch"],
        )
    with pytest.raises(ValidationError):
        _ = InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_a", reason="x",
            evidence=["other/evidence.patch"],
        )


# --- recalibration -----------------------------------------------------------


def partial_stream() -> list[Event]:
    """One unfinished node: a completed claim plus residual work around it."""
    partial = InitiativeSpec(
        id="init_a",
        name="api",
        brief="add a health endpoint",
        assignment=LUNA,
        routes=Routes(writes=["src/api/**"]),
        subtasks=["read the router", "write the route", "wire it up"],
    )
    return [
        PlanCreated(plan_id="plan_1", at=AT, brief="add a health endpoint"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[partial]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="init_a",
            assignment=LUNA, worktree_ref="wt_1",
        ),
        SubtaskAdvanced(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            subtask_id="init_a.2", state="done",
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
    ]


def unfinished_failure_stream() -> list[Event]:
    """One failed, unfinished node: consumed attempts, no completed claim."""
    unfinished = InitiativeSpec(
        id="init_b",
        name="api",
        brief="ship the flag",
        assignment=LUNA,
        routes=Routes(writes=["src/b/**"]),
        subtasks=["write it", "test it"],
    )
    return [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship the flag"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[unfinished]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="init_b",
            assignment=LUNA, worktree_ref="wt_1",
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=AT,
            checkpoint=Checkpoint(
                id="cp_1", attempt_id="att_1", exit_code=0,
                usage=Usage(input_tokens=40, output_tokens=10, source="harness"),
            ),
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_b", reason="checks failed"
        ),
        InitiativeFailed(
            plan_id="plan_1", at=AT, initiative_id="init_b", reason="checks failed"
        ),
    ]


def reproposal(
    *specs: InitiativeSpec, version: int = 2, reason: str | None = None
) -> PlanProposed:
    return PlanProposed(
        plan_id="plan_1",
        at=AT,
        version=version,
        initiatives=list(specs),
        reason=reason,
    )


def declared(events: list[Event]) -> list[InitiativeSpec]:
    return cast(PlanProposed, events[1]).initiatives


def test_frozen_work_anchors_only_settled_live_and_approved_work() -> None:
    partial = Plan.fold(partial_stream()).initiatives["init_a"]

    # A done claim and a finished attempt are not a frozen node: the residual
    # work around them is exactly what recalibration exists to revise.
    assert [sub.id for sub in partial.completed_claims] == ["init_a.2"]
    assert frozen_work(partial) is False

    # A live attempt is an anchor: its worktree and pane are still in flight.
    live = Plan.fold(partial_stream()[:4]).initiatives["init_a"]
    assert frozen_work(live) is True

    approved = Plan.fold(partial_stream() + [
        CheckpointApproved(plan_id="plan_1", at=AT, checkpoint_id="cp_1")
    ]).initiatives["init_a"]
    assert frozen_work(approved) is True
    assert [version.id for version in approved.approved_checkpoints] == ["cp_1"]
    assert frozen_work(Plan.fold(stream()).initiatives["init_a"]) is True


def test_completed_work_is_re_declared_unchanged_or_refused() -> None:
    events = stream()
    api, tests = declared(events)

    # Byte-identical re-declaration re-enters the approval gate without
    # touching a single recorded identity.
    again = Plan.fold(events + [reproposal(api, tests)])
    assert (again.version, again.approval) == (2, "pending")
    node = again.initiatives["init_a"]
    assert node.state == "settled"
    assert [attempt.id for attempt in node.attempts] == ["att_1"]
    assert [version.id for version in node.checkpoint_versions] == ["cp_1"]
    assert node.checkpoint_decisions["cp_1"].state == "approved"
    assert [sub.id for sub in node.completed_claims] == ["init_a.1"]

    # Editing settled work, or dropping it, is refused inside the fold.
    edited = api.model_copy(update={"brief": "something else entirely"})
    with pytest.raises(ValueError, match="re-declare it with its existing spec"):
        _ = Plan.fold(events + [reproposal(edited, tests)])
    orphaned = tests.model_copy(update={"depends_on": []})
    with pytest.raises(ValueError, match="keeps its id"):
        _ = Plan.fold(events + [reproposal(orphaned)])


def test_partial_edit_keeps_recorded_claim_ids_when_positions_shift() -> None:
    events = partial_stream()
    (original,) = declared(events)
    revised = original.model_copy(
        update={"subtasks": ["write the route", "wire it up", "cover the edge case"]}
    )

    plan = Plan.fold(events + [reproposal(revised)])
    initiative = plan.initiatives["init_a"]

    # The leading todo claim is gone and the done claim moved to the front: it
    # keeps the exact id its SubtaskAdvanced event named, never a renumbering.
    assert [(sub.id, sub.brief, sub.state) for sub in initiative.subtasks] == [
        ("init_a.2", "write the route", "done"),
        ("init_a.3", "wire it up", "todo"),
        ("init_a.4", "cover the edge case", "todo"),
    ]
    assert [sub.id for sub in initiative.completed_claims] == ["init_a.2"]
    assert [attempt.id for attempt in initiative.attempts] == ["att_1"]
    assert plan.approval == "pending"


def test_a_duplicated_claim_text_cannot_fan_a_done_state_out() -> None:
    duplicated = InitiativeSpec(
        id="init_a",
        name="api",
        brief="deploy twice",
        assignment=LUNA,
        subtasks=["deploy", "deploy"],
    )
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="deploy twice"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=[duplicated]),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1", initiative_id="init_a",
            assignment=LUNA,
        ),
        SubtaskAdvanced(
            plan_id="plan_1", at=AT, initiative_id="init_a",
            subtask_id="init_a.1", state="done",
        ),
        CheckpointRecorded(
            plan_id="plan_1", at=AT,
            checkpoint=Checkpoint(id="cp_1", attempt_id="att_1", exit_code=0),
        ),
    ]

    plan = Plan.fold(events + [reproposal(duplicated)])

    # Occurrence position, not claim text alone: the second copy stays todo.
    assert [(sub.id, sub.state) for sub in plan.initiatives["init_a"].subtasks] == [
        ("init_a.1", "done"),
        ("init_a.2", "todo"),
    ]


def test_a_revision_cannot_erase_a_completed_claim() -> None:
    events = partial_stream()
    (original,) = declared(events)

    erased = original.model_copy(update={"subtasks": ["read the router", "wire it up"]})
    with pytest.raises(ValueError, match="drops the done claim init_a.2"):
        _ = Plan.fold(events + [reproposal(erased)])

    # The same revision with the completed claim still declared is accepted,
    # and the claim keeps its recorded id and state.
    kept = original.model_copy(
        update={"subtasks": ["read the router", "write the route", "wire it up", "more"]}
    )
    accepted = Plan.fold(events + [reproposal(kept)])
    assert [(sub.id, sub.state) for sub in accepted.initiatives["init_a"].completed_claims] == [
        ("init_a.2", "done")
    ]


def test_residual_extraction_keeps_the_anchor_and_needs_approval_to_run() -> None:
    events = partial_stream()
    (original,) = declared(events)
    anchor = original.model_copy(update={"subtasks": ["write the route"]})
    residual = InitiativeSpec(
        id="init_b",
        name="api residual",
        brief="wire the endpoint up",
        assignment=LUNA,
        routes=Routes(writes=["src/api/**"]),
        subtasks=["wire it up"],
    )
    revision = [reproposal(anchor, residual)]

    plan = Plan.fold(events + revision)

    # The completed claim stays on its recorded node, which also keeps its
    # attempt; the extracted residual is a fresh, schedulable node.
    assert [(sub.id, sub.state) for sub in plan.initiatives["init_a"].subtasks] == [
        ("init_a.2", "done")
    ]
    assert [attempt.id for attempt in plan.initiatives["init_a"].attempts] == ["att_1"]
    assert [(sub.id, sub.state) for sub in plan.initiatives["init_b"].subtasks] == [
        ("init_b.1", "todo")
    ]
    assert plan.ready() == ["init_b"]

    # A revised plan executes only after its own approval.
    with pytest.raises(ValueError, match="plan must be approved"):
        _ = Plan.fold(events + revision + [
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2", initiative_id="init_b",
                assignment=LUNA,
            )
        ])
    for _ in range(2):  # replay is deterministic, identities included
        replayed = Plan.fold(events + revision + [
            PlanApproved(plan_id="plan_1", at=AT, version=2),
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2", initiative_id="init_b",
                assignment=LUNA,
            ),
        ])
        assert replayed.initiatives["init_b"].state == "running"
        assert replayed.initiatives["init_b"].subtasks[0].state == "todo"
        assert replayed.initiatives["init_a"].completed_claims[0].id == "init_a.2"
        assert [attempt.id for attempt in replayed.initiatives["init_a"].attempts] == [
            "att_1"
        ]


def test_rename_carry_moves_recorded_state_and_rekeys_failure_signatures() -> None:
    events = unfinished_failure_stream()
    (unfinished,) = declared(events)
    moved = unfinished.model_copy(update={"id": "init_bb", "name": "flag"})

    plan = Plan.fold(events + [reproposal(moved)])
    initiative = plan.initiatives["init_bb"]

    assert plan.retired == []
    assert initiative.known_ids == ["init_b", "init_bb"]
    assert initiative.state == "failed"
    assert [attempt.id for attempt in initiative.attempts] == ["att_1"]
    # Artifact identity is never rewritten; the attempt points at the id it ran
    # under, and the alias is what resolves the rename.
    assert initiative.attempts[0].initiative_id == "init_b"
    assert [version.id for version in initiative.checkpoint_versions] == ["cp_1"]
    assert [failure.reason for failure in initiative.failures] == ["checks failed"] * 2
    assert initiative.spec.policy.max_attempts == MAX_ATTEMPTS
    # Repeated-failure stopping binds to the renamed node, and the old key is
    # gone so reusing that id later cannot inherit another node's counts.
    assert set(plan.failure_signatures) == {("init_bb", "error", "checks failed")}
    assert plan.failure_signatures[("init_bb", "error", "checks failed")].count == 2


def test_an_ambiguous_duplicate_digest_refuses_the_rename() -> None:
    events = unfinished_failure_stream()
    (unfinished,) = declared(events)
    twin = unfinished.model_copy(update={"id": "init_b2", "name": "twin"})
    two_identical = [
        PlanCreated(plan_id="plan_1", at=AT, brief="ship the flag"),
        PlanProposed(
            plan_id="plan_1", at=AT, version=1, initiatives=[unfinished, twin]
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
    ]

    with pytest.raises(ValueError, match="share content"):
        _ = Plan.fold(two_identical + [
            reproposal(unfinished.model_copy(update={"id": "init_bb"}))
        ])


def test_dropped_unfinished_nodes_are_retired_not_schedulable() -> None:
    events = unfinished_failure_stream()
    (unfinished,) = declared(events)
    fresh = InitiativeSpec(
        id="init_c",
        name="fresh",
        brief="unrelated work",
        assignment=LUNA,
        routes=Routes(writes=["src/c/**"]),
    )

    plan = Plan.fold(events + [reproposal(fresh)])

    assert sorted(plan.initiatives) == ["init_c"]
    assert [initiative.spec.id for initiative in plan.retired] == ["init_b"]
    assert plan.ready() == ["init_c"]
    # Retired work is not schedulable, but its attempts were paid for: the
    # plan-wide burn and the ledger keep them, while its own allowance is gone.
    assert plan.accounted_token_burn() == 50
    assert plan.accounted_token_burn("init_b") == 0
    assert plan.accounted_token_burn("init_c") == 0
    with pytest.raises(ValueError, match="unknown initiative init_b"):
        _ = Plan.fold(events + [
            reproposal(fresh),
            PlanApproved(plan_id="plan_1", at=AT, version=2),
            AttemptStarted(
                plan_id="plan_1", at=AT, attempt_id="att_2", initiative_id="init_b",
                assignment=LUNA,
            ),
        ])
    # Alias and id reuse fail closed: a retired id already owns the accounting
    # and evidence lookups that name it.
    with pytest.raises(ValueError, match="already held by another record"):
        _ = Plan.fold(events + [
            reproposal(fresh),
            reproposal(fresh, unfinished, version=3),
        ])
    renamed = Plan.fold(events + [
        reproposal(unfinished.model_copy(update={"id": "init_bb", "name": "flag"}))
    ])
    assert renamed.initiatives["init_bb"].known_ids == ["init_b", "init_bb"]
    with pytest.raises(ValueError, match="already held by another record"):
        _ = Plan.fold(events + [
            reproposal(unfinished.model_copy(update={"id": "init_bb", "name": "flag"})),
            reproposal(fresh, unfinished, version=3),
        ])


def test_plan_proposal_reason_is_audit_only_and_replays_as_none() -> None:
    events = partial_stream()
    (original,) = declared(events)
    revised = original.model_copy(update={"brief": "revised health endpoint"})

    tagged = reproposal(revised, reason="the old plan was obsolete")
    assert Plan.fold(events + [tagged]).version == 2
    # The fold ignores the reason, and a pre-Sprint-7 payload has none.
    untagged = PlanProposed(
        plan_id="plan_1", at=AT, version=2, initiatives=[revised]
    )
    assert untagged.reason is None
    assert PlanProposed.model_validate_json(untagged.model_dump_json()).reason is None
    assert Plan.fold(events + [untagged]).version == 2


def test_a_refused_revision_persists_nothing(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    try:
        for event in stream():
            _ = store.append(event)

        (api, _tests) = declared(stream())
        edited = api.model_copy(update={"brief": "somewhere else"})
        with pytest.raises(ValueError, match="re-declare it with its existing spec"):
            _ = store.append(reproposal(edited))

        # The fold refuses before the store writes, so replay still shows
        # exactly the version that was accepted.
        assert [event.type for event in store.read("plan_1")] == [
            "plan_created",
            "plan_proposed",
            "plan_approved",
            "attempt_started",
            "subtask_advanced",
            "checkpoint_recorded",
            "initiative_settled",
        ]
        assert store.load("plan_1").version == 1
    finally:
        store.close()
