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
from collections.abc import Callable, Sequence
from functools import reduce
from typing import Annotated, ClassVar, Literal, Never, Self, TypeVar, cast, overload

import networkx as nx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
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


_GLOB = frozenset("*?[]")


def _validate_route(path: str) -> str:
    """Accept a repository-relative path or directory prefix, and nothing else.

    A route names where an initiative may touch. Anything that could reach
    outside the worktree, or that this codebase would silently mis-compare, is
    rejected at the plan gate rather than quietly weakening serialization.
    Globs are allowed but deliberately over-approximated by `graph._segments`:
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

    @property
    def digest(self) -> str:
        """Content-addressed identity: hash of brief and declared scope.

        Derived, never stored — a stored copy would drift from the content it
        names. Recalibration diffs compare digests to tell a renamed
        initiative from a materially changed one, so the inputs are exactly
        the planner-authored contract: what to do, and where it may touch.
        The contract itself joins this hash in Sprint 3.
        """
        payload = json.dumps(
            {
                "brief": self.brief,
                "reads": sorted(self.routes.reads),
                "writes": sorted(self.routes.writes),
                "subtasks": list(self.subtasks),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --- events: the only thing on disk ------------------------------------------


class Ev(FrozenModel):

    plan_id: str
    at: AwareDatetime
    seq: int = 0
    """Assigned by the event store on append; ignore on construction."""


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
    worktree_ref: str | None = None
    pane_ref: str | None = None
    packet_tokens: int = 0
    """Estimated size of the packet Herdsman injects — orchestration overhead."""


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


class InitiativeSettled(Ev):
    type: Literal["initiative_settled"] = "initiative_settled"
    initiative_id: str
    checkpoint_id: str


class InitiativeFailed(Ev):
    type: Literal["initiative_failed"] = "initiative_failed"
    initiative_id: str
    reason: str


Event = Annotated[
    PlanCreated
    | PlanProposed
    | PlanApproved
    | AttemptStarted
    | AttemptProvisioned
    | SubtaskAdvanced
    | RuntimeObserved
    | CheckpointRecorded
    | InitiativeSettled
    | InitiativeFailed,
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
    worktree_ref: str | None = None
    pane_ref: str | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    checkpoint: Checkpoint | None = None
    packet_tokens: int = 0


class Initiative(Model):
    """A single parallel unit of work. One worktree, one implementer."""

    spec: InitiativeSpec
    subtasks: list[Subtask] = []
    attempts: list[Attempt] = []
    state: Literal["pending", "running", "settled", "failed", "cancelled"] = "pending"


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

    def ready(self) -> list[str]:
        """Ids of pending initiatives whose dependencies have all settled.

        Readiness is computed, not stored: a stored `blocked` state would be a
        second source of truth that drifts after a retry.
        """
        return [
            i.spec.id
            for i in self.initiatives.values()
            if i.state == "pending"
            and all(
                d in self.initiatives and self.initiatives[d].state == "settled"
                for d in i.spec.depends_on
            )
        ]

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
                        # ponytail: subtasks are left alone on re-propose. A
                        # recalibration that edits them needs a merge rule —
                        # Sprint 7.
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
                if initiative.state != "pending":
                    raise ValueError(
                        f"initiative {ev.initiative_id} is not pending"
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
                        worktree_ref=ev.worktree_ref,
                        pane_ref=ev.pane_ref,
                        started_at=ev.at,
                        packet_tokens=ev.packet_tokens,
                    )
                )
                initiative.state = "running"
            case AttemptProvisioned():
                attempt = self._attempt(ev.attempt_id)
                attempt.worktree_ref = ev.worktree_ref
                if ev.pane_ref is not None:
                    attempt.pane_ref = ev.pane_ref
            case SubtaskAdvanced():
                initiative = self._initiative(ev.initiative_id)
                for sub in initiative.subtasks:
                    if sub.id == ev.subtask_id:
                        sub.state = ev.state
                        break
                else:
                    raise ValueError(f"unknown subtask {ev.subtask_id}")
            case CheckpointRecorded():
                attempt = self._attempt(ev.checkpoint.attempt_id)
                if attempt.checkpoint is not None:
                    raise ValueError(f"attempt {attempt.id} already has a checkpoint")
                if any(
                    existing.checkpoint is not None
                    and existing.checkpoint.id == ev.checkpoint.id
                    for initiative in self.initiatives.values()
                    for existing in initiative.attempts
                ):
                    raise ValueError(f"duplicate checkpoint {ev.checkpoint.id}")
                attempt.checkpoint = ev.checkpoint
                attempt.ended_at = ev.at
            case InitiativeSettled():
                initiative = self._initiative(ev.initiative_id)
                # `failed` is settleable on purpose: dirty evidence is retained,
                # and the operator overriding it is the documented escape hatch.
                # Settling is what releases the dependents, so it must stay
                # available after the automatic policy refused to advance.
                if initiative.state not in {"running", "failed"}:
                    raise ValueError(
                        f"initiative {ev.initiative_id} is {initiative.state}; "
                        + "only a running or failed initiative can be settled"
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
                initiative.state = "settled"
            case InitiativeFailed():
                self._initiative(ev.initiative_id).state = "failed"
            case RuntimeObserved():
                pass  # streamed and audited, but carries no projected state
            case PlanCreated():
                raise ValueError("duplicate plan_created event")

    def _initiative(self, initiative_id: str) -> Initiative:
        try:
            return self.initiatives[initiative_id]
        except KeyError:
            raise ValueError(f"unknown initiative {initiative_id}") from None

    def _attempt(self, attempt_id: str) -> Attempt:
        for initiative in self.initiatives.values():
            for attempt in initiative.attempts:
                if attempt.id == attempt_id:
                    return attempt
        raise ValueError(f"unknown attempt {attempt_id}")


def _subtasks(spec: InitiativeSpec) -> list[Subtask]:
    return [
        Subtask(id=f"{spec.id}.{n}", brief=brief)
        for n, brief in enumerate(spec.subtasks, start=1)
    ]
