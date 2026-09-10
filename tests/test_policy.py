import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typing_extensions import override

from herdsman.classes import (
    APPROVE_CONTRACT,
    APPROVE_DIFF_SIZE,
    APPROVE_SCOPE,
    Assignment,
    Checkpoint,
    CheckResult,
    Contract,
    ESCALATE_OPERATOR_REVIEW,
    InitiativePolicy,
    InitiativeSpec,
    PlanCreated,
    PlanProposed,
    PolicyDecisionRecorded,
    Routes,
    RuntimeObserved,
    STOP_LOSS_BUDGET,
    STOP_LOSS_RETRY_CEILING,
    Usage,
)
from herdsman.policy import digest_projection, evaluate_checkpoint
from herdsman.store import EventStore
from tests.test_classes import stream
from tests.test_daemon import StubCollector, StubRuntime, local_daemon
from tests.test_dag_run import seed, spec


class SequenceRuntime(StubRuntime):
    exit_codes: list[int]
    exit_code: int

    def __init__(self, exit_codes: list[int]) -> None:
        super().__init__()
        self.exit_codes = exit_codes

    @override
    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ) -> AsyncIterator[RuntimeObserved]:
        self.exit_code = self.exit_codes.pop(0)
        async for event in super().observe_events(
            plan_id, attempt_id, pane_ref, match=match
        ):
            yield event


class StubBudgetGuard:
    admitted: bool

    def __init__(self, admitted: bool) -> None:
        self.admitted = admitted
        self.calls: list[tuple[str, int, Usage | None]] = []

    def admit(
        self, *, initiative_id: str, budget: int, usage: Usage | None
    ) -> bool:
        self.calls.append((initiative_id, budget, usage))
        return self.admitted


def test_policy_defaults_and_clean_rule_attribution() -> None:
    spec = InitiativeSpec(
        id="a",
        name="a",
        brief="a",
        assignment=Assignment(harness="luna", model="cheap"),
        routes=Routes(writes=["src/**"]),
        policy=InitiativePolicy(max_diff_lines=10),
    )
    checkpoint = Checkpoint(
        id="cp",
        attempt_id="attempt",
        changed_paths=["src/a.py"],
        diff_lines=2,
        checks=[],
        exit_code=0,
        usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
    )
    decision = evaluate_checkpoint(spec, checkpoint)
    assert decision.outcome == "approved"
    assert decision.rule_ids == [
        "approve.contract",
        "approve.checks_green",
        "approve.scope",
        "approve.diff_size",
    ]


def test_policy_decision_requires_rule_attribution() -> None:
    with pytest.raises(ValueError):
        _ = PolicyDecisionRecorded(
            plan_id="p",
            at=datetime(2026, 8, 25, tzinfo=UTC),
            initiative_id="a",
            outcome="stopped",
            rule_ids=[],
        )


def test_policy_bounds_cover_diff_scope_contract_retry_and_escalation() -> None:
    over_limit = spec("a", writes=["src/"]).model_copy(
        update={
            "policy": InitiativePolicy(max_diff_lines=2, operator_review=False),
        }
    )
    oversized = Checkpoint(
        id="cp",
        attempt_id="attempt",
        changed_paths=["src/a.py"],
        diff_lines=3,
        exit_code=0,
        usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
    )
    decision = evaluate_checkpoint(over_limit, oversized)
    assert decision.outcome == "stopped"
    assert decision.rule_ids == [APPROVE_DIFF_SIZE]

    escalated = evaluate_checkpoint(
        over_limit.model_copy(
            update={"policy": InitiativePolicy(max_diff_lines=2)}
        ),
        oversized,
    )
    assert escalated.outcome == "escalated"
    assert escalated.rule_ids == [ESCALATE_OPERATOR_REVIEW]

    out_of_scope = evaluate_checkpoint(
        spec("a", writes=["src/"]),
        oversized.model_copy(update={"changed_paths": ["docs/a.md"]}),
    )
    assert out_of_scope.outcome == "stopped"
    assert out_of_scope.rule_ids == [APPROVE_SCOPE]

    contract = spec("a").model_copy(
        update={"contract": Contract(id="required", required_checks=["pytest"])}
    )
    missing_check = evaluate_checkpoint(
        contract,
        Checkpoint(
            id="cp",
            attempt_id="attempt",
            exit_code=0,
            usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
        ),
    )
    assert missing_check.outcome == "stopped"
    assert missing_check.rule_ids == [APPROVE_CONTRACT]

    failed = oversized.model_copy(
        update={
            "checks": [CheckResult(name="pytest", passed=False)],
            "exit_code": 1,
        }
    )
    ceiling = evaluate_checkpoint(
        spec("a", writes=["src/"]), failed, attempt_count=3
    )
    assert ceiling.outcome == "stopped"
    assert ceiling.rule_ids == [STOP_LOSS_RETRY_CEILING]


