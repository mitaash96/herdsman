"""Contracts seam: required gates fail typed, in-scope green passes."""

from uuid import uuid4

from herdsman.classes import (
    Assignment,
    CheckResult,
    Checkpoint,
    Contract,
    InitiativeSpec,
    Routes,
    Usage,
)
from herdsman.contracts import (
    DEFAULT_CONTRACT,
    REVIEW_CONTRACT,
    VERIFY_CHECK,
    ContractError,
    is_acceptable,
    summarize_violations,
    validate_checkpoint,
)

USAGE = Usage(input_tokens=10, output_tokens=5, source="harness")


def spec(writes: list[str] | None = None) -> InitiativeSpec:
    return InitiativeSpec(
        id="a",
        name="a",
        brief="implement a",
        assignment=Assignment(harness="luna", model="cheap-1"),
        routes=Routes(writes=writes if writes is not None else ["a/"]),
    )


def checkpoint(
    *,
    changed: list[str] | None = None,
    checks: list[CheckResult] | None = None,
    patch: str | None = ".herdsman/artifacts/cp_1.patch",
    exit_code: int | None = 0,
) -> Checkpoint:
    return Checkpoint(
        id=f"cp_{uuid4().hex}",
        attempt_id="attempt_1",
        changed_paths=list(changed) if changed is not None else ["a/out.py"],
        checks=list(checks)
        if checks is not None
        else [CheckResult(name="uv run pytest -q", passed=True)],
        exit_code=exit_code,
        usage=USAGE,
        patch_path=patch,
    )


def codes(s: InitiativeSpec, c: Checkpoint, contract: Contract | None = None) -> list[str]:
    return [v.code for v in validate_checkpoint(s, c, contract)]


def test_green_in_scope_checkpoint_is_acceptable() -> None:
    assert validate_checkpoint(spec(), checkpoint()) == []
    assert is_acceptable(spec(), checkpoint()) is True


def test_missing_required_check_is_a_typed_failure() -> None:
    contract = Contract(id="c", required_checks=["uv run pytest -q", "lint"])
    found = validate_checkpoint(spec(), checkpoint(), contract)
    assert [v.code for v in found] == ["missing-check"]
    assert found[0].detail == "lint"
    assert is_acceptable(spec(), checkpoint(), contract) is False


def test_failing_required_check_is_a_typed_failure() -> None:
    contract = Contract(id="c", required_checks=["uv run pytest -q"])
    bad = checkpoint(checks=[CheckResult(name="uv run pytest -q", passed=False)])
    found = validate_checkpoint(spec(), bad, contract)
    assert [v.code for v in found] == ["failed-check"]
    assert found[0].detail == "uv run pytest -q"


def test_unrequired_failing_check_also_fails() -> None:
    bad = checkpoint(checks=[CheckResult(name="uv run pytest -q", passed=False)])
    assert codes(spec(), bad, DEFAULT_CONTRACT) == ["failed-check"]


def test_missing_patch_and_artifact_are_typed_failures() -> None:
    contract = Contract(id="c", require_patch=True, required_paths=["a/out.py"])
    assert codes(spec(), checkpoint(patch=None), contract) == ["missing-patch"]
    missing = checkpoint(changed=["a/other.py"])
    assert codes(spec(), missing, contract) == ["missing-artifact"]


def test_out_of_scope_write_is_a_typed_failure() -> None:
    # No explicit contract: scope is enforced from the spec's declared writes.
    bad = checkpoint(changed=["elsewhere/out.py"])
    found = validate_checkpoint(spec(["a/"]), bad)
    assert [v.code for v in found] == ["out-of-scope-write"]
    assert found[0].detail == "elsewhere/out.py"


def test_scope_uses_prefix_grammar_like_contention() -> None:
    # Declared "a/" owns its subtree; the sibling tree is outside it.
    assert validate_checkpoint(spec(["a/"]), checkpoint(changed=["a/deep/out.py"])) == []
    assert codes(spec(["a/"]), checkpoint(changed=["other/out.py"])) == [
        "out-of-scope-write"
    ]


