"""Canonical domain models.

Pydantic is the single definition of the domain; JSON Schema is generated from
these models and never hand-maintained. Third-party types (herdr, NetworkX,
Git) never appear here — foreign things cross this boundary as opaque strings.

Only events are persisted. Everything below `Event` is a projection rebuilt by
`Plan.fold`.

Vocabulary: an *initiative* is a single parallel unit of work — one worktree,
one implementer, one brief. A *plan* holds many of them.
"""

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from functools import reduce
from typing import Annotated, ClassVar, Literal, Never, Self, TypeVar, cast, overload

import networkx as nx
from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from typing_extensions import override


class Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


class FrozenList(list[T]):
    """A list-shaped container that rejects all in-place mutations."""

    def _immutable(self, *_args: object, **_kwargs: object) -> Never:
        raise TypeError("frozen model field cannot be mutated")

    @override
    def __delitem__(self, key: object) -> Never:
        self._immutable(key)

    @override
    def __setitem__(self, key: object, value: object) -> Never:
        self._immutable(key, value)

    @override
    def append(self, value: T) -> Never:
        self._immutable(value)

    @override
    def clear(self) -> Never:
        self._immutable()

    @override
    def extend(self, values: object) -> Never:
        self._immutable(values)

    @override
    def insert(self, index: object, value: T) -> Never:
        self._immutable(index, value)

    @override
    def pop(self, index: object = -1) -> Never:
        self._immutable(index)

    @override
    def remove(self, value: T) -> Never:
        self._immutable(value)

    @override
    def reverse(self) -> Never:
        self._immutable()

    @override
    def sort(
        self,
        *,
        key: Callable[[T], object] | None = None,
        reverse: bool = False,
    ) -> Never:
        self._immutable(key, reverse)

    @override
    def __iadd__(self, value: object) -> "FrozenList[T]":
        return self._immutable(value)

    @override
    def __imul__(self, value: object) -> "FrozenList[T]":
        return self._immutable(value)


class FrozenDict(dict[K, V]):
    """A dict-shaped container that rejects all in-place mutations."""

    def _immutable(self, *_args: object, **_kwargs: object) -> Never:
        raise TypeError("frozen model field cannot be mutated")

    @override
    def __delitem__(self, key: K) -> Never:
        self._immutable(key)

    @override
    def __setitem__(self, key: K, value: V) -> Never:
        self._immutable(key, value)

    @override
    def clear(self) -> Never:
        self._immutable()

    @override
    def pop(self, key: K, default: object = None, /) -> Never:
        self._immutable(key, default)

    @override
    def popitem(self) -> Never:
        self._immutable()

    @overload
    def setdefault(self, key: K, default: None = None, /) -> V | None: ...

    @overload
    def setdefault(self, key: K, default: V, /) -> V: ...

    @override
    def setdefault(self, key: K, default: V | None = None, /) -> V | None:
        self._immutable(key, default)

    @override
    def update(self, mapping: object = (), /, **kwargs: object) -> Never:
        self._immutable(mapping, kwargs)

    @override
    def __ior__(self, value: object) -> "FrozenDict[K, V]":
        return self._immutable(value)


def _freeze(value: object) -> object:
    """Recursively freeze containers nested in an event/value object."""
    if isinstance(value, FrozenList | FrozenDict):
        return cast(object, value)
    if isinstance(value, list):
        items = cast(list[object], value)
        return FrozenList(_freeze(item) for item in items)
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        return FrozenDict({key: _freeze(item) for key, item in items.items()})
    return value


class FrozenModel(Model):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _freeze_containers(self) -> Self:
        for field_name in self.__class__.model_fields:
            value = cast(object, getattr(self, field_name))
            frozen = _freeze(value)
            if frozen is not value:
                object.__setattr__(self, field_name, frozen)
        return self


# --- value objects -----------------------------------------------------------


class Assignment(FrozenModel):
    """Which agent CLI, on which model. Used for planner and implementer alike."""

    harness: str
    model: str


EXECUTOR_HARNESS = "luna"
"""The only executor harness the runtime compiles an implementer command for."""


LeafOrigin = Literal["redirect", "nudge", "operator-answer", "failure"]
"""Where a run-scoped ground-truth leaf came from. Closed set like ViolationCode.

`failure` leaves are promoted mechanically by the fold from repeated failure
signatures — no intervention event produces them."""


MAX_ATTEMPTS = 3
"""Fold-enforced per-initiative attempt ceiling. The guard lives in
`Plan._apply`, so neither a direct store append nor a replay can start an
attempt beyond the third — bounded retries are a projection invariant, not
daemon memory."""

# Policy rule identifiers are an API: once emitted they are never repurposed.
APPROVE_CONTRACT = "approve.contract"
APPROVE_CHECKS_GREEN = "approve.checks_green"
APPROVE_DIFF_SIZE = "approve.diff_size"
APPROVE_SCOPE = "approve.scope"
STOP_LOSS_BUDGET = "stop_loss.budget"
STOP_LOSS_RETRY_CEILING = "stop_loss.retry_ceiling"
ESCALATE_OPERATOR_REVIEW = "escalate.operator_review"
POLICY_RULE_IDS = (
    APPROVE_CONTRACT,
    APPROVE_CHECKS_GREEN,
    APPROVE_DIFF_SIZE,
    APPROVE_SCOPE,
    STOP_LOSS_BUDGET,
    STOP_LOSS_RETRY_CEILING,
    ESCALATE_OPERATOR_REVIEW,
)


REPEATED_FAILURE_LIMIT = 2
"""Failed attempts carrying the same check + normalized error before the fold
promotes exactly one mechanical memory leaf. Also the repeated-failure
stopping datum: the daemon's admission rule reads `Plan.failure_signatures`."""


class TaskBriefVersion(FrozenModel):
    """One operator redirect of a task's brief.

    Version 1 is the planner-authored `InitiativeSpec.brief` and is never
    stored here; versions 2+ are appended redirects. An attempt records which
    version it ran on, so a redirect never rewrites attempt history.
    """

    version: int
    brief: str
    by: str = "operator"
    at: AwareDatetime
    reason: str = ""


class MemoryLeaf(FrozenModel):
    """A run-scoped ground-truth fact, projected from intervention events.

    Minimal spine of the Sprint 14 leaf: the claim line plus the subject the
    daemon's auto-answer matcher keys on. Nothing here is persisted directly —
    redirects, ground-truth nudges, and operator answers arrive as events, and
    the fold projects one leaf per ground-truth intervention. Run-scoped: they
    live on the plan and are what retry and newly compiled packets include.
    """

    id: str
    subject: str
    claim: str
    origin: LeafOrigin
    by: str = "operator"
    at: AwareDatetime


_GLOB = frozenset("*?[]")


def _validate_route(path: str) -> str:
    """Accept a repository-relative path or directory prefix, and nothing else.

    A route names where an initiative may touch. Anything that could reach
    outside the worktree, or that this codebase would silently mis-compare, is
    rejected at the plan gate rather than quietly weakening serialization.
    Globs are allowed but deliberately over-approximated by `path_segments`:
    `src/*.py` is treated as the whole `src` subtree, so a glob can only ever
    cause a false conflict, never a missed one.
    """
    cleaned = path.strip()
    if not cleaned:
        raise ValueError("route path cannot be empty")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise ValueError(f"route {path!r} must be repository-relative")
    if "\\" in cleaned:
        raise ValueError(f"route {path!r} must use forward slashes")
    for segment in cleaned.strip("/").split("/"):
        if segment == "..":
            raise ValueError(f"route {path!r} cannot escape the repository")
        if segment.startswith("~"):
            raise ValueError(f"route {path!r} cannot reference a home directory")
    return cleaned


class Routes(FrozenModel):
    """Contention routes. Overlapping writes are a conflict; shared reads are not."""

    reads: list[str] = []
    writes: list[str] = []

    @model_validator(mode="after")
    def _validate_paths(self) -> Self:
        for path in (*self.reads, *self.writes):
            _ = _validate_route(path)
        return self


_GLOB_CHARS = frozenset("*?[")


