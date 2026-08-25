"""Canonical domain models.

Pydantic is the single definition of the domain; JSON Schema is generated from
these models and never hand-maintained. Third-party types (herdr, NetworkX,
Git) never appear here — foreign things cross this boundary as opaque strings.

Only events are persisted. Everything below `Event` is a projection rebuilt by
`Plan.fold`.

Vocabulary: an *initiative* is a single parallel unit of work — one worktree,
one implementer, one brief. A *plan* holds many of them.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- value objects -----------------------------------------------------------


class Assignment(Model):
    """Which agent CLI, on which model. Used for planner and implementer alike."""

    harness: str
    model: str


class Routes(Model):
    """Contention routes. Overlapping writes are a conflict; shared reads are not."""

    reads: list[str] = []
    writes: list[str] = []


class Usage(Model):
    """Token facts. Counts from different sources are never summed."""

    input_tokens: int = 0
    output_tokens: int = 0
    source: Literal["harness", "provider", "estimate"]


class CheckResult(Model):
    name: str
    passed: bool
    summary: str = ""


class Checkpoint(Model):
    """Evidence manifest, mechanically populated. Not model-authored prose."""

    id: str
    attempt_id: str
    changed_paths: list[str] = []
    base_sha: str | None = None
    head_sha: str | None = None
    checks: list[CheckResult] = []
    exit_code: int | None = None
    usage: Usage | None = None
    caveats: list[str] = []
    """Only non-recoverable decisions, caveats, or blockers written by the executor."""


class InitiativeSpec(Model):
    """Planner-authored content. Immutable; travels inside `PlanProposed`."""

    id: str
    name: str
    brief: str
    assignment: Assignment
    routes: Routes = Routes()
    subtasks: list[str] = []
    """Briefs. Ids are derived positionally as `{spec.id}.{n}`, n from 1."""
    depends_on: list[str] = []


# --- events: the only thing on disk ------------------------------------------


class Ev(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)

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


class AttemptStarted(Ev):
    type: Literal["attempt_started"] = "attempt_started"
    attempt_id: str
    initiative_id: str
    assignment: Assignment
    worktree_ref: str | None = None
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
    detail: dict = {}
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
    | AttemptStarted
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
    initiatives: dict[str, Initiative] = {}
    created_at: AwareDatetime

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
                self.initiatives[d].state == "settled"
                for d in i.spec.depends_on
                if d in self.initiatives
            )
        ]

    @classmethod
    def fold(cls, events: list[Event]) -> "Plan":
        """Rebuild plan state from its event stream, in order."""
        plan: Plan | None = None
        for ev in events:
            if isinstance(ev, PlanCreated):
                plan = cls(
                    id=ev.plan_id,
                    brief=ev.brief,
                    planner=ev.planner,
                    created_at=ev.at,
                )
                continue
            if plan is None:
                raise ValueError(f"{ev.type} arrived before plan_created")
            plan._apply(ev)
        if plan is None:
            raise ValueError("empty event stream")
        return plan

    def _apply(self, ev: Event) -> None:
        match ev:
            case PlanProposed():
                self.version = ev.version
                for spec in ev.initiatives:
                    existing = self.initiatives.get(spec.id)
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
            case AttemptStarted():
                initiative = self._initiative(ev.initiative_id)
                initiative.attempts.append(
                    Attempt(
                        id=ev.attempt_id,
                        initiative_id=ev.initiative_id,
                        assignment=ev.assignment,
                        worktree_ref=ev.worktree_ref,
                        pane_ref=ev.pane_ref,
                        started_at=ev.at,
                    )
                )
                initiative.state = "running"
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
                attempt.checkpoint = ev.checkpoint
                attempt.ended_at = ev.at
            case InitiativeSettled():
                self._initiative(ev.initiative_id).state = "settled"
            case InitiativeFailed():
                self._initiative(ev.initiative_id).state = "failed"
            case RuntimeObserved():
                pass  # streamed and audited, but carries no projected state

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
