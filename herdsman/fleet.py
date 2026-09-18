"""Deterministic fleet rollups and attention, for Home and its adapters.

A pure reducer, like `observability`: it reads folded plans and their already
persisted events, and calls nothing. Everything here is derived — no fleet
state is stored anywhere except the archive flag, which is an event like
everything else.

This module is the single place that decides *what needs the user*. The
daemon, the browser adapter, and the herdr adapter all read `attention` and
`notifications` rather than re-deriving the rules; a second classifier would
be a second answer.

Vocabulary follows `classes`: an *initiative* is one parallel unit of work and
a *plan* holds many of them. A *run* is one plan's execution — what the fleet
navigates, and what archiving hides.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode

from pydantic import AwareDatetime

from .classes import (
    CheckpointDecision,
    CheckpointRecorded,
    Event,
    FrozenModel,
    InitiativeFailed,
    Plan,
    PlanProposed,
)
from .observability import activity_projection, burn_down, token_ledger

DEFAULT_STALL_SECONDS = 900.0
"""How long a live attempt may go unobserved before it needs the user.

A wall-clock heuristic, not a state: an agent can legitimately think for a
long time, so this is deliberately generous and deliberately a parameter.
# ponytail: one global threshold; make it per-adapter if harnesses turn out to
# differ enough that one number cannot serve them.
"""

BLOCKED_STATUSES = frozenset({"blocked", "waiting"})
"""Runtime statuses that mean the agent stopped and is waiting on a person.

The same set `observability.vitals` counts, so the fleet and the run vitals
cannot disagree about what "blocked" means.
"""

INITIATIVE_STATES = (
    "pending",
    "running",
    "settled",
    "failed",
    "paused",
    "cancelled",
)
"""Every `Initiative.state`, in a fixed order so count maps are deterministic."""

AttentionKind = Literal[
    "plan_gate",
    "checkpoint_review",
    "blocked_on_user",
    "failed",
    "stalled",
]
"""Every way a run can need the user. Closed set, matched by name."""

RunStatus = Literal[
    "awaiting_approval",
    "running",
    "settled",
    "failed",
    "paused",
    "idle",
    "empty",
]
"""One run's rolled-up state. Derived from its initiatives, never stored."""

_ROUTINE_EVENTS = frozenset(
    {
        "runtime_observed",
        "memory_use_recorded",
        "subtask_advanced",
        "attempt_provisioned",
    }
)
"""Event types the while-away digest drops as routine.

Terminal chatter, token accounting, and within-attempt progress are what the
Run view is for. A returning user wants the things that moved, so anything
that would repeat every few seconds is not a change worth reading.
"""


class ActionTarget(FrozenModel):
    """The one daemon call that resolves an attention item.

    Exactly one per item, by construction: a surface that offers two actions
    for one blocker has not decided what the blocker is.
    """

    method: Literal["POST"] = "POST"
    path: str
    """Daemon API route, already substituted — not a template."""
    label: str


class DeepLink(FrozenModel):
    """The one UI location an attention item or digest entry opens."""

    path: str
    """UI route with its query string, already encoded by `_link`."""


class AttentionItem(FrozenModel):
    """One thing that needs the user, with where to look and what to press."""

    key: str
    """Stable identity across polls, so an adapter can tell a new blocker from
    a still-open one and notify once. Derived from the thing itself — never a
    position or a timestamp."""
    kind: AttentionKind
    plan_id: str
    initiative_id: str | None = None
    attempt_id: str | None = None
    checkpoint_id: str | None = None
    summary: str
    since: AwareDatetime
    """When this became true. The primary ordering key, oldest first."""
    blocking: bool
    """Whether nothing proceeds until the user acts.

    The notification predicate, and the only one: `notifications` filters on
    this field so the adapters share the classification instead of copying it.
    """
    action: ActionTarget
    link: DeepLink


class RunSpend(FrozenModel):
    """What one run has actually cost, and what is left of its declared cap.

    Coarse on purpose. Per-role token attribution is Sprint 6-A's; what is
    true today is the fold's own admission ledger, which is also the number a
    cap is measured against, so the fleet and the run view cannot disagree
    about what a run has spent.
    """

    accounted: int
    """`Plan.accounted_token_burn` — one authoritative count per attempt."""
    sources: list[str] = []
    """Which measurement phases contributed, highest precedence first:
    `actual`, `preflight`, `estimate`. Empty means nothing has been measured,
    which is unknown and not zero — the surface must not present it as spend
    of 0 with the same confidence as a measured 0."""
    cap: int | None = None
    """`Plan.token_cap`, an admission-only declaration. `None` means no cap
    was ever declared, which is not a cap of zero and not an unlimited one."""
    remaining: int | None = None
    """`cap - accounted`, floored at 0; `None` whenever `cap` is."""