def path_segments(path: str) -> tuple[str, ...]:
    """Normalize one declared route path to comparable segments.

    Matching is deliberately over-approximate: a segment containing any glob
    character truncates the path to its parent, so `src/*.py` is compared as
    the whole `src` subtree. That can raise a false conflict, which costs some
    concurrency, but it can never miss a real one -- and a missed write/write
    overlap is two agents editing one file. `Routes` rejects the forms that
    would be actively misleading. An empty result means the whole repository.
    """
    parts: list[str] = []
    for part in path.strip().strip("/").split("/"):
        if part in ("", "."):
            continue
        if _GLOB_CHARS & set(part):
            break
        parts.append(part)
    return tuple(parts)


class ScopeTrie:
    """Prefix index over declared paths; a path owns its entire subtree.

    Plain string equality would miss the overlap that actually bites: one
    initiative claiming `herdsman/` while another claims `herdsman/daemon.py`.
    """

    def __init__(self) -> None:
        self.children: dict[str, ScopeTrie] = {}
        self.owners: set[str] = set()

    def insert(self, path: str, owner: str) -> None:
        node = self
        for segment in path_segments(path):
            node = node.children.setdefault(segment, ScopeTrie())
        node.owners.add(owner)

    def _subtree_owners(self) -> set[str]:
        found = set(self.owners)
        for child in self.children.values():
            found |= child._subtree_owners()
        return found

    def touching(self, path: str) -> set[str]:
        """Owners whose declared paths overlap `path`, in either direction."""
        node = self
        found = set(node.owners)
        for segment in path_segments(path):
            child = node.children.get(segment)
            if child is None:
                return found  # nothing claims this deep; ancestors still overlap
            node = child
            found |= node.owners
        return found | node._subtree_owners()


class Usage(FrozenModel):
    """Token facts. Counts from different sources are never summed."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    source: Literal["harness", "provider", "estimate"]


class CheckResult(FrozenModel):
    name: str
    passed: bool
    summary: str = ""


def _validate_artifact_path(path: str) -> str:
    """Accept only a repository-local physical handoff artifact path."""
    cleaned = _validate_route(path)
    if cleaned != path:
        raise ValueError(f"patch artifact path {path!r} cannot have surrounding whitespace")
    parts = tuple(part for part in cleaned.split("/") if part not in ("", "."))
    if len(parts) < 3 or parts[:2] != (".herdsman", "artifacts"):
        raise ValueError(
            f"patch artifact path {path!r} must be under .herdsman/artifacts"
        )
    return cleaned


class Checkpoint(FrozenModel):
    """Evidence manifest, mechanically populated. Not model-authored prose."""

    id: str
    attempt_id: str
    changed_paths: list[str] = []
    diff_lines: int | None = Field(default=None, ge=0)
    """Mechanical changed-line count, when the collector can provide it."""
    base_sha: str | None = None
    head_sha: str | None = None
    checks: list[CheckResult] = []
    exit_code: int | None = None
    usage: Usage | None = None
    patch_path: str | None = None
    """Project-relative path to this attempt's diff, the physical handoff artifact."""
    caveats: list[str] = []
    """Only non-recoverable decisions, caveats, or blockers written by the executor."""

    @model_validator(mode="after")
    def _validate_patch_path(self) -> Self:
        if self.patch_path is not None:
            _ = _validate_artifact_path(self.patch_path)
        return self


class CheckpointDecision(FrozenModel):
    """Derived review state of one checkpoint version. The fold computes it.

    Never persisted directly: decisions are derived from review events and
    from settlement under the automatic policy, so the event stream stays the
    one authoritative record.
    """

    state: Literal["pending", "approved", "rejected", "changes_requested"] = "pending"
    decided_at: AwareDatetime | None = None
    decided_by: str = ""
    reason: str = ""
    approved_at: AwareDatetime | None = None
    """When this version was approved, if it ever was.

    Kept through a later rejection or change request: already-released
    consumers built on the approval, so taint exposure is computed against
    the time they started, not against the latest verdict.
    """


ViolationCode = Literal[
    "missing-usage",
    "nonzero-exit",
    "missing-patch",
    "missing-artifact",
    "missing-check",
    "failed-check",
    "out-of-scope-write",
    "write-not-permitted",
    "command-not-permitted",
]
"""Every way a checkpoint can fail its contract. Closed set, matched by code."""


class Role(FrozenModel):
    """A named capability label. Enforcement lives on `Contract`, not here."""

    name: str
    description: str = ""


class Contract(FrozenModel):
    """What one task's checkpoint must show before it can settle.

    Attached per task as `InitiativeSpec.contract`; a plan without an explicit
    contract keeps the Sprint 2 behavior (nothing is required, nothing is
    refused). The approval policy is `InitiativeSpec.approval`, not a contract
    field: one source of truth for whether settlement waits on review.
    """

    id: str
    role: str = "implementer"
    required_checks: list[str] = []
    """Check names (exact match on `CheckResult.name`) that must have passed."""
    required_paths: list[str] = []
    """Artifact paths (exact match on `Checkpoint.changed_paths`) that must exist."""
    require_patch: bool = False
    """When true, `Checkpoint.patch_path` must be present (the handoff bytes)."""
    allow_writes: bool = True
    """When false, any changed path is rejected -- the review/no-write gate."""
    allowed_commands: list[str] | None = None
    """When set, every executed check name must be listed (exact match)."""


class ContractViolation(FrozenModel):
    """One typed contract failure. `detail` carries the offending name/path."""

    code: ViolationCode
    message: str
    detail: str = ""


DEFAULT_CONTRACT = Contract(id="default")
"""Backward-compatible: requires nothing declared, still enforces write scope."""


class ContractError(ValueError):
    """A checkpoint cannot settle: contract validation failed, typed by code."""

    def __init__(
        self,
        message: str,
        *,
        violations: Sequence[ContractViolation] = (),
    ) -> None:
        super().__init__(message)
        self.violations: tuple[ContractViolation, ...] = tuple(violations)


class ArtifactRef(FrozenModel):
    """A settled dependency's evidence, passed across a DAG edge by reference.

    Handoffs are physical: the consumer gets the producer's checkpoint id, its
    commit, and the paths it touched — never a model-authored summary of them.
    """

    initiative_id: str
    checkpoint_id: str
    head_sha: str | None = None
    changed_paths: list[str] = []
    patch_path: str | None = None
    """Where the producer's bytes actually are. Herdsman applies it; agents do not."""

    @model_validator(mode="after")
    def _validate_patch_path(self) -> Self:
        if self.patch_path is not None:
            _ = _validate_artifact_path(self.patch_path)
        return self


class InitiativePolicy(FrozenModel):
    """Per-initiative limits for unattended execution.

    Budgets are declarations only. A configured token budget is fail-closed
    until Sprint 6-A supplies an authoritative ledger.
    """

    auto_approve: bool = True
    max_diff_lines: int | None = Field(default=None, ge=0)
    token_budget: int | None = Field(default=None, ge=0)
    max_attempts: int = Field(default=MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS)
    operator_review: bool = Field(
        default=True,
        validation_alias=AliasChoices("operator_review", "escalate_to_operator"),
    )

    @property
    def escalate_to_operator(self) -> bool:
        return self.operator_review


