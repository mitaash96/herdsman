"""Contract-check seam over the domain's pure validator.

Sprint 3 makes contracted checkpoints, not generic task completion, control
downstream progress. The schemas and the pure `validate_checkpoint` rule live
in `classes.py` — the authoritative event fold enforces them before
`InitiativeSettled` can apply, so no direct append or replay can accept a
violating checkpoint. This module re-exports that seam and keeps the pieces
that are policy vocabulary rather than domain state: the in-process
`verify-proposed` check name, the review preset, the builtin roles, and the
one-line acceptance predicate. No I/O, no model calls, no hidden state.
"""

from __future__ import annotations

from .classes import (
    Checkpoint,
    Contract,
    ContractError,
    ContractViolation,
    DEFAULT_CONTRACT,
    InitiativeSpec,
    Role,
    ViolationCode,
    summarize_violations,
    validate_checkpoint,
)

VERIFY_CHECK = "verify-proposed"
"""The in-process contract check the daemon computes from the attempt worktree.

A contract that lists it under `required_checks` gets the proposed-code
verifier run over the attempt's composed Python files; BLOCK fails the check,
PASS/WARN pass it (WARN is reviewer attention, not rejection). Unlike shell
checks it never reaches the executor, so `allowed_commands` must list it
explicitly when a command policy is set.
"""

REVIEW_CONTRACT = Contract(
    id="review",
    role="reviewer",
    allow_writes=False,
)
"""Read-only review: in-scope or not, any write is rejected."""

BUILTIN_ROLES: tuple[Role, ...] = (
    Role(name="implementer", description="Produces changes within its write scope."),
    Role(name="reviewer", description="Reads and comments; must not write."),
)


def is_acceptable(
    spec: InitiativeSpec,
    checkpoint: Checkpoint,
    contract: Contract | None = None,
) -> bool:
    """Whether a checkpoint satisfies its contract (no violations)."""
    return not validate_checkpoint(spec, checkpoint, contract)


__all__ = [
    "BUILTIN_ROLES",
    "DEFAULT_CONTRACT",
    "REVIEW_CONTRACT",
    "VERIFY_CHECK",
    "Contract",
    "ContractError",
    "ContractViolation",
    "Role",
    "ViolationCode",
    "is_acceptable",
    "summarize_violations",
    "validate_checkpoint",
]