class FleetSpend(FrozenModel):
    """Spend summed over the listed runs, and the part of it that has a cap."""

    accounted: int
    sources: list[str] = []
    capped_runs: int = 0
    """How many listed runs declare a cap at all. Every figure below covers
    only those runs, because summing a declared cap with an absent one would
    invent a ceiling nobody set."""
    cap: int | None = None
    remaining: int | None = None


_SPEND_PHASES = ("actual", "preflight", "estimate")
"""Measurement phases in precedence order, the order provenance reads in."""


def run_spend(plan: Plan) -> RunSpend:
    """One run's admission burn with the provenance of its measurements.

    # ponytail: builds the whole ledger per plan per read, which is what makes
    # the provenance honest. If a large fleet's `/fleet` read ever shows up as
    # slow, cache on the store's last event id rather than dropping provenance.
    """
    ledger = token_ledger(plan)
    down = burn_down(plan, ledger)
    return RunSpend(
        accounted=down.accounted_tokens,
        sources=[
            phase for phase in _SPEND_PHASES if ledger.totals.provenance.get(phase)
        ],
        cap=plan.token_cap,
        remaining=down.remaining_plan_cap,
    )


def fleet_spend(rollups: Sequence[RunRollup]) -> FleetSpend:
    """Sum spend across runs, keeping capped and uncapped runs apart."""
    capped = [rollup for rollup in rollups if rollup.spend.cap is not None]
    seen = {
        source for rollup in rollups for source in rollup.spend.sources
    }
    return FleetSpend(
        accounted=sum(rollup.spend.accounted for rollup in rollups),
        sources=[phase for phase in _SPEND_PHASES if phase in seen],
        capped_runs=len(capped),
        cap=sum(r.spend.cap or 0 for r in capped) if capped else None,
        remaining=sum(r.spend.remaining or 0 for r in capped) if capped else None,
    )


class RunRollup(FrozenModel):
    """One run's status, progress, and attention — the fleet's row."""

    plan_id: str
    brief: str
    version: int
    approval: str
    status: RunStatus
    archived: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    """The last recorded event's time; `created_at` when nothing followed."""
    counts: dict[str, int]
    """Initiatives per state, keyed by `INITIATIVE_STATES` in that order."""
    total: int
    progress: float
    """Settled initiatives over total, 0.0 for an empty or unproposed plan.

    Cancelled work is not progress and is not removed from the denominator:
    a run that cancelled half its nodes did not finish half its brief.
    """
    attention: list[AttentionItem] = []
    spend: RunSpend = RunSpend(accounted=0)
    link: DeepLink


class Fleet(FrozenModel):
    """Every run at once: what is moving, and what is waiting on the user."""

    runs: list[RunRollup]
    """Active runs, newest first. Archived runs appear only when asked for."""
    archived: int
    """How many of the given runs are archived, listed or not — the count an
    active/archived navigation toggle shows."""
    counts: dict[str, int]
    """Initiative states summed over the listed runs."""
    running_runs: int
    total_runs: int
    attention: list[AttentionItem]
    """Merged across the listed runs, oldest first."""
    notifications: list[AttentionItem]
    """The user-blocking subset of `attention`, in the same order."""
    spend: FleetSpend = FleetSpend(accounted=0)
    """Token burn summed over the listed runs. Archived runs are excluded
    with everything else: a fleet's spend is the spend of what it lists."""
    unreadable: list[str] = []
    """Plan ids on disk whose events would not fold, so they are in no count
    above. A run that cannot be read is not a run that is not there: dropping
    it silently would report the fleet as smaller than it is."""


class DigestEntry(FrozenModel):
    """One thing that changed while the user was away."""

    at: AwareDatetime
    plan_id: str
    type: str
    initiative_id: str | None = None
    attempt_id: str | None = None
    summary: str
    link: DeepLink