class InitiativeSpec(FrozenModel):
    """Planner-authored content. Immutable; travels inside `PlanProposed`."""

    id: str
    name: str
    brief: str
    assignment: Assignment
    routes: Routes = Routes()
    subtasks: list[str] = []
    """Briefs. Ids are derived positionally as `{spec.id}.{n}`, n from 1."""
    depends_on: list[str] = []
    approval: Literal["automatic", "required"] = "automatic"
    """Checkpoint approval policy for this contract.

    `automatic` preserves the Sprint 2 behavior: clean evidence settles the
    initiative by itself and releases its dependents. `required` holds the
    recorded checkpoint for review: settlement, and therefore downstream
    readiness, waits until a reviewer approves it.
    """
    contract: Contract | None = None
    """The task's declared gates: required checks/artifacts, write policy.

    None keeps the legacy behavior: nothing beyond the clean-evidence rule is
    enforced at settlement. An explicit contract also enforces declared write
    scope, so an out-of-scope diff is a typed failure instead of an
    acceptable checkpoint.
    """
    policy: InitiativePolicy = InitiativePolicy()
    """Unattended approval, budget, retry, and operator-escalation rules."""

    @property
    def digest(self) -> str:
        """Content-addressed identity: hash of brief and declared scope.

        Derived, never stored — a stored copy would drift from the content it
        names. Recalibration diffs compare digests to tell a renamed
        initiative from a materially changed one, so the inputs are exactly
        the planner-authored contract: what to do, where it may touch, which
        gates it must satisfy, and whether review gates settlement.
        """
        payload = json.dumps(
            {
                "brief": self.brief,
                "reads": sorted(self.routes.reads),
                "writes": sorted(self.routes.writes),
                "subtasks": list(self.subtasks),
                "approval": self.approval,
                "policy": self.policy.model_dump(mode="json"),
                "contract": (
                    self.contract.model_dump(mode="json")
                    if self.contract is not None
                    else None
                ),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _scope_trie(spec: InitiativeSpec) -> ScopeTrie:
    trie = ScopeTrie()
    for path in spec.routes.writes:
        trie.insert(path, "scope")
    return trie


def validate_checkpoint(
    spec: InitiativeSpec,
    checkpoint: Checkpoint,
    contract: Contract | None = None,
) -> list[ContractViolation]:
    """Check one mechanical checkpoint against its contract and write scope.

    Pure and deterministic: violations come out in a stable order (usage,
    exit, patch, artifacts, checks, writes/scope, commands) so identical
    evidence always yields identical failures. An empty list means acceptable.
    This is the rule the event fold enforces before `InitiativeSettled` can
    apply, so no append or replay path can accept a violating checkpoint.
    """
    selected = contract if contract is not None else DEFAULT_CONTRACT
    violations: list[ContractViolation] = []

    if checkpoint.usage is None:
        violations.append(
            ContractViolation(
                code="missing-usage",
                message="checkpoint has no harness-reported usage",
            )
        )
    if checkpoint.exit_code != 0:
        violations.append(
            ContractViolation(
                code="nonzero-exit",
                message=f"checkpoint exited {checkpoint.exit_code}",
            )
        )
    if selected.require_patch and checkpoint.patch_path is None:
        violations.append(
            ContractViolation(
                code="missing-patch",
                message=f"contract {selected.id!r} requires a patch artifact",
            )
        )
    for required in selected.required_paths:
        if required not in checkpoint.changed_paths:
            violations.append(
                ContractViolation(
                    code="missing-artifact",
                    message=f"required artifact {required!r} is not in changed paths",
                    detail=required,
                )
            )
    by_name = {check.name: check for check in checkpoint.checks}
    for required in selected.required_checks:
        check = by_name.get(required)
        if check is None:
            violations.append(
                ContractViolation(
                    code="missing-check",
                    message=f"required check {required!r} did not run",
                    detail=required,
                )
            )
        elif not check.passed:
            violations.append(
                ContractViolation(
                    code="failed-check",
                    message=f"required check {required!r} failed",
                    detail=required,
                )
            )
    for check in checkpoint.checks:
        if not check.passed and check.name not in selected.required_checks:
            violations.append(
                ContractViolation(
                    code="failed-check",
                    message=f"check {check.name!r} failed",
                    detail=check.name,
                )
            )
    if not selected.allow_writes:
        for path in checkpoint.changed_paths:
            violations.append(
                ContractViolation(
                    code="write-not-permitted",
                    message=(
                        f"contract {selected.id!r} forbids writes "
                        + f"but checkpoint changed {path!r}"
                    ),
                    detail=path,
                )
            )
    else:
        trie = _scope_trie(spec)
        for path in checkpoint.changed_paths:
            if not trie.touching(path):
                violations.append(
                    ContractViolation(
                        code="out-of-scope-write",
                        message=f"changed path {path!r} is outside declared writes",
                        detail=path,
                    )
                )
    if selected.allowed_commands is not None:
        permitted = set(selected.allowed_commands)
        for check in checkpoint.checks:
            if check.name not in permitted:
                violations.append(
                    ContractViolation(
                        code="command-not-permitted",
                        message=f"check {check.name!r} is not permitted "
                        + f"by contract {selected.id!r}",
                        detail=check.name,
                    )
                )
    return violations


def summarize_violations(violations: Sequence[ContractViolation]) -> str:
    """One stable human-readable line per violation, for typed failures."""
    return "; ".join(f"{violation.code}: {violation.message}" for violation in violations)


# --- events: the only thing on disk ------------------------------------------


class Ev(FrozenModel):

    plan_id: str
    at: AwareDatetime
    seq: int = 0
    """Assigned by the event store on append; ignore on construction."""
    action_id: str | None = None
    """Idempotency key for a daemon action request; None for plain records.

    The fold refuses a second event carrying a known `action_id`, so a
    repeated recovery request cannot double-apply: the daemon returns the
    original outcome instead of appending. The key is bound to the request
    it recorded — the event type plus its semantic payload, digested by
    `action_fingerprint` — so reusing a key for a different request is a
    conflict, never a silent success. Older streams replay as None."""


class PlanCreated(Ev):
    type: Literal["plan_created"] = "plan_created"
    brief: str
    planner: Assignment | None = None
    """None means the plan was fired directly, without a planning session."""


class PlanProposed(Ev):
    type: Literal["plan_proposed"] = "plan_proposed"
    version: int
    initiatives: list[InitiativeSpec]
    usage: Usage | None = None
    """What the planning call cost. Frontier planning is productive work."""

    @model_validator(mode="after")
    def _validate_dag(self) -> Self:
        graph: nx.DiGraph[str] = nx.DiGraph()
        for spec in self.initiatives:
            if spec.id in graph:
                raise ValueError(f"duplicate initiative {spec.id}")
            graph.add_node(spec.id)
        for spec in self.initiatives:
            for dependency in spec.depends_on:
                if dependency not in graph:
                    raise ValueError(
                        f"initiative {spec.id} depends on unknown initiative {dependency}"
                    )
                _ = graph.add_edge(dependency, spec.id)
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("initiative dependencies must be acyclic")
        return self


class PlanApproved(Ev):
    type: Literal["plan_approved"] = "plan_approved"
    version: int


class AttemptStarted(Ev):
    """The attempt reservation. Appended *before* any worktree or agent exists.

    The fold rejects a second attempt on a running initiative, so appending
    this first is what makes two concurrent `run` requests for one initiative
    safe: the loser is refused before it can launch a duplicate agent.
    Runtime references arrive later, in `AttemptProvisioned`.
    """

    type: Literal["attempt_started"] = "attempt_started"
    attempt_id: str
    initiative_id: str
    assignment: Assignment
    brief_version: int = 1
    """Which brief version the packet was compiled from; the fold rejects a stale one."""
    worktree_ref: str | None = None
    pane_ref: str | None = None
    packet_tokens: int = 0
    """Estimated size of the packet Herdsman injects — orchestration overhead."""
    by: str = "daemon"
    """Who reserved the attempt: the daemon for an ordinary run, the actor a
    retry names. Older events replay as daemon."""
    origin: Literal["run", "retry"] = "run"
    """Run vs retry: a retry appends a new attempt to an already-failed task."""
    unattended: bool = False
    """Whether this attempt is governed by unattended policy, persisted for recovery."""


class AttemptProvisioned(Ev):
    """A runtime resource herdr opened for an already-reserved attempt.

    Appended as soon as each reference exists, not once both do: a worktree
    whose reference was never persisted cannot be released by `discard`, so a
    failure between creating it and launching the pane would strand it.
    """

    type: Literal["attempt_provisioned"] = "attempt_provisioned"
    attempt_id: str
    worktree_ref: str
    pane_ref: str | None = None
    base_sha: str | None = None
    """The commit the attempt's diff is taken against, persisted when known so
    a daemon death before checkpoint collection cannot lose the diff base."""


class SubtaskAdvanced(Ev):
    type: Literal["subtask_advanced"] = "subtask_advanced"
    initiative_id: str
    subtask_id: str
    state: Literal["doing", "done", "skipped"]


class RuntimeObserved(Ev):
    """A herdr terminal/runtime event, passed through for the stream and audit."""

    type: Literal["runtime_observed"] = "runtime_observed"
    attempt_id: str
    kind: str
    detail: dict[str, object] = {}
    # ponytail: the one untyped payload — typing it would pull herdr's
    # vocabulary into our API. Revisit at Sprint 8 (adapter capabilities).


class CheckpointRecorded(Ev):
    type: Literal["checkpoint_recorded"] = "checkpoint_recorded"
    checkpoint: Checkpoint


class CheckpointApproved(Ev):
    """A reviewer accepted one checkpoint version."""

    type: Literal["checkpoint_approved"] = "checkpoint_approved"
    checkpoint_id: str
    by: str = "operator"
    reason: str = ""


class CheckpointRejected(Ev):
    """A reviewer refused one checkpoint version.

    Rejection never deletes anything: the version stays in the projection with
    its decision, and consumers already released by it are tainted through
    `Plan.attention`.
    """

    type: Literal["checkpoint_rejected"] = "checkpoint_rejected"
    checkpoint_id: str
    by: str = "operator"
    reason: str = ""


class CheckpointChangesRequested(Ev):
    """A reviewer asked for a revision instead of approving or rejecting."""

    type: Literal["checkpoint_changes_requested"] = "checkpoint_changes_requested"
    checkpoint_id: str
    by: str = "operator"
    reason: str = ""


class PolicyDecisionRecorded(Ev):
    """The stable attribution for one unattended policy decision."""

    type: Literal["policy_decision_recorded"] = "policy_decision_recorded"
    initiative_id: str
    attempt_id: str | None = None
    checkpoint_id: str | None = None
    outcome: Literal["approved", "stopped", "escalated"]
    rule_ids: list[str] = Field(default_factory=list, min_length=1)
    reason: str = ""


class InitiativeSettled(Ev):
    type: Literal["initiative_settled"] = "initiative_settled"
    initiative_id: str
    checkpoint_id: str


class InitiativeFailed(Ev):
    type: Literal["initiative_failed"] = "initiative_failed"
    initiative_id: str
    reason: str
    evidence: list[str] = []
    """Preserved diagnostic artifact paths (`.herdsman/artifacts/...`) recorded
    before any cleanup, so `salvage` has stable pointers to the raw failure
    evidence. Validated like checkpoint patch paths."""

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        for path in self.evidence:
            _ = _validate_artifact_path(path)
        return self


class InitiativePaused(Ev):
    """Operator pause: the scheduler stops admitting new attempts for the task.

    A live attempt at pause time keeps running and settles under the existing
    policy — pause holds the queue, it does not stop the agent (cancel does).
    The next state is not stored: resume recomputes it from attempt history."""

    type: Literal["initiative_paused"] = "initiative_paused"
    initiative_id: str
    by: str = "operator"
    reason: str = ""


class InitiativeResumed(Ev):
    """Release of a paused task. The fold recomputes the state — `failed` when
    attempt history exists (retryable), else `pending` (runnable) — so no
    prior state is stored and replay is deterministic."""

    type: Literal["initiative_resumed"] = "initiative_resumed"
    initiative_id: str
    by: str = "operator"
    reason: str = ""


class InitiativeCancelled(Ev):
    """Operator cancel: the task stops for good. Terminal like `settled` — no
    retry, redirect, reassignment, or settlement reaches it. The live
    attempt's window closes; worktree and evidence stay preserved for
    `discard` and salvage. Downstream initiatives stay pending: cancel never
    releases a dependency."""

    type: Literal["initiative_cancelled"] = "initiative_cancelled"
    initiative_id: str
    by: str = "operator"
    reason: str = ""


class TaskRedirected(Ev):
    """Operator redirect: a task continues on a new brief version.

    Exactly one of `brief` and `checkpoint_id` is set: a replacement brief, or
    an existing checkpoint version in the plan the next attempt continues
    from (its deterministic brief is derived in the fold).
    """

    type: Literal["task_redirected"] = "task_redirected"
    initiative_id: str
    brief: str = ""
    checkpoint_id: str | None = None
    by: str = "operator"
    reason: str = ""


class TaskReassigned(Ev):
    """Operator reassignment of a task's harness/model, keeping attempt history."""

    type: Literal["task_reassigned"] = "task_reassigned"
    initiative_id: str
    assignment: Assignment
    by: str = "operator"
    reason: str = ""


class TaskNudged(Ev):
    """Free text steered at a task's live attempt.

    A nudge flagged `ground_truth` also becomes a run-scoped memory leaf, so
    retry and newly compiled packets carry the correction — not just the live
    pane message.
    """

    type: Literal["task_nudged"] = "task_nudged"
    initiative_id: str
    attempt_id: str
    text: str
    by: str = "operator"
    ground_truth: bool = False


class OperatorAnswered(Ev):
    """Operator answer to an agent block/decision request, zero ceremony.

    One event records both the audit trail and the run-scoped leaf the
    daemon's auto-answer matcher keys on by subject.
    """

    type: Literal["operator_answered"] = "operator_answered"
    attempt_id: str
    subject: str
    answer: str
    by: str = "operator"


class ProcessRestarted(Ev):
    """Operator restarted the executor process in a live attempt's pane.

    Audit-only: the same attempt, packet, and worktree continue, so this is
    not a retry. The event exists only after the restart was delivered, so it
    records the outcome by its presence.
    """

    type: Literal["process_restarted"] = "process_restarted"
    attempt_id: str
    by: str = "operator"


Event = Annotated[
    PlanCreated
    | PlanProposed
    | PlanApproved
    | AttemptStarted
    | AttemptProvisioned
    | SubtaskAdvanced
    | RuntimeObserved
    | CheckpointRecorded
    | CheckpointApproved
    | CheckpointRejected
    | CheckpointChangesRequested
    | PolicyDecisionRecorded
    | InitiativeSettled
    | InitiativeFailed
    | InitiativePaused
    | InitiativeResumed
    | InitiativeCancelled
    | TaskRedirected
    | TaskReassigned
    | TaskNudged
    | OperatorAnswered
    | ProcessRestarted,
    Field(discriminator="type"),
]


# --- projections: rebuilt by the fold, never persisted ------------------------


class Subtask(Model):
    id: str
    brief: str
    state: Literal["todo", "doing", "done", "skipped"] = "todo"


class Attempt(Model):
    """One run of an initiative. A retry appends a new attempt."""

    id: str
    initiative_id: str
    assignment: Assignment
    """Recorded per attempt so reassignment preserves history."""
    brief_version: int = 1
    """Which brief version this attempt ran on; 1 is the planner-authored brief.
    Snapshotted per attempt so a redirect preserves history."""
    worktree_ref: str | None = None
    pane_ref: str | None = None
    base_sha: str | None = None
    """The diff base persisted at provisioning, if known; recovery collects
    against it instead of a value lost with the daemon's memory."""
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    checkpoint: Checkpoint | None = None
    packet_tokens: int = 0
    by: str = "daemon"
    """Who reserved the attempt; see `AttemptStarted.by`."""
    origin: Literal["run", "retry"] = "run"
    """Whether this attempt was an ordinary run or a retry."""
    unattended: bool = False
    """Whether recovery must reapply unattended policy to this attempt."""


class Initiative(Model):
    """A single parallel unit of work. One worktree, one implementer."""

    spec: InitiativeSpec
    subtasks: list[Subtask] = []
    attempts: list[Attempt] = []
    state: Literal[
        "pending", "running", "settled", "failed", "paused", "cancelled"
    ] = "pending"
    failures: list[InitiativeFailure] = []
    """One entry per recorded failure, in event order: the reason as recorded
    and the preserved diagnostic evidence paths. Bounded by the attempt
    ceiling."""
    checkpoint_versions: list[Checkpoint] = []
    """Every recorded checkpoint version, in record order.

    A revision after rejection or requested changes appends here; nothing is
    removed, so rejected evidence stays auditable and stays addressable.
    """
    checkpoint_decisions: dict[str, CheckpointDecision] = {}
    """Review state per checkpoint id, derived from review events and policy."""
    brief_versions: list[TaskBriefVersion] = []
    """Operator redirects, in order. Version 1 is `spec.brief` and is not stored."""
    assignment_override: Assignment | None = None
    """The operator's harness/model override; None keeps the planner's choice.
    Applies to the next attempt only — running attempts keep their snapshot."""

    @property
    def current_brief(self) -> str:
        """The brief new attempts run on: latest redirect, else the planner's."""
        return self.brief_versions[-1].brief if self.brief_versions else self.spec.brief

    @property
    def current_assignment(self) -> Assignment:
        """The assignment new attempts run on: the override, else the planner's."""
        return (
            self.assignment_override
            if self.assignment_override is not None
            else self.spec.assignment
        )

    @property
    def latest_checkpoint(self) -> Checkpoint | None:
        """The current version: what review, handoff, and readiness read."""
        return self.checkpoint_versions[-1] if self.checkpoint_versions else None


class InitiativeFailure(Model):
    """One recorded failure of an initiative: its reason and preserved evidence.

    Projected from `InitiativeFailed` so salvage reads the fold like every
    other reader. Bounded by the attempt ceiling — at most one failure per
    attempt.
    """

    reason: str
    evidence: list[str] = []


class Taint(FrozenModel):
    """Deterministic attention: downstream work resting on invalidated evidence.

    Emitted when a checkpoint an attempt actually built on has since been
    rejected or superseded — directly, or through a tainted dependency.
    """

    initiative_id: str
    producer_id: str
    """The direct dependency whose evidence went bad, or the root producer."""
    checkpoint_id: str
    reason: str


class FailureRecord(Model):
    """How many attempts of one initiative failed with one signature.

    Projection-only bookkeeping for repeated-failure stopping: `attempts`
    names every failed attempt that fed the count, so a promoted leaf can
    reference its evidence and the daemon's admission rule can stop a
    mechanically identical retry.
    """

    count: int = 0
    attempts: list[str] = []


class Plan(Model):
    id: str
    version: int = 1
    brief: str
    """The user's original prompt, verbatim, on both the planned and direct paths."""
    planner: Assignment | None = None
    approval: Literal["pending", "approved"] = "pending"
    initiatives: dict[str, Initiative] = {}
    created_at: AwareDatetime
    planner_usage: Usage | None = None
    """Planning is productive work, so it belongs in the overhead denominator."""
    memory_leaves: list[MemoryLeaf] = []
    """Run-scoped ground-truth leaves, projected from intervention events."""
    live_until: dict[str, AwareDatetime] = {}
    """Per attempt: when it stopped being the live attempt of a running task.

    A pane delivery is recorded after the pane write, so one that began
    against the validated live attempt can race the settlement or failure
    landing during the write. Its event's `at` is the delivery's initiation
    time, so the record folds against a no-longer-live attempt only when that
    time falls inside `[attempt.started_at, live_until)`; an initiation after
    the window closed is a retroactive intervention and stays refused.
    """
    policy_decisions: list[PolicyDecisionRecorded] = []
    """Automatic decisions, folded from `PolicyDecisionRecorded` events."""
    action_ids: dict[str, str] = {}
    """Folded idempotency index: action_id -> "<event type>:<request
    fingerprint>" for the event that recorded it.

    The fold refuses a second event with a known `action_id` (the same
    apply-before-append gate as the contract checks), so a repeated recovery
    request can never double-apply — the daemon returns the original outcome
    instead of appending. The fingerprint binds the key to the request it
    recorded, computed from the event itself, so replay rebuilds the index
    identically with no side table."""
    failure_signatures: dict[tuple[str, str, str], FailureRecord] = {}
    """Per (initiative_id, check name, normalized error): the repeated-failure
    stopping data. Folded, never cached — the counts decide mechanical leaf
    promotion and survive a restart identically."""

    def ready(self) -> list[str]:
        """Ids of pending initiatives whose dependencies have all settled.

        Readiness is computed, not stored: a stored `blocked` state would be a
        second source of truth that drifts after a retry.

        A settled dependency releases its consumers only while its latest
        checkpoint stands approved. A required approval that is still pending
        keeps the producer un-settled, so it blocks through the state check;
        a later rejection or change request on a released producer blocks here
        until a revised version is approved.
        """
        return [
            i.spec.id
            for i in self.initiatives.values()
            if i.state == "pending" and self.dependencies_released(i)
        ]

    def dependencies_released(self, initiative: Initiative) -> bool:
        """Whether every dependency of one initiative currently releases it.

        `ready` applies this to pending initiatives; retry applies it to a
        failed one, so a node cannot start again on evidence that is no longer
        approved.
        """
        return all(
            d in self.initiatives
            and self.initiatives[d].state == "settled"
            and self._releases_consumers(self.initiatives[d])
            for d in initiative.spec.depends_on
        )

    def _releases_consumers(self, initiative: Initiative) -> bool:
        latest = initiative.latest_checkpoint
        if latest is None:
            return False
        decision = initiative.checkpoint_decisions.get(latest.id)
        return decision is not None and decision.state == "approved"

    def attention(self) -> list[Taint]:
        """Consumers whose recorded work rests on invalidated evidence.

        A producer invalidates its consumers when the checkpoint they built on
        — the latest approval at or before the consumer's last attempt started
        — is now rejected or superseded. Taint then propagates along depen-
        dency edges, so a consumer of a tainted consumer is tainted too. The
        taint clears deterministically: once the producer has a later approved
        version, only consumers whose last attempt started after that approval
        remain clean; everyone earlier stays listed until they re-run on the
        recovered evidence.
        """
        graph: nx.DiGraph[str] = nx.DiGraph()
        graph.add_nodes_from(self.initiatives)
        for initiative in self.initiatives.values():
            for dependency in initiative.spec.depends_on:
                if dependency in self.initiatives:
                    _ = graph.add_edge(dependency, initiative.spec.id)
        tainted: dict[str, list[Taint]] = {}
        for initiative_id in nx.topological_sort(graph):
            initiative = self.initiatives[initiative_id]
            items: list[Taint] = []
            if initiative.attempts:
                started = initiative.attempts[-1].started_at
                for dependency_id in initiative.spec.depends_on:
                    dependency = self.initiatives.get(dependency_id)
                    if dependency is None:
                        continue
                    invalidated = self._invalidated_since(dependency, started)
                    if invalidated is not None:
                        checkpoint, why = invalidated
                        items.append(
                            Taint(
                                initiative_id=initiative_id,
                                producer_id=dependency_id,
                                checkpoint_id=checkpoint.id,
                                reason=(
                                    f"checkpoint {checkpoint.id} of {dependency_id}, "
                                    + f"which this attempt built on, was {why}"
                                ),
                            )
                        )
                    for inherited in tainted.get(dependency_id, []):
                        items.append(
                            Taint(
                                initiative_id=initiative_id,
                                producer_id=inherited.producer_id,
                                checkpoint_id=inherited.checkpoint_id,
                                reason=(
                                    f"depends on tainted {dependency_id}: "
                                    + inherited.reason
                                ),
                            )
                        )
            tainted[initiative_id] = items
        return [
            item for initiative_id in tainted for item in tainted[initiative_id]
        ]

    def _invalidated_since(
        self, producer: Initiative, at: AwareDatetime
    ) -> "tuple[Checkpoint, str] | None":
        """The producer checkpoint `at` built on, if it no longer stands.

        The evidence an attempt consumed is the producer's latest approval at
        or before the attempt started. Superseded means a newer version of the
        same checkpoint exists; rejected means a reviewer refused it.
        """
        effective: tuple[Checkpoint, CheckpointDecision] | None = None
        for version in producer.checkpoint_versions:
            decision = producer.checkpoint_decisions.get(version.id)
            if (
                decision is not None
                and decision.approved_at is not None
                and decision.approved_at <= at
            ):
                effective = (version, decision)
        if effective is None:
            return None
        checkpoint, decision = effective
        superseded = checkpoint.id != producer.checkpoint_versions[-1].id
        if decision.state == "rejected":
            return checkpoint, "rejected"
        if superseded:
            return checkpoint, "superseded"
        return None

    @classmethod
    def step(cls, plan: "Plan | None", ev: Event) -> "Plan":
        """Apply one event, creating the plan when it is `plan_created`.

        The event store applies before it appends, so an event that cannot be
        folded is never written.
        """
        if isinstance(ev, PlanCreated):
            if plan is not None:
                raise ValueError("duplicate plan_created event")
            return cls(
                id=ev.plan_id,
                brief=ev.brief,
                planner=ev.planner,
                created_at=ev.at,
            )
        if plan is None:
            raise ValueError(f"{ev.type} arrived before plan_created")
        if ev.plan_id != plan.id:
            raise ValueError(
                f"{ev.type} belongs to plan {ev.plan_id}, expected {plan.id}"
            )
        plan._apply(ev)
        return plan

    @classmethod
    def fold(cls, events: Sequence[Event]) -> "Plan":
        """Rebuild plan state from its event stream, in order."""
        plan = reduce(cls.step, events, cast("Plan | None", None))
        if plan is None:
            raise ValueError("empty event stream")
        return plan

    def _apply(self, ev: Event) -> None:
        if ev.action_id is not None:
            if ev.action_id in self.action_ids:
                raise ValueError(
                    f"action request {ev.action_id} was already recorded as "
                    + f"{self.action_ids[ev.action_id]}"
                )
            self.action_ids[ev.action_id] = f"{ev.type}:{action_fingerprint(ev)}"
        match ev:
            case PlanProposed():
                if ev.version <= 0:
                    raise ValueError("plan proposal version must be positive")
                if ev.version < self.version:
                    raise ValueError("plan proposal version must not go backwards")
                if ev.version == self.version and self.initiatives:
                    raise ValueError("plan proposal version must advance")
                self.version = ev.version
                self.approval = "pending"
                if ev.usage is not None:
                    self.planner_usage = ev.usage
                current = self.initiatives
                self.initiatives = {}
                for spec in ev.initiatives:
                    existing = current.get(spec.id)
                    if existing is None:
                        self.initiatives[spec.id] = Initiative(
                            spec=spec, subtasks=_subtasks(spec)
                        )
                    else:
                        # Surviving initiatives keep their runtime state; only
                        # planner-authored content is replaced.
                        # ponytail: subtasks and redirect history are left
                        # alone on re-propose. A recalibration that edits them
                        # needs a merge rule — Sprint 7.
                        existing.spec = spec
                        self.initiatives[spec.id] = existing
            case PlanApproved():
                if not self.initiatives:
                    raise ValueError("plan has no proposed initiatives")
                if ev.version != self.version:
                    raise ValueError(
                        f"cannot approve plan version {ev.version}; "
                        + f"current version is {self.version}"
                    )
                if self.approval == "approved":
                    raise ValueError("plan is already approved")
                self.approval = "approved"
            case AttemptStarted():
                if self.approval != "approved":
                    raise ValueError("plan must be approved before starting an attempt")
                initiative = self._initiative(ev.initiative_id)
                # `failed` is retryable on purpose: a retry is a new attempt on
                # the task's current brief version and assignment. A running
                # initiative still refuses a second attempt — the loser of a
                # concurrent `run` race is turned away before launching a
                # duplicate agent.
                if initiative.state not in {"pending", "failed"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a pending or failed initiative can start an attempt"
                    )
                if initiative.state == "failed" and ev.origin == "run":
                    raise ValueError(
                        f"initiative {ev.initiative_id} is failed; a new attempt "
                        + "on failed work is a retry: start it with origin='retry'"
                    )
                if len(initiative.attempts) >= initiative.spec.policy.max_attempts:
                    raise ValueError(
                        f"initiative {ev.initiative_id} has reached the attempt "
                        + f"ceiling of {initiative.spec.policy.max_attempts}; no further attempt can start"
                    )
                if ev.assignment != initiative.current_assignment:
                    raise ValueError(
                        f"attempt assignment {ev.assignment.harness}/"
                        + f"{ev.assignment.model} does not match the task's "
                        + "current assignment; reassign first"
                    )
                current_version = len(initiative.brief_versions) + 1
                if ev.brief_version != current_version:
                    raise ValueError(
                        f"attempt must start on the current brief version "
                        + f"{current_version}, not {ev.brief_version}"
                    )
                if any(
                    attempt.id == ev.attempt_id
                    for existing in self.initiatives.values()
                    for attempt in existing.attempts
                ):
                    raise ValueError(f"duplicate attempt {ev.attempt_id}")
                initiative.attempts.append(
                    Attempt(
                        id=ev.attempt_id,
                        initiative_id=ev.initiative_id,
                        assignment=ev.assignment,
                        brief_version=ev.brief_version,
                        worktree_ref=ev.worktree_ref,
                        pane_ref=ev.pane_ref,
                        started_at=ev.at,
                        packet_tokens=ev.packet_tokens,
                        by=ev.by,
                        origin=ev.origin,
                        unattended=ev.unattended,
                    )
                )
                initiative.state = "running"
            case AttemptProvisioned():
                attempt = self._attempt(ev.attempt_id)
                attempt.worktree_ref = ev.worktree_ref
                if ev.pane_ref is not None:
                    attempt.pane_ref = ev.pane_ref
                if ev.base_sha is not None:
                    attempt.base_sha = ev.base_sha
            case SubtaskAdvanced():
                initiative = self._initiative(ev.initiative_id)
                for sub in initiative.subtasks:
                    if sub.id == ev.subtask_id:
                        sub.state = ev.state
                        break
                else:
                    raise ValueError(f"unknown subtask {ev.subtask_id}")
            case CheckpointRecorded():
                owner, attempt = self._attempt_owner(ev.checkpoint.attempt_id)
                if any(
                    ev.checkpoint.id in initiative.checkpoint_decisions
                    for initiative in self.initiatives.values()
                ):
                    raise ValueError(f"duplicate checkpoint {ev.checkpoint.id}")
                if attempt.checkpoint is not None:
                    prior = owner.checkpoint_decisions.get(
                        attempt.checkpoint.id, CheckpointDecision()
                    )
                    if prior.state not in {"rejected", "changes_requested"}:
                        raise ValueError(
                            f"attempt {attempt.id} already holds checkpoint "
                            + f"{attempt.checkpoint.id} ({prior.state}); a revision "
                            + "can be recorded only after rejection or requested changes"
                        )
                    # A revision replaces the attempt's current evidence; the
                    # superseded version stays in `checkpoint_versions`.
                attempt.checkpoint = ev.checkpoint
                if attempt.ended_at is None:
                    attempt.ended_at = ev.at
                owner.checkpoint_versions.append(ev.checkpoint)
                owner.checkpoint_decisions[ev.checkpoint.id] = CheckpointDecision()
            case CheckpointApproved():
                initiative, checkpoint = self._checkpoint_owner(ev.checkpoint_id)
                decision = initiative.checkpoint_decisions.get(
                    checkpoint.id, CheckpointDecision()
                )
                if decision.state == "approved":
                    raise ValueError(f"checkpoint {checkpoint.id} is already approved")
                if decision.state == "rejected":
                    raise ValueError(
                        f"checkpoint {checkpoint.id} was rejected; record a "
                        + "revised checkpoint instead of approving it"
                    )
                self._decide(
                    initiative,
                    checkpoint.id,
                    state="approved",
                    decided_at=ev.at,
                    decided_by=ev.by,
                    reason=ev.reason,
                    approved_at=ev.at,
                )
            case CheckpointRejected():
                initiative, checkpoint = self._checkpoint_owner(ev.checkpoint_id)
                decision = initiative.checkpoint_decisions.get(
                    checkpoint.id, CheckpointDecision()
                )
                if decision.state == "rejected":
                    raise ValueError(f"checkpoint {checkpoint.id} is already rejected")
                self._decide(
                    initiative,
                    checkpoint.id,
                    state="rejected",
                    decided_at=ev.at,
                    decided_by=ev.by,
                    reason=ev.reason,
                )
                if initiative.state == "running":
                    # The run that produced the evidence is over; rejection is
                    # what stops it pretending to await review.
                    self._close_live_attempt(initiative, ev.at)
                    initiative.state = "failed"
            case CheckpointChangesRequested():
                initiative, checkpoint = self._checkpoint_owner(ev.checkpoint_id)
                decision = initiative.checkpoint_decisions.get(
                    checkpoint.id, CheckpointDecision()
                )
                if decision.state != "pending":
                    raise ValueError(
                        f"checkpoint {checkpoint.id} is {decision.state}; "
                        + "changes can be requested only while review is pending"
                    )
                self._decide(
                    initiative,
                    checkpoint.id,
                    state="changes_requested",
                    decided_at=ev.at,
                    decided_by=ev.by,
                    reason=ev.reason,
                )
                if initiative.state == "running":
                    self._close_live_attempt(initiative, ev.at)
                    initiative.state = "failed"
            case PolicyDecisionRecorded():
                initiative = self._initiative(ev.initiative_id)
                if ev.attempt_id is not None and not any(
                    attempt.id == ev.attempt_id for attempt in initiative.attempts
                ):
                    raise ValueError(
                        f"policy decision names unknown attempt {ev.attempt_id}"
                    )
                if ev.checkpoint_id is not None and not any(
                    version.id == ev.checkpoint_id
                    for version in initiative.checkpoint_versions
                ):
                    raise ValueError(
                        f"policy decision names unknown checkpoint {ev.checkpoint_id}"
                    )
                unknown_rules = set(ev.rule_ids) - set(POLICY_RULE_IDS)
                if unknown_rules:
                    raise ValueError(
                        "unknown policy rule id(s): " + ", ".join(sorted(unknown_rules))
                    )
                self.policy_decisions.append(ev)
            case InitiativeSettled():
                initiative = self._initiative(ev.initiative_id)
                # `failed` is settleable on purpose: dirty evidence is retained,
                # and the operator overriding it is the documented escape hatch.
                # Settling is what releases the dependents, so it must stay
                # available after the automatic policy refused to advance.
                if initiative.state not in {"running", "failed", "paused"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a running, failed, or paused initiative can be settled"
                    )
                if not any(
                    attempt.checkpoint is not None
                    and attempt.checkpoint.id == ev.checkpoint_id
                    for attempt in initiative.attempts
                ):
                    raise ValueError(
                        f"checkpoint {ev.checkpoint_id} does not belong to "
                        + f"initiative {ev.initiative_id}"
                    )
                checkpoint = next(
                    attempt.checkpoint
                    for attempt in initiative.attempts
                    if attempt.checkpoint is not None
                    and attempt.checkpoint.id == ev.checkpoint_id
                )
                decision = initiative.checkpoint_decisions.get(
                    checkpoint.id, CheckpointDecision()
                )
                if decision.state == "rejected":
                    raise ValueError(
                        f"checkpoint {checkpoint.id} was rejected; record a "
                        + "revised checkpoint before settling"
                    )
                if initiative.spec.contract is not None:
                    # The authoritative contract gate: an event that would
                    # accept violating evidence is refused here, so neither a
                    # direct store append nor a replay can bypass the task's
                    # required checks, command policy, or write scope.
                    violations = validate_checkpoint(
                        initiative.spec, checkpoint, initiative.spec.contract
                    )
                    if violations:
                        raise ContractError(
                            summarize_violations(violations), violations=violations
                        )
                if initiative.spec.approval == "required":
                    if decision.state != "approved":
                        raise ValueError(
                            f"checkpoint {checkpoint.id} requires approval before "
                            + f"{ev.initiative_id} can settle"
                        )
                elif decision.state in {"pending", "changes_requested"}:
                    # The automatic policy is the documented clean-evidence
                    # behavior: settlement itself is the approval, so released
                    # consumers read an approved checkpoint either way.
                    self._decide(
                        initiative,
                        checkpoint.id,
                        state="approved",
                        decided_at=ev.at,
                        decided_by="policy",
                        approved_at=ev.at,
                    )
                # A paused task's live attempt still settles under the same
                # policy; the window closes whenever it is still open.
                self._close_live_attempt(initiative, ev.at)
                initiative.state = "settled"
            case InitiativeFailed():
                initiative = self._initiative(ev.initiative_id)
                self._close_live_attempt(initiative, ev.at)
                initiative.state = "failed"
                initiative.failures.append(
                    InitiativeFailure(reason=ev.reason, evidence=list(ev.evidence))
                )
                self._record_failure_signatures(initiative, ev.reason, ev.at)
            case InitiativePaused():
                initiative = self._initiative(ev.initiative_id)
                if initiative.state not in {"pending", "failed", "running"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a pending, failed, or running initiative can be paused"
                    )
                initiative.state = "paused"
            case InitiativeResumed():
                initiative = self._initiative(ev.initiative_id)
                if initiative.state != "paused":
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a paused initiative can be resumed"
                    )
                initiative.state = "failed" if initiative.attempts else "pending"
            case InitiativeCancelled():
                initiative = self._initiative(ev.initiative_id)
                if initiative.state in {"settled", "cancelled"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "a settled or cancelled task cannot be cancelled"
                    )
                self._close_live_attempt(initiative, ev.at)
                initiative.state = "cancelled"
            case RuntimeObserved():
                pass  # streamed and audited, but carries no projected state
            case TaskRedirected():
                initiative = self._initiative(ev.initiative_id)
                if initiative.state in {"settled", "cancelled"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only an active or retryable task can be redirected"
                    )
                brief = ev.brief
                if ev.checkpoint_id is not None:
                    if ev.brief.strip():
                        raise ValueError(
                            "a redirect takes a brief or a checkpoint, not both"
                        )
                    checkpoint = next(
                        (
                            version
                            for candidate in self.initiatives.values()
                            for version in candidate.checkpoint_versions
                            if version.id == ev.checkpoint_id
                        ),
                        None,
                    )
                    if checkpoint is None:
                        raise ValueError(f"unknown checkpoint {ev.checkpoint_id}")
                    brief = _checkpoint_brief(checkpoint)
                elif not ev.brief.strip():
                    raise ValueError("redirect brief cannot be empty")
                version = len(initiative.brief_versions) + 2
                initiative.brief_versions.append(
                    TaskBriefVersion(
                        version=version,
                        brief=brief,
                        by=ev.by,
                        at=ev.at,
                        reason=ev.reason,
                    )
                )
                # The redirect is ground truth by definition: the brief changed.
                # The leaf claim is the brief itself, so newly compiled packets
                # carry the actual correction; `reason` stays in the version.
                self._leaf(
                    subject=f"{ev.initiative_id}.brief",
                    claim=brief,
                    origin="redirect",
                    by=ev.by,
                    at=ev.at,
                )
            case TaskReassigned():
                initiative = self._initiative(ev.initiative_id)
                if initiative.state in {"settled", "cancelled"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only an active or retryable task can be reassigned"
                    )
                if ev.assignment == initiative.current_assignment:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is already assigned to "
                        + f"{ev.assignment.harness}/{ev.assignment.model}"
                    )
                # Applies to the next attempt; the running attempt keeps its
                # snapshot, so reassignment never disturbs live or past work.
                initiative.assignment_override = ev.assignment
            case TaskNudged():
                if not ev.text.strip():
                    raise ValueError("nudge text cannot be empty")
                initiative = self._initiative(ev.initiative_id)
                # A delivery is recorded after the pane write, so one begun
                # against the validated live attempt can race the settlement
                # or failure landing during the write; its initiation time
                # (the event's `at`) still admits it. An initiation after the
                # attempt's live window is retroactive and stays refused.
                while_live = self._delivered_while_live(
                    next(
                        (a for a in initiative.attempts if a.id == ev.attempt_id),
                        None,
                    ),
                    ev.at,
                )
                if initiative.state != "running" and not while_live:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a running task can be nudged"
                    )
                if not initiative.attempts or initiative.attempts[-1].id != ev.attempt_id:
                    if not while_live:
                        raise ValueError(
                            f"attempt {ev.attempt_id} is not the live attempt of "
                            + f"{ev.initiative_id}"
                        )
                if ev.ground_truth:
                    self._leaf(
                        subject=f"{ev.initiative_id}.nudge",
                        claim=ev.text,
                        origin="nudge",
                        by=ev.by,
                        at=ev.at,
                    )
            case OperatorAnswered():
                if not ev.subject.strip() or not ev.answer.strip():
                    raise ValueError("operator answer needs a subject and an answer")
                initiative, _attempt = self._attempt_owner(ev.attempt_id)
                # Same delivery race as `TaskNudged` above.
                while_live = self._delivered_while_live(_attempt, ev.at)
                if initiative.state != "running" and not while_live:
                    raise ValueError(
                        f"initiative {initiative.spec.id} is {initiative.state}; "
                        + "answers are recorded for live requests only"
                    )
                if initiative.attempts[-1].id != ev.attempt_id and not while_live:
                    raise ValueError(
                        f"attempt {ev.attempt_id} is not the live attempt of "
                        + f"{initiative.spec.id}"
                    )
                self._leaf(
                    subject=ev.subject,
                    claim=ev.answer,
                    origin="operator-answer",
                    by=ev.by,
                    at=ev.at,
                )
            case ProcessRestarted():
                initiative, _attempt = self._attempt_owner(ev.attempt_id)
                # Same delivery race as `TaskNudged` above.
                while_live = self._delivered_while_live(_attempt, ev.at)
                if initiative.state != "running" and not while_live:
                    raise ValueError(
                        f"initiative {initiative.spec.id} is {initiative.state}; "
                        + "a process restart targets a live attempt only"
                    )
                if initiative.attempts[-1].id != ev.attempt_id and not while_live:
                    raise ValueError(
                        f"attempt {ev.attempt_id} is not the live attempt of "
                        + f"{initiative.spec.id}"
                    )
                # Audit-only fold: the same attempt keeps running; the event
                # itself is the record.
            case PlanCreated():
                raise ValueError("duplicate plan_created event")

    def _decide(
        self, initiative: Initiative, checkpoint_id: str, **update: object
    ) -> None:
        """Record one review verdict, preserving earlier fields (approval time)."""
        current = initiative.checkpoint_decisions.get(
            checkpoint_id, CheckpointDecision()
        )
        initiative.checkpoint_decisions[checkpoint_id] = current.model_copy(
            update=update
        )

    def _close_live_attempt(self, initiative: Initiative, at: AwareDatetime) -> None:
        """Close the live-attempt window when the task stops running.

        Guarded on an open window so a task that already stopped — a failure
        after a pause, a cancel of an already-failed task — cannot extend a
        closed window and reopen the delivery race.
        """
        if initiative.attempts and initiative.attempts[-1].id not in self.live_until:
            self.live_until[initiative.attempts[-1].id] = at

    def _record_failure_signatures(
        self, initiative: Initiative, reason: str, at: AwareDatetime
    ) -> None:
        """Fold one failed attempt into the repeated-failure signature counts.

        A signature is (check name, normalized error). Failed checks come from
        the attempt's recorded checkpoint; when no check applies — a crash, a
        missing pane, no checkpoint — the failure reason stands in under the
        name `error`. At the repeat limit the fold promotes exactly one
        mechanical memory leaf, so the next retry packet carries the failure
        without ever carrying the attempt's transcript. The daemon's fail()
        dedup keeps one failure event per attempt; the counts mirror events.
        """
        attempt = initiative.attempts[-1] if initiative.attempts else None
        checkpoint = attempt.checkpoint if attempt is not None else None
        failed = (
            [
                (check.name, check.summary)
                for check in checkpoint.checks
                if not check.passed
            ]
            if checkpoint is not None
            else []
        ) or [("error", reason)]
        for name, error in failed:
            key = (initiative.spec.id, name, normalize_error(error))
            record = self.failure_signatures.get(key, FailureRecord())
            record.count += 1
            if attempt is not None:
                record.attempts.append(attempt.id)
            self.failure_signatures[key] = record
            if record.count == REPEATED_FAILURE_LIMIT:
                refs = ", ".join(record.attempts)
                what = (
                    f"check {name!r} failed in attempts {refs}"
                    if name != "error"
                    else f"failure repeated in attempts {refs}"
                )
                self._leaf(
                    subject=f"{initiative.spec.id}.failure",
                    claim=f"{what}: {key[2]}",
                    origin="failure",
                    by="policy",
                    at=at,
                )

    def _delivered_while_live(self, attempt: Attempt | None, at: AwareDatetime) -> bool:
        """Whether a pane delivery initiated at `at` began while `attempt` was live."""
        if attempt is None:
            return False
        ended = self.live_until.get(attempt.id)
        # An open window is still live: a paused task's attempt keeps running,
        # so a delivery initiated against it stays attributable.
        return attempt.started_at <= at and (ended is None or at < ended)

    def _leaf(
        self, *, subject: str, claim: str, origin: LeafOrigin, by: str, at: AwareDatetime
    ) -> None:
        """Project one run-scoped leaf; the id is the fold order, so replay is stable."""
        self.memory_leaves.append(
            MemoryLeaf(
                id=f"leaf_{len(self.memory_leaves) + 1}",
                subject=subject,
                claim=claim,
                origin=origin,
                by=by,
                at=at,
            )
        )

    def _initiative(self, initiative_id: str) -> Initiative:
        try:
            return self.initiatives[initiative_id]
        except KeyError:
            raise ValueError(f"unknown initiative {initiative_id}") from None

    def _attempt(self, attempt_id: str) -> Attempt:
        return self._attempt_owner(attempt_id)[1]

    def _attempt_owner(self, attempt_id: str) -> "tuple[Initiative, Attempt]":
        for initiative in self.initiatives.values():
            for attempt in initiative.attempts:
                if attempt.id == attempt_id:
                    return initiative, attempt
        raise ValueError(f"unknown attempt {attempt_id}")

    def _checkpoint_owner(
        self, checkpoint_id: str
    ) -> "tuple[Initiative, Checkpoint]":
        """The initiative and version a review event names."""
        for initiative in self.initiatives.values():
            for version in initiative.checkpoint_versions:
                if version.id == checkpoint_id:
                    return initiative, version
        raise ValueError(f"unknown checkpoint {checkpoint_id}")


