"""Deterministic token, activity, and makespan projections.

This module is intentionally a pure reducer. It reads folded plans and (where
needed for runtime activity) their already-persisted events; it never calls a
model or a third-party runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

import networkx as nx

from pydantic import AwareDatetime, BaseModel

from .classes import (
    Event,
    PacketSnapshot,
    Plan,
    RuntimeObserved,
    TokenCategory,
    TokenMeasurement,
    TokenPhase,
    TokenSource,
    Usage,
    token_measurement_rank,
)


class TokenTotals(BaseModel):
    actual: int = 0
    preflight: int = 0
    estimate: int = 0
    productive: int = 0
    orchestration: int = 0
    provenance: dict[str, list[str]] = {}
    derivation: dict[str, str] = {}


class TokenLedger(BaseModel):
    """All selected measurements plus attributable totals."""

    plan_id: str
    entries: list[TokenMeasurement] = []
    totals: TokenTotals = TokenTotals()
    by_category: dict[str, int] = {}
    by_provenance: dict[str, int] = {}
    derivation: str = "selected measurements grouped by semantic work identity"
    provenance: list[str] = []
    accounted_tokens: int = 0
    accounted_derivation: str = "highest-precedence usage replaces same-attempt packet preflight"

    @property
    def productive_tokens(self) -> int:
        return self.totals.productive

    @property
    def orchestration_tokens(self) -> int:
        return self.totals.orchestration

    @property
    def ratio(self) -> float | None:
        return (
            self.orchestration_tokens / self.productive_tokens
            if self.productive_tokens
            else None
        )


class BurnDown(BaseModel):
    accounted_tokens: int
    productive_tokens: int
    orchestration_tokens: int
    remaining_plan_cap: int | None = None
    remaining_initiative_caps: dict[str, int | None] = {}
    derivation: str = "highest-precedence count per attempt plus planning usage"
    provenance: list[str] = []


class TokenAnomaly(BaseModel):
    code: Literal[
        "overhead",
        "exhausted-budget",
        "missing-usage",
        "conflicting-usage",
    ]
    message: str
    initiative_id: str | None = None
    attempt_id: str | None = None


class Activity(BaseModel):
    attempt_id: str
    initiative_id: str
    first_at: AwareDatetime | None = None
    last_at: AwareDatetime | None = None
    last_status: str | None = None
    events: int = 0
    kinds: list[str] = []


class Vitals(BaseModel):
    running_attempts: int
    working_attempts: int
    blocked_attempts: int
    observed_events: int
    pane_events: int
    derivation: str = "deterministic reduction of folded plan and RuntimeObserved events"
    provenance: str = "herdsman events"


class MakespanETA(BaseModel):
    eta: AwareDatetime | None = None
    remaining_seconds: float | None = None
    reason: str = ""
    derivation: str = "explicit remaining estimates minus elapsed running time"
    provenance: list[str] = []


class InitiativeEvent(BaseModel):
    at: AwareDatetime
    type: str
    initiative_id: str | None = None
    attempt_id: str | None = None


def _usage_entry(
    plan_id: str,
    usage: Usage,
    *,
    entry_id: str,
    initiative_id: str | None,
    attempt_id: str | None,
    default_category: TokenCategory,
    observed_at: AwareDatetime | None = None,
) -> TokenMeasurement:
    phase: TokenPhase = usage.phase
    source: TokenSource = usage.source
    return TokenMeasurement(
        entry_id=usage.measurement_id or entry_id,
        plan_id=plan_id,
        initiative_id=initiative_id,
        attempt_id=attempt_id,
        phase=phase,
        source=source,
        category=usage.category or default_category,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        provenance=usage.provenance or f"{source}:{entry_id}",
        observed_at=observed_at,
        semantic_work_id=usage.semantic_work_id or entry_id,
        gateway_used=usage.gateway_used,
    )


def _packet_entry(
    plan_id: str, initiative_id: str, attempt_id: str, snapshot: PacketSnapshot
) -> list[TokenMeasurement]:
    return [
        TokenMeasurement(
            entry_id=f"{attempt_id}:packet:{section.name}",
            plan_id=plan_id,
            initiative_id=initiative_id,
            attempt_id=attempt_id,
            phase=section.phase,
            source=section.source,
            category=section.category,
            input_tokens=section.input_tokens,
            output_tokens=section.output_tokens,
            provenance=section.provenance,
            semantic_work_id=section.semantic_work_id or f"{attempt_id}:packet:{section.name}",
            gateway_used=section.gateway_used,
        )
        for section in snapshot.sections
    ]


def normalize_usage(
    usage: Usage,
    *,
    plan_id: str,
    entry_id: str,
    initiative_id: str | None = None,
    attempt_id: str | None = None,
    category: TokenCategory = "execution",
) -> TokenMeasurement:
    """Normalize one legacy or modern usage row with explicit provenance."""
    return _usage_entry(
        plan_id,
        usage,
        entry_id=entry_id,
        initiative_id=initiative_id,
        attempt_id=attempt_id,
        default_category=category,
    )


def select_measurements(entries: Iterable[TokenMeasurement]) -> list[TokenMeasurement]:
    """Select by measurement identity and phase precedence, never by summing."""
    return _deduplicate(entries)


def _measurement_rank(entry: TokenMeasurement) -> int:
    """The one source precedence used by every ledger projection."""
    return token_measurement_rank(entry.phase, entry.source, entry.gateway_used)


def _deduplicate(entries: Iterable[TokenMeasurement]) -> list[TokenMeasurement]:
    """Select one value per semantic work identity; alternatives are not summed."""
    selected: dict[str, TokenMeasurement] = {}
    for entry in entries:
        identity = entry.semantic_work_id or entry.entry_id
        prior = selected.get(identity)
        if prior is None or _measurement_rank(entry) > _measurement_rank(prior):
            selected[identity] = entry
        elif _measurement_rank(entry) == _measurement_rank(prior) and entry.model_dump(mode="json") != prior.model_dump(mode="json"):
            # Keep first-in-stream deterministically; anomaly reporting can call
            # out conflicting same-rank rows without double counting them.
            continue
    return list(selected.values())


def token_ledger(plan: Plan) -> TokenLedger:
    """Build the attributable ledger from a folded plan without double counting."""
    entries: list[TokenMeasurement] = []
    history = plan.planner_usage_history or (
        [plan.planner_usage] if plan.planner_usage is not None else []
    )
    for number, usage in enumerate(history, start=1):
        entries.append(
            _usage_entry(
                plan.id,
                usage,
                entry_id=usage.measurement_id or f"planner:{number}",
                initiative_id=None,
                attempt_id=None,
                default_category="planning",
            )
        )
    # Retired nodes are out of the plan but not out of the bill: their packets
    # and usage were really spent, so a recalibration cannot refund them.
    for initiative in [*plan.initiatives.values(), *plan.retired]:
        for attempt in initiative.attempts:
            if attempt.packet_snapshot is not None:
                entries.extend(
                    _packet_entry(
                        plan.id,
                        initiative.spec.id,
                        attempt.id,
                        attempt.packet_snapshot,
                    )
                )
            elif attempt.packet_tokens:
                entries.append(
                    TokenMeasurement(
                        entry_id=f"{attempt.id}:packet:legacy",
                        plan_id=plan.id,
                        initiative_id=initiative.spec.id,
                        attempt_id=attempt.id,
                        phase="estimate",
                        source="estimate",
                        category="repeated_context",
                        input_tokens=attempt.packet_tokens,
                        provenance="legacy packet_tokens estimate",
                    )
                )
            checkpoint = attempt.checkpoint
            if checkpoint is not None and checkpoint.usage is not None:
                entries.append(
                    _usage_entry(
                        plan.id,
                        checkpoint.usage,
                        entry_id=checkpoint.usage.measurement_id or f"{attempt.id}:actual",
                        initiative_id=initiative.spec.id,
                        attempt_id=attempt.id,
                        default_category="execution",
                        observed_at=attempt.ended_at,
                    )
                )
    entries = _deduplicate(entries)
    totals = TokenTotals()
    by_category: dict[str, int] = {}
    by_provenance: dict[str, int] = {}
    productive_categories = {"planning", "execution", "semantic_integration"}
    provenance: dict[str, set[str]] = {
        "actual": set(),
        "preflight": set(),
        "estimate": set(),
        "productive": set(),
        "orchestration": set(),
    }
    derivation = {
        "actual": "selected actual measurements by semantic work identity",
        "preflight": "selected native/gateway/tokenizer preflight measurements",
        "estimate": "selected labelled estimates",
        "productive": "actual provider or harness execution/planning categories only",
        "orchestration": "selected packet and non-productive category measurements",
    }
    for entry in entries:
        total = entry.total_tokens
        provenance.setdefault(entry.phase, set()).add(entry.provenance)
        setattr(totals, entry.phase, getattr(totals, entry.phase) + total)
        if (
            entry.category in productive_categories
            and entry.phase == "actual"
            and entry.source in {"harness", "provider"}
        ):
            # Actual provider counts are as authoritative as harness actuals;
            # estimates and unused gateway rows never enter the denominator.
            totals.productive += total
            provenance["productive"].add(entry.provenance)
        elif entry.category not in productive_categories:
            totals.orchestration += total
            provenance["orchestration"].add(entry.provenance)
        by_category[entry.category] = by_category.get(entry.category, 0) + total
        by_provenance[entry.provenance] = by_provenance.get(entry.provenance, 0) + total
    totals.provenance = {
        key: sorted(values) for key, values in provenance.items()
    }
    totals.derivation = derivation
    return TokenLedger(
        plan_id=plan.id,
        entries=entries,
        totals=totals,
        by_category=by_category,
        by_provenance=by_provenance,
        provenance=sorted(by_provenance),
        accounted_tokens=accounted_burn(plan),
    )


def accounted_burn(plan: Plan, initiative_id: str | None = None) -> int:
    """Use the fold's shared precedence-aware admission accounting."""
    return plan.accounted_token_burn(initiative_id)