def _link(plan_id: str, initiative_id: str | None = None, checkpoint_id: str | None = None) -> DeepLink:
    """The Run view location for one plan, initiative, or checkpoint.

    One spelling, built here only, so every consumer's links are byte-equal.
    Query parameters are percent-encoded because ids are free text as far as
    this module is concerned.
    """
    query: dict[str, str] = {"plan": plan_id}
    if initiative_id is not None:
        query["initiative"] = initiative_id
    if checkpoint_id is not None:
        query["checkpoint"] = checkpoint_id
    return DeepLink(path="/run?" + urlencode(query))


def deep_link(
    plan_id: str,
    initiative_id: str | None = None,
    checkpoint_id: str | None = None,
) -> DeepLink:
    """Public CLI/UI entry to the canonical Run-link builder."""
    return _link(plan_id, initiative_id, checkpoint_id)


def run_status(plan: Plan) -> RunStatus:
    """Roll one plan's initiatives up to a single run state.

    Precedence is fixed and total, so two callers cannot disagree: an
    unapproved version is a gate before anything else, live work outranks
    every settled outcome, and a run is only `settled` when nothing is left
    that could still run.
    """
    if plan.approval == "pending":
        return "awaiting_approval"
    if not plan.initiatives:
        return "empty"
    states = [initiative.state for initiative in plan.initiatives.values()]
    if "running" in states:
        return "running"
    if all(state in {"settled", "cancelled"} for state in states):
        return "settled"
    if "failed" in states:
        return "failed"
    if "paused" in states:
        return "paused"
    return "idle"


def attention(
    plan: Plan,
    events: Sequence[Event],
    *,
    now: AwareDatetime | None = None,
    stall_after_seconds: float = DEFAULT_STALL_SECONDS,
) -> list[AttentionItem]:
    """Everything in one run that needs the user, oldest first.

    Five kinds, and no sixth without a scope decision: the plan gate, a
    checkpoint whose review has not been decided, an agent that stopped to
    ask, a failed initiative, and a live attempt that has gone quiet past
    `stall_after_seconds`.

    `events` is required rather than optional because two of the kinds are
    only visible there — `RuntimeObserved` carries no projected state — and a
    projection that silently degraded when it was omitted would report "fine"
    for a blocked agent.
    """
    now = now or datetime.now(UTC)
    items: list[AttentionItem] = []

    if plan.approval == "pending" and plan.initiatives:
        proposed = max(
            (
                event.at
                for event in events
                if isinstance(event, PlanProposed) and event.version == plan.version
            ),
            default=plan.created_at,
        )
        items.append(
            AttentionItem(
                key=f"plan_gate:{plan.id}:{plan.version}",
                kind="plan_gate",
                plan_id=plan.id,
                summary=(
                    f"plan version {plan.version} is waiting for approval "
                    + f"({len(plan.initiatives)} initiative(s))"
                ),
                since=proposed,
                blocking=True,
                action=ActionTarget(
                    path=f"/plans/{plan.id}/approve", label="Approve plan"
                ),
                link=_link(plan.id),
            )
        )

    recorded_at = {
        event.checkpoint.id: event.at
        for event in events
        if isinstance(event, CheckpointRecorded)
    }
    failed_at: dict[str, list[AwareDatetime]] = {}
    for event in events:
        if isinstance(event, InitiativeFailed):
            failed_at.setdefault(event.initiative_id, []).append(event.at)

    activity = {item.attempt_id: item for item in activity_projection(events)}

    for initiative_id, initiative in plan.initiatives.items():
        spec_link = _link(plan.id, initiative_id)
        latest = initiative.latest_checkpoint
        # A missing decision is a pending one, the same default
        # `Initiative.approved_checkpoints` reads, so an unreviewed version is
        # never mistaken for a decided one.
        if (
            latest is not None
            and initiative.state not in {"settled", "cancelled"}
            and initiative.checkpoint_decisions.get(
                latest.id, CheckpointDecision()
            ).state
            == "pending"
        ):
            items.append(
                AttentionItem(
                    key=f"checkpoint_review:{plan.id}:{latest.id}",
                    kind="checkpoint_review",
                    plan_id=plan.id,
                    initiative_id=initiative_id,
                    attempt_id=latest.attempt_id,
                    checkpoint_id=latest.id,
                    summary=(
                        f"checkpoint {latest.id} of {initiative_id} is waiting "
                        + "for review"
                    ),
                    since=recorded_at.get(latest.id, plan.created_at),
                    blocking=True,
                    action=ActionTarget(
                        path=f"/plans/{plan.id}/checkpoints/{latest.id}/approve",
                        label="Review checkpoint",
                    ),
                    link=_link(plan.id, initiative_id, latest.id),
                )
            )

        if initiative.state == "failed":
            when = failed_at.get(initiative_id, [])
            items.append(
                AttentionItem(
                    key=f"failed:{plan.id}:{initiative_id}:{len(initiative.failures)}",
                    kind="failed",
                    plan_id=plan.id,
                    initiative_id=initiative_id,
                    attempt_id=(
                        initiative.attempts[-1].id if initiative.attempts else None
                    ),
                    summary=(
                        f"{initiative_id} failed: "
                        + (
                            initiative.failures[-1].reason
                            if initiative.failures
                            else "no reason recorded"
                        )
                    ),
                    since=when[-1] if when else plan.created_at,
                    blocking=True,
                    action=ActionTarget(
                        path=f"/plans/{plan.id}/initiatives/{initiative_id}/retry",
                        label="Retry initiative",
                    ),
                    link=spec_link,
                )
            )

        live = (
            initiative.attempts[-1]
            if initiative.attempts and initiative.attempts[-1].ended_at is None
            else None
        )
        if live is None or initiative.state != "running":
            continue
        observed = activity.get(live.id)
        last_at = observed.last_at if observed and observed.last_at else live.started_at
        if observed is not None and observed.last_status in BLOCKED_STATUSES:
            items.append(
                AttentionItem(
                    key=f"blocked_on_user:{plan.id}:{live.id}",
                    kind="blocked_on_user",
                    plan_id=plan.id,
                    initiative_id=initiative_id,
                    attempt_id=live.id,
                    summary=(
                        f"{initiative_id} is {observed.last_status} on the user"
                    ),
                    since=last_at,
                    blocking=True,
                    action=ActionTarget(
                        path=f"/plans/{plan.id}/attempts/{live.id}/answer",
                        label="Answer the agent",
                    ),
                    link=spec_link,
                )
            )
        elif (now - last_at).total_seconds() >= stall_after_seconds:
            items.append(
                AttentionItem(
                    key=f"stalled:{plan.id}:{live.id}",
                    kind="stalled",
                    plan_id=plan.id,
                    initiative_id=initiative_id,
                    attempt_id=live.id,
                    summary=(
                        f"{initiative_id} has been silent for "
                        + f"{int((now - last_at).total_seconds())}s"
                    ),
                    since=last_at,
                    # A stall is a wall-clock guess about an agent that may
                    # simply be thinking, and it becomes true again every
                    # tick. Attention, never a notification.
                    blocking=False,
                    action=ActionTarget(
                        path=f"/plans/{plan.id}/initiatives/{initiative_id}/nudge",
                        label="Nudge the agent",
                    ),
                    link=spec_link,
                )
            )

    return _ordered(items)