def test_configured_budget_fails_closed_without_ledger() -> None:
    spec = InitiativeSpec(
        id="a",
        name="a",
        brief="a",
        assignment=Assignment(harness="luna", model="cheap"),
        policy=InitiativePolicy(token_budget=100),
    )
    checkpoint = Checkpoint(
        id="cp",
        attempt_id="attempt",
        exit_code=0,
        usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
    )
    decision = evaluate_checkpoint(spec, checkpoint)
    assert decision.outcome == "stopped"
    assert decision.rule_ids == ["stop_loss.budget"]


def test_failed_check_budget_guard_stops_before_retry() -> None:
    guarded = spec("a").model_copy(
        update={"policy": InitiativePolicy(token_budget=100)}
    )
    checkpoint = Checkpoint(
        id="cp",
        attempt_id="attempt",
        checks=[CheckResult(name="pytest", passed=False)],
        exit_code=1,
        usage=Usage(input_tokens=90, output_tokens=20, source="harness"),
    )
    guard = StubBudgetGuard(admitted=False)
    decision = evaluate_checkpoint(
        guarded, checkpoint, attempt_count=1, budget_guard=guard
    )
    assert decision.outcome == "stopped"
    assert decision.rule_ids == [STOP_LOSS_BUDGET]
    assert guard.calls == [("a", 100, checkpoint.usage)]


def test_present_budget_guard_admits_clean_checkpoint() -> None:
    guarded = spec("a").model_copy(
        update={"policy": InitiativePolicy(token_budget=100)}
    )
    usage = Usage(input_tokens=10, output_tokens=20, source="harness")
    guard = StubBudgetGuard(admitted=True)
    decision = evaluate_checkpoint(
        guarded,
        Checkpoint(id="cp", attempt_id="attempt", exit_code=0, usage=usage),
        budget_guard=guard,
    )
    assert decision.outcome == "approved"
    assert guard.calls == [("a", 100, usage)]


def test_direct_unattended_retries_failed_check_to_approval(tmp_path: Path) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        _ = seed(daemon, spec("a", writes=["src/"]))
        runtime = SequenceRuntime([1, 0])
        checkpoint = asyncio.run(
            daemon.run_and_settle(
                "p",
                "a",
                runtime=runtime,
                collector=StubCollector(),
                unattended=True,
            )
        )
        assert checkpoint is not None
        plan = daemon.plan("p")
        initiative = plan.initiatives["a"]
        assert initiative.state == "settled"
        assert len(initiative.attempts) == 2
        assert initiative.attempts[1].origin == "retry"
        assert [decision.outcome for decision in plan.policy_decisions] == [
            "stopped",
            "approved",
        ]
    finally:
        store.close()


def test_direct_unattended_retries_failed_check_to_ceiling(tmp_path: Path) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        limited = spec("a", writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(max_attempts=2)}
        )
        _ = seed(daemon, limited)
        runtime = SequenceRuntime([1, 1])
        checkpoint = asyncio.run(
            daemon.run_and_settle(
                "p",
                "a",
                runtime=runtime,
                collector=StubCollector(),
                unattended=True,
            )
        )
        assert checkpoint is not None
        plan = daemon.plan("p")
        assert plan.initiatives["a"].state == "failed"
        assert len(plan.initiatives["a"].attempts) == 2
        assert plan.policy_decisions[-1].rule_ids == [STOP_LOSS_RETRY_CEILING]
    finally:
        store.close()


def test_unattended_unapproved_budget_request_does_not_mutate_stream(
    tmp_path: Path,
) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        budgeted = spec("a", writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(token_budget=1)}
        )
        _ = daemon.append(PlanCreated(plan_id="p", at=datetime.now(UTC), brief="brief"))
        _ = daemon.append(
            PlanProposed(
                plan_id="p",
                at=datetime.now(UTC),
                version=1,
                initiatives=[budgeted],
            )
        )
        before = store.read("p")
        with pytest.raises(PermissionError):
            _ = asyncio.run(daemon.run_and_settle("p", "a", unattended=True))
        assert store.read("p") == before
    finally:
        store.close()


def test_unattended_dependency_blocked_budget_request_does_not_mutate_stream(
    tmp_path: Path,
) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        budgeted = spec("b", depends_on=["a"], writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(token_budget=1)}
        )
        _ = seed(daemon, spec("a"), budgeted)
        before = store.read("p")
        with pytest.raises(ValueError, match="is not ready"):
            _ = asyncio.run(daemon.run_and_settle("p", "b", unattended=True))
        assert store.read("p") == before
    finally:
        store.close()