def burn_down(plan: Plan, ledger: TokenLedger | None = None) -> BurnDown:
    ledger = ledger or token_ledger(plan)
    accounted = accounted_burn(plan)
    remaining_plan = (
        max(plan.token_cap - accounted, 0)
        if plan.token_cap is not None
        else None
    )
    return BurnDown(
        accounted_tokens=accounted,
        productive_tokens=ledger.productive_tokens,
        orchestration_tokens=ledger.orchestration_tokens,
        remaining_plan_cap=remaining_plan,
        remaining_initiative_caps={

            initiative.spec.id: (
                max(
                    initiative.spec.token_cap
                    - accounted_burn(plan, initiative.spec.id),
                    0,
                )
                if initiative.spec.token_cap is not None
                else None
            )
            for initiative in plan.initiatives.values()
        },
        provenance=ledger.provenance,
    )


def anomalies(
    plan: Plan,
    ledger: TokenLedger | None = None,
    *,
    overhead_limit: float = 0.20,
) -> list[TokenAnomaly]:
    ledger = ledger or token_ledger(plan)
    result: list[TokenAnomaly] = []
    ratio = ledger.ratio
    if ratio is not None and ratio > overhead_limit:
        result.append(TokenAnomaly(code="overhead", message=f"overhead ratio {ratio:.3f} exceeds {overhead_limit:.2f}"))
    if plan.token_cap is not None and accounted_burn(plan) >= plan.token_cap:
        result.append(TokenAnomaly(code="exhausted-budget", message="plan token cap exhausted"))
    seen: dict[str, tuple[int, int, str]] = {}
    for initiative in plan.initiatives.values():
        if initiative.spec.token_cap is not None:
            total = accounted_burn(plan, initiative.spec.id)
            if total >= initiative.spec.token_cap:
                result.append(TokenAnomaly(code="exhausted-budget", message="initiative token cap exhausted", initiative_id=initiative.spec.id))
        for attempt in initiative.attempts:
            if attempt.checkpoint is not None and attempt.checkpoint.usage is None:
                result.append(TokenAnomaly(code="missing-usage", message="checkpoint has no usage", initiative_id=initiative.spec.id, attempt_id=attempt.id))
            usage = attempt.checkpoint.usage if attempt.checkpoint is not None else None
            if usage is not None and usage.measurement_id is not None:
                value = (usage.input_tokens, usage.output_tokens, usage.source)
                prior = seen.get(usage.measurement_id)
                if prior is not None and prior != value:
                    result.append(TokenAnomaly(code="conflicting-usage", message=f"measurement {usage.measurement_id} has conflicting values", initiative_id=initiative.spec.id, attempt_id=attempt.id))
                seen[usage.measurement_id] = value
    return result