def _ordered(items: Sequence[AttentionItem]) -> list[AttentionItem]:
    """One total order for attention, everywhere: oldest first, then by key.

    `key` breaks every remaining tie and is unique per item, so the order is
    total and replay-stable no matter what order the caller assembled them in.
    """
    return sorted(items, key=lambda item: (item.since, item.plan_id, item.key))


def notifications(items: Sequence[AttentionItem]) -> list[AttentionItem]:
    """The user-blocking subset, in the order it was given.

    The whole notification rule: an item notifies when nothing proceeds until
    the user acts. Routine state changes never reach `attention` at all, so
    they cannot reach here, and adapters do not get a second opinion.
    """
    return [item for item in items if item.blocking]


def run_rollup(
    plan: Plan,
    events: Sequence[Event],
    *,
    now: AwareDatetime | None = None,
    stall_after_seconds: float = DEFAULT_STALL_SECONDS,
) -> RunRollup:
    """Project one run's fleet row: status, progress, and its attention."""
    counts = {
        state: sum(
            initiative.state == state for initiative in plan.initiatives.values()
        )
        for state in INITIATIVE_STATES
    }
    total = len(plan.initiatives)
    return RunRollup(
        plan_id=plan.id,
        brief=plan.brief,
        version=plan.version,
        approval=plan.approval,
        status=run_status(plan),
        archived=plan.archived,
        created_at=plan.created_at,
        updated_at=max((event.at for event in events), default=plan.created_at),
        counts=counts,
        total=total,
        progress=(counts["settled"] / total if total else 0.0),
        attention=attention(
            plan, events, now=now, stall_after_seconds=stall_after_seconds
        ),
        spend=run_spend(plan),
        link=_link(plan.id),
    )


