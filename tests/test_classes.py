from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError
from pydantic import TypeAdapter

from herdsman.classes import (
    ArtifactRef,
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
    Usage,
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