def makespan_eta(
    plan: Plan,
    *,
    now: AwareDatetime | None = None,
) -> MakespanETA:
    """Use explicit estimates only; subtract elapsed time for running work."""
    now = now or datetime.now(UTC)
    remaining: dict[str, float] = {}
    provenance: list[str] = []
    for initiative in plan.initiatives.values():
        if initiative.state in {"settled", "cancelled"}:
            continue
        estimate = initiative.spec.duration_estimate_seconds
        if estimate is None:
            return MakespanETA(
                reason=f"duration estimate unknown for {initiative.spec.id}",
                provenance=provenance,
            )
        elapsed = 0.0
        if initiative.state == "running" and initiative.attempts:
            attempt = initiative.attempts[-1]
            elapsed = max((now - attempt.started_at).total_seconds(), 0.0)
        remaining[initiative.spec.id] = max(estimate - elapsed, 0.0)
        provenance.append(f"{initiative.spec.id}:explicit-duration-estimate")
    if not remaining:
        return MakespanETA(
            eta=now,
            remaining_seconds=0,
            reason="plan complete",
            provenance=provenance,
        )
    # Longest remaining dependency path is the deterministic DAG makespan floor.
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(plan.initiatives.keys())
    for initiative in plan.initiatives.values():
        for dependency in initiative.spec.depends_on:
            if dependency in plan.initiatives:
                _ = graph.add_edge(dependency, initiative.spec.id)
    longest: dict[str, float] = {}
    for node in reversed(list(nx.topological_sort(graph))):
        longest[node] = remaining.get(node, 0.0) + max(
            (longest[child] for child in graph.successors(node)), default=0.0
        )
    seconds = max((longest.get(node, 0.0) for node in remaining), default=0.0)
    return MakespanETA(
        eta=now + timedelta(seconds=seconds),
        remaining_seconds=seconds,
        reason="explicit estimates with elapsed running time subtracted",
        provenance=provenance,
    )


