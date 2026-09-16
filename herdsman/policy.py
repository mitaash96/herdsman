"""Pure policy evaluation and deterministic decision digesting.

This module does not call a runtime, ledger, or model. A configured budget is
intentionally refused without a BudgetGuard supplied by Sprint 6-A.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

from .classes import (
    APPROVE_CHECKS_GREEN,
    APPROVE_CONTRACT,
    APPROVE_DIFF_SIZE,
    APPROVE_SCOPE,
    ESCALATE_OPERATOR_REVIEW,
    STOP_LOSS_BUDGET,
    STOP_LOSS_RETRY_CEILING,
    Checkpoint,
    DEFAULT_CONTRACT,
    FrozenModel,
    InitiativeSpec,
    Plan,
    Usage,
    validate_checkpoint,
)


class BudgetGuard(Protocol):
    """The 6-A ledger admission seam; no ledger implementation belongs here."""

    def admit(
        self,
        *,
        initiative_id: str,
        budget: int,
        usage: Usage | None,
    ) -> bool: ...


class PolicyEvaluation(FrozenModel):
    """Pure result of evaluating one recorded checkpoint."""

    outcome: Literal["approved", "stopped", "escalated"]
    rule_ids: list[str] = []
    reason: str = ""


class PolicyDigestEntry(FrozenModel):
    """One returning-user digest item, retaining its authorizing rules."""

    initiative_id: str
    attempt_id: str | None = None
    checkpoint_id: str | None = None
    outcome: str
    rule_ids: list[str] = []
    reason: str = ""


class PolicyDigest(FrozenModel):
    plan_id: str
    decisions: list[PolicyDigestEntry] = []
    digest: str


def evaluate_checkpoint(
    spec: InitiativeSpec,
    checkpoint: Checkpoint,
    *,
    attempt_count: int = 1,
    budget_guard: BudgetGuard | None = None,
) -> PolicyEvaluation:
    """Evaluate policy mechanically, without side effects.

    The contract validator remains the source of truth for evidence validity;
    this function only maps its deterministic result to stable policy rules.
    """
    contract = spec.contract or DEFAULT_CONTRACT
    violations = validate_checkpoint(spec, checkpoint, contract)
    scope = any(
        item.code in {"out-of-scope-write", "write-not-permitted"}
        for item in violations
    )
    if scope:
        return PolicyEvaluation(
            outcome="stopped",
            rule_ids=[APPROVE_SCOPE],
            reason="checkpoint changed paths outside the declared scope",
        )

    def budget_decision() -> PolicyEvaluation | None:
        if spec.policy.token_budget is None:
            return None
        if budget_guard is None:
            return PolicyEvaluation(
                outcome="stopped",
                rule_ids=[STOP_LOSS_BUDGET],
                reason="token budget is configured but no ledger BudgetGuard is available",
            )
        try:
            admitted = budget_guard.admit(
                initiative_id=spec.id,
                budget=spec.policy.token_budget,
                usage=checkpoint.usage,
            )
        except Exception as exc:
            return PolicyEvaluation(
                outcome="stopped",
                rule_ids=[STOP_LOSS_BUDGET],
                reason=f"budget admission failed closed: {exc}",
            )
        if not admitted:
            return PolicyEvaluation(
                outcome="stopped",
                rule_ids=[STOP_LOSS_BUDGET],
                reason="token budget admission refused",
            )
        return None

    failed_checks = [check.name for check in checkpoint.checks if not check.passed]
    if checkpoint.exit_code != 0 or failed_checks:
        other_violations = [
            item
            for item in violations
            if item.code not in {"failed-check", "nonzero-exit"}
        ]
        if other_violations:
            return PolicyEvaluation(
                outcome="stopped",
                rule_ids=[APPROVE_CONTRACT],
                reason="; ".join(item.message for item in other_violations),
            )
        if attempt_count >= spec.policy.max_attempts:
            return PolicyEvaluation(
                outcome="stopped",
                rule_ids=[STOP_LOSS_RETRY_CEILING],
                reason=f"attempt ceiling reached ({spec.policy.max_attempts})",
            )
        admitted = budget_decision()
        if admitted is not None:
            return admitted
        return PolicyEvaluation(
            outcome="stopped",
            rule_ids=[APPROVE_CHECKS_GREEN],
            reason=(
                f"checkpoint exited {checkpoint.exit_code}"
                if checkpoint.exit_code != 0
                else "failed checks: " + ", ".join(failed_checks)
            ),
        )
    if violations:
        return PolicyEvaluation(
            outcome="stopped",
            rule_ids=[APPROVE_CONTRACT],
            reason="; ".join(item.message for item in violations),
        )

    admitted = budget_decision()
    if admitted is not None:
        return admitted

    if spec.policy.max_diff_lines is not None:
        if checkpoint.diff_lines is None:
            return PolicyEvaluation(
                outcome="escalated" if spec.policy.operator_review else "stopped",
                rule_ids=[ESCALATE_OPERATOR_REVIEW] if spec.policy.operator_review else [APPROVE_DIFF_SIZE],
                reason="diff size was not mechanically recorded",
            )
        if checkpoint.diff_lines > spec.policy.max_diff_lines:
            return PolicyEvaluation(
                outcome="escalated" if spec.policy.operator_review else "stopped",
                rule_ids=[ESCALATE_OPERATOR_REVIEW] if spec.policy.operator_review else [APPROVE_DIFF_SIZE],
                reason=(
                    f"diff has {checkpoint.diff_lines} lines; limit is "
                    f"{spec.policy.max_diff_lines}"
                ),
            )

    if not spec.policy.auto_approve or spec.approval == "required":
        return PolicyEvaluation(
            outcome="escalated",
            rule_ids=[ESCALATE_OPERATOR_REVIEW],
            reason="initiative requires operator review",
        )

    rules = [APPROVE_CONTRACT, APPROVE_CHECKS_GREEN, APPROVE_SCOPE]
    if spec.policy.max_diff_lines is not None:
        rules.append(APPROVE_DIFF_SIZE)
    return PolicyEvaluation(
        outcome="approved",
        rule_ids=rules,
        reason="checkpoint satisfies the unattended policy",
    )


# Short aliases keep the seam easy to consume without adding another wrapper.
evaluate = evaluate_checkpoint


def digest_projection(plan: Plan) -> PolicyDigest:
    """Return an order-stable digest projection of folded policy decisions."""
    entries = [
        PolicyDigestEntry(
            initiative_id=event.initiative_id,
            attempt_id=event.attempt_id,
            checkpoint_id=event.checkpoint_id,
            outcome=event.outcome,
            rule_ids=list(event.rule_ids),
            reason=event.reason,
        )
        for event in plan.policy_decisions
    ]
    payload = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        sort_keys=True,
        separators=(",", ":"),
    )
    return PolicyDigest(
        plan_id=plan.id,
        decisions=entries,
        digest=hashlib.sha256(payload.encode()).hexdigest()[:16],
    )


__all__ = [
    "BudgetGuard",
    "PolicyDigest",
    "PolicyDecision",
    "PolicyDigestEntry",
    "PolicyEvaluation",
    "digest_projection",
    "evaluate",
    "evaluate_checkpoint",
]

PolicyDecision = PolicyEvaluation