def test_unattended_scheduler_skips_dependency_blocked_budget_retry(
    tmp_path: Path,
) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        producer = spec("a", writes=["a/"])
        budgeted = spec("b", depends_on=["a"], writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(token_budget=1)}
        )
        _ = seed(daemon, producer, budgeted)
        checkpoint = asyncio.run(
            daemon.run_and_settle(
                "p", "a", runtime=StubRuntime(), collector=StubCollector()
            )
        )
        assert checkpoint is not None
        _ = asyncio.run(
            daemon.run_and_settle(
                "p", "b", runtime=StubRuntime(exit_code=1), collector=StubCollector()
            )
        )
        attempt_id = daemon.plan("p").initiatives["b"].attempts[-1].id
        _ = daemon.append(
            PolicyDecisionRecorded(
                plan_id="p",
                at=datetime.now(UTC),
                initiative_id="b",
                attempt_id=attempt_id,
                outcome="stopped",
                rule_ids=["approve.checks_green"],
                reason="failed checks",
            )
        )
        _ = daemon.reject_checkpoint("p", checkpoint.id, by="reviewer", reason="bad")
        before = store.read("p")
        _ = asyncio.run(daemon.run_unattended("p"))
        assert store.read("p") == before
    finally:
        store.close()


def test_unattended_records_approval_rule(tmp_path: Path) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        _ = seed(daemon, spec("a", writes=["src/"]))
        plan = asyncio.run(
            daemon.run_unattended(
                "p", runtime_factory=StubRuntime, collector=StubCollector()
            )
        )
        assert plan.initiatives["a"].state == "settled"
        assert plan.policy_decisions[0].outcome == "approved"
        assert "approve.checks_green" in plan.policy_decisions[0].rule_ids
    finally:
        store.close()


def test_direct_unattended_budget_stops_before_attempt_without_ledger(
    tmp_path: Path,
) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        budgeted = spec("a", writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(token_budget=1)}
        )
        _ = seed(daemon, budgeted)
        assert asyncio.run(daemon.run_and_settle("p", "a", unattended=True)) is None
        plan = daemon.plan("p")
        assert plan.initiatives["a"].attempts == []
        assert plan.policy_decisions[0].rule_ids == [STOP_LOSS_BUDGET]
    finally:
        store.close()


def test_unattended_budget_stops_before_attempt_without_ledger(tmp_path: Path) -> None:
    store, daemon = local_daemon(tmp_path)
    try:
        budgeted = spec("a", writes=["src/"]).model_copy(
            update={"policy": InitiativePolicy(token_budget=1)}
        )
        _ = seed(daemon, budgeted)
        plan = asyncio.run(daemon.run_unattended("p"))
        initiative = plan.initiatives["a"]
        assert initiative.state == "failed"
        assert initiative.attempts == []
        assert plan.policy_decisions[0].rule_ids == ["stop_loss.budget"]
    finally:
        store.close()


def test_replay_prefix_and_policy_event_digest(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    try:
        persisted = [store.append(event) for event in stream()]
        prefix = store.read("plan_1", through_seq=persisted[5].seq)
        assert prefix[-1].type == "checkpoint_recorded"
        assert len(prefix) == 6
        decision = store.append(
            PolicyDecisionRecorded(
                plan_id="plan_1",
                at=datetime(2026, 8, 25, 12, 1, tzinfo=UTC),
                initiative_id="init_a",
                attempt_id="att_1",
                checkpoint_id="cp_1",
                outcome="approved",
                rule_ids=["approve.checks_green"],
                reason="checks passed",
            )
        )
        assert decision.seq > persisted[-1].seq
        plan = store.load("plan_1")
        first = digest_projection(plan)
        second = digest_projection(store.load("plan_1"))
        assert first == second
        assert first.decisions[0].rule_ids == ["approve.checks_green"]

        stopped = store.append(
            PolicyDecisionRecorded(
                plan_id="plan_1",
                at=datetime(2026, 8, 25, 12, 2, tzinfo=UTC),
                initiative_id="init_a",
                attempt_id="att_1",
                checkpoint_id="cp_1",
                outcome="stopped",
                rule_ids=[STOP_LOSS_BUDGET],
                reason="budget refused",
            )
        )
        assert stopped.seq > decision.seq
        digest = digest_projection(store.load("plan_1"))
        assert digest.decisions[-1].outcome == "stopped"
        assert digest.decisions[-1].rule_ids == [STOP_LOSS_BUDGET]

        timestamp_prefix = store.read(
            "plan_1", through_at=datetime(2026, 8, 25, 12, 1, tzinfo=UTC)
        )
        assert all(
            event.at <= datetime(2026, 8, 25, 12, 1, tzinfo=UTC)
            for event in timestamp_prefix
        )
        assert len(timestamp_prefix) == len(stream()) + 1
    finally:
        store.close()