def action_fingerprint(ev: Event) -> str:
    """A stable digest of one action request's semantic identity.

    Covers what the request is — the action type, its target, and its
    structural payload (a checkpoint id, a brief version) — and nothing
    that is not: the record's timing (`at`, `seq`), the key itself, and the
    attribution prose (`by`, `reason`) whose omission with defaults marks a
    repeat of the same request, plus the attempt id and packet estimate a
    run generates per call. Computed from the event, so replay reproduces
    the idempotency index without a side table; a repeat of the same request
    hashes identically, a reused key over a different action, target, or
    structural payload does not. The recorded event keeps the original
    actor and reason either way.
    """
    exclude = {"at", "seq", "action_id", "by", "reason"}
    if isinstance(ev, AttemptStarted):
        exclude |= {"attempt_id", "packet_tokens"}
    payload = ev.model_dump(mode="json", exclude=exclude)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def normalize_error(text: str) -> str:
    """Collapse one error text into a comparable failure signature.

    Deterministic and pure: casefold, whitespace collapse, digit runs to `#`,
    truncated to a bounded key — the same failure normalizes identically
    across attempts, and the run-scoped signature counts cannot grow
    unbounded. A loose merge costs one run, not memory.
    """
    return re.sub(r"\d+", "#", " ".join(text.casefold().split()))[:200]


def _checkpoint_brief(checkpoint: Checkpoint) -> str:
    """The deterministic brief a checkpoint-targeted redirect compiles.

    The next attempt continues from the referenced checkpoint's recorded
    work; the reference, its scope, and its written caveats are the whole
    brief, so replay and packets carry the same text.
    """
    brief = (
        f"Continue from checkpoint {checkpoint.id} (attempt "
        + f"{checkpoint.attempt_id}, {len(checkpoint.changed_paths)} changed "
        + "path(s))."
    )
    if checkpoint.caveats:
        brief += " Caveats: " + "; ".join(checkpoint.caveats)
    return brief


def _subtasks(spec: InitiativeSpec) -> list[Subtask]:
    return [
        Subtask(id=f"{spec.id}.{n}", brief=brief)
        for n, brief in enumerate(spec.subtasks, start=1)
    ]