def test_empty_writes_means_no_writes() -> None:
    assert codes(spec([]), checkpoint(changed=[])) == []
    assert codes(spec([]), checkpoint(changed=["a/out.py"])) == ["out-of-scope-write"]


def test_review_contract_rejects_writes_even_in_scope() -> None:
    found = validate_checkpoint(spec(["a/"]), checkpoint(), REVIEW_CONTRACT)
    assert [v.code for v in found] == ["write-not-permitted"]
    # A read-only checkpoint with no writes still passes a no-write contract.
    assert validate_checkpoint(spec(["a/"]), checkpoint(changed=[]), REVIEW_CONTRACT) == []


def test_no_write_contract_is_expressible_directly() -> None:
    contract = Contract(id="c", role="reviewer", allow_writes=False)
    assert codes(spec(), checkpoint(), contract) == ["write-not-permitted"]


def test_command_policy_rejects_unlisted_checks() -> None:
    contract = Contract(id="c", allowed_commands=["uv run pytest -q"])
    assert validate_checkpoint(spec(), checkpoint(), contract) == []
    other = checkpoint(checks=[CheckResult(name="rm -rf /tmp/x", passed=True)])
    found = validate_checkpoint(spec(), other, contract)
    assert [v.code for v in found] == ["command-not-permitted"]


def test_approval_policy_lives_on_the_spec_not_the_contract() -> None:
    """One source of truth: the fold reads `spec.approval`; the contract gates."""
    assert InitiativeSpec(
        id="a", name="a", brief="b", assignment=Assignment(harness="luna", model="m"),
        approval="required",
        contract=Contract(id="g"),
    ).approval == "required"
    # Approval is a downstream gate, not a checkpoint defect.
    assert validate_checkpoint(spec(), checkpoint(), Contract(id="g")) == []


def test_per_task_contract_travels_on_the_spec_and_joins_the_digest() -> None:
    gated = spec()
    assert gated.contract is None  # legacy plans carry no contract
    contracted = InitiativeSpec(
        id="a",
        name="a",
        brief="implement a",
        assignment=Assignment(harness="luna", model="cheap-1"),
        routes=Routes(writes=["a/"]),
        contract=Contract(id="gated", required_checks=[VERIFY_CHECK]),
    )
    assert contracted.contract is not None
    assert contracted.digest != gated.digest  # the contract is plan identity
    assert (
        contracted.model_copy(update={"contract": Contract(id="gated")}).digest
        != contracted.digest
    )


def test_backward_compat_defaults_require_nothing_declared() -> None:
    bare = checkpoint(changed=[], checks=[])
    assert validate_checkpoint(spec([]), bare) == []
    assert validate_checkpoint(spec([]), bare, None) == []
    assert validate_checkpoint(spec([]), bare, Contract(id="x")) == []


def test_violations_summarize_deterministically() -> None:
    contract = Contract(id="c", required_checks=["lint"])
    bad = checkpoint(changed=["elsewhere/out.py"], exit_code=1)
    summary = summarize_violations(validate_checkpoint(spec(["a/"]), bad, contract))
    assert summary == (
        "nonzero-exit: checkpoint exited 1; "
        "missing-check: required check 'lint' did not run; "
        "out-of-scope-write: changed path 'elsewhere/out.py' is outside declared writes"
    )


def test_settlement_refusal_is_typed_and_carries_the_violations() -> None:
    contract = Contract(id="c", required_checks=["lint"])
    bad = checkpoint(changed=["elsewhere/out.py"])
    violations = validate_checkpoint(spec(["a/"]), bad, contract)
    error = ContractError(summarize_violations(violations), violations=violations)
    assert isinstance(error, ValueError)
    assert [violation.code for violation in error.violations] == [
        "missing-check",
        "out-of-scope-write",
    ]
    assert "out-of-scope-write" in str(error)