def fleet(
    rollups: Sequence[RunRollup],
    *,
    include_archived: bool = False,
    unreadable: Sequence[str] = (),
) -> Fleet:
    """Aggregate run rollups into the fleet view.

    Takes rollups rather than plans so the loading stays in the daemon and
    this stays pure. Archived runs are excluded unless asked for — that is
    the whole of active/archived navigation. `unreadable` names the plans the
    caller could not fold, which are reported rather than counted.
    """
    listed = [
        rollup for rollup in rollups if include_archived or not rollup.archived
    ]
    items = _ordered([item for rollup in listed for item in rollup.attention])
    return Fleet(
        # Newest run first: the fleet is a place to resume from, and the
        # per-run attention is already ordered oldest-first inside each row.
        runs=sorted(listed, key=lambda r: (r.created_at, r.plan_id), reverse=True),
        archived=sum(1 for rollup in rollups if rollup.archived),
        counts={
            state: sum(rollup.counts.get(state, 0) for rollup in listed)
            for state in INITIATIVE_STATES
        },
        running_runs=sum(1 for rollup in listed if rollup.status == "running"),
        total_runs=len(listed),
        attention=items,
        notifications=notifications(items),
        spend=fleet_spend(listed),
        unreadable=list(unreadable),
    )


def digest(
    events: Sequence[Event],
    *,
    since: AwareDatetime | None = None,
    limit: int = 200,
) -> list[DigestEntry]:
    """What changed while the user was away, oldest first.

    Cross-plan: every event carries its `plan_id`, so a caller hands in as
    many streams as it wants and the merge is a sort. Routine traffic is
    dropped by `_ROUTINE_EVENTS` — a digest that replayed terminal chatter
    would be the thing it exists to replace.

    `limit` keeps the newest entries when a long absence overflows it, then
    restores chronological order: the most recent changes are the ones a
    returning user acts on.
    """
    if limit < 1:
        raise ValueError("digest limit must be positive")
    # Recorded order breaks every timestamp tie: `seq` is the store's own
    # ordering, and the caller's position covers events not yet appended (and
    # older streams, where `seq` replays as 0). Never the event type — that
    # would reorder a burst into alphabetical nonsense.
    kept = sorted(
        (
            (event, index)
            for index, event in enumerate(events)
            if event.type not in _ROUTINE_EVENTS
            and (since is None or event.at > since)
        ),
        key=lambda pair: (pair[0].at, pair[0].plan_id, pair[0].seq, pair[1]),
    )
    return [
        DigestEntry(
            at=event.at,
            plan_id=event.plan_id,
            type=event.type,
            initiative_id=getattr(event, "initiative_id", None),
            attempt_id=getattr(event, "attempt_id", None),
            summary=_summary(event),
            link=_link(event.plan_id, getattr(event, "initiative_id", None)),
        )
        for event, _ in kept[-limit:]
    ]


def _summary(event: Event) -> str:
    """One deterministic line per event, built from the event alone.

    Deliberately mechanical — the digest is a record of what happened, not
    prose about it, and a model call here would make replay non-deterministic.
    """
    checkpoint = event.checkpoint if isinstance(event, CheckpointRecorded) else None
    target = (
        getattr(event, "initiative_id", None)
        or getattr(event, "checkpoint_id", None)
        or (checkpoint.id if checkpoint is not None else None)
        or getattr(event, "attempt_id", None)
    )
    reason = str(getattr(event, "reason", "") or "")
    label = event.type.replace("_", " ")
    line = f"{label} {target}" if target else label
    return f"{line}: {reason}" if reason else line


__all__ = [
    "BLOCKED_STATUSES",
    "DEFAULT_STALL_SECONDS",
    "INITIATIVE_STATES",
    "ActionTarget",
    "AttentionItem",
    "AttentionKind",
    "DeepLink",
    "DigestEntry",
    "Fleet",
    "FleetSpend",
    "RunRollup",
    "RunSpend",
    "RunStatus",
    "attention",
    "digest",
    "fleet",
    "fleet_spend",
    "notifications",
    "run_rollup",
    "run_spend",
    "run_status",
]