def activity_projection(events: Sequence[Event]) -> list[Activity]:
    by_attempt: dict[str, Activity] = {}
    for event in events:
        if not isinstance(event, RuntimeObserved):
            continue
        item = by_attempt.get(event.attempt_id)
        if item is None:
            item = Activity(attempt_id=event.attempt_id, initiative_id="", first_at=event.at, last_at=event.at)
            by_attempt[event.attempt_id] = item
        item.first_at = min(item.first_at, event.at) if item.first_at else event.at
        item.last_at = max(item.last_at, event.at) if item.last_at else event.at
        item.events += 1
        if event.kind not in item.kinds:
            item.kinds.append(event.kind)
        status = event.detail.get("status") or event.detail.get("agent_status")
        if isinstance(status, str):
            item.last_status = status
    return sorted(by_attempt.values(), key=lambda item: (item.first_at or datetime.min.replace(tzinfo=UTC), item.attempt_id))


def vitals(plan: Plan, events: Sequence[Event]) -> Vitals:
    activities = activity_projection(events)
    initiative_by_attempt = {
        attempt.id: initiative.spec.id
        for initiative in plan.initiatives.values()
        for attempt in initiative.attempts
    }
    for item in activities:
        item.initiative_id = initiative_by_attempt.get(item.attempt_id, "")
    return Vitals(
        running_attempts=sum(i.state == "running" for i in plan.initiatives.values()),
        working_attempts=sum(
            item.last_status in {"working", "busy", "running"} for item in activities
        ),
        blocked_attempts=sum(
            item.last_status in {"blocked", "waiting"} for item in activities
        ),
        observed_events=sum(item.events for item in activities),
        pane_events=sum(
            item.events for item in activities if any("pane" in kind for kind in item.kinds)
        ),
    )


def initiative_events(events: Sequence[Event]) -> list[InitiativeEvent]:
    return [
        InitiativeEvent(
            at=event.at,
            type=event.type,
            initiative_id=getattr(event, "initiative_id", None),
            attempt_id=getattr(event, "attempt_id", None),
        )
        for event in events
        if getattr(event, "type", "") != "runtime_observed"
    ]


def packet_diff(previous: PacketSnapshot, current: PacketSnapshot):
    from .classes import PacketDiff
    before = {section.name: section for section in previous.sections}
    after = {section.name: section for section in current.sections}
    provenance = sorted(
        {previous.provenance, current.provenance}
        | {section.provenance for section in previous.sections}
        | {section.provenance for section in current.sections}
    )
    return PacketDiff(
        changed_sections=sorted(
            name for name in before.keys() & after.keys()
            if before[name] != after[name]
        ),
        added_sections=sorted(after.keys() - before.keys()),
        removed_sections=sorted(before.keys() - after.keys()),
        token_delta=current.total_tokens - previous.total_tokens,
        before_tokens=previous.total_tokens,
        after_tokens=current.total_tokens,
        provenance=provenance,
        derivation="canonical launched packet total delta; section values compared by name",
    )


__all__ = [
    "Activity", "BurnDown", "InitiativeEvent", "MakespanETA", "TokenAnomaly",
    "TokenLedger", "TokenTotals", "Vitals", "activity_projection", "anomalies",
    "accounted_burn", "burn_down", "initiative_events", "makespan_eta", "packet_diff", "token_ledger",
    "vitals", "normalize_usage", "select_measurements",
]
