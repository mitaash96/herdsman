"""Graph calculations over a plan.

NetworkX computes; Herdsman decides. Nothing here mutates state or persists
anything — every function is a pure projection of an already-folded `Plan`, so
the daemon can answer a graph question without touching the event store twice.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Literal, cast

import networkx as nx
from pydantic import ConfigDict

from .checkpoint import CheckpointError
from .classes import Model, Plan, ScopeTrie

TARGET_OVERHEAD_RATIO = 0.20
"""The public, falsifiable claim: orchestration tokens over productive tokens."""


def graph_of(plan: Plan) -> "nx.DiGraph[str]":
    """The plan's dependency DAG, edges pointing from producer to consumer."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(plan.initiatives)
    for initiative in plan.initiatives.values():
        for dependency in initiative.spec.depends_on:
            if dependency in plan.initiatives:
                _ = graph.add_edge(dependency, initiative.spec.id)
    return graph


def critical_path(plan: Plan) -> list[str]:
    """The longest dependency chain — the plan's floor on wall-clock time.

    Unweighted: every initiative counts as one hop. Sprint 4's ledger is what
    supplies real per-node cost, and weighting this by a guess before then
    would dress an estimate up as a measurement.
    """
    graph = graph_of(plan)
    if not graph:
        return []
    return list(nx.dag_longest_path(graph))


def max_concurrency(plan: Plan) -> int:
    """The widest set of initiatives that could run at once.

    Dilworth's theorem: the largest antichain equals the smallest chain cover,
    which is `n` minus a maximum matching over the transitive closure. That is
    the most agents this plan can ever keep busy — a scheduler limit above it
    buys nothing.
    """
    graph = graph_of(plan)
    if not graph:
        return 0
    closure = nx.transitive_closure_dag(graph)
    bipartite: nx.Graph[str] = nx.Graph()
    bipartite.add_nodes_from((f"out:{node}" for node in closure), bipartite=0)
    bipartite.add_nodes_from((f"in:{node}" for node in closure), bipartite=1)
    bipartite.add_edges_from(
        (f"out:{u}", f"in:{v}") for u, v in closure.edges
    )
    matching = nx.algorithms.bipartite.maximum_matching(
        bipartite, top_nodes=[f"out:{node}" for node in closure]
    )
    return len(closure) - len(matching) // 2


# --- contention --------------------------------------------------------------


def _trie(plan: Plan, writes: bool) -> ScopeTrie:
    trie = ScopeTrie()
    for initiative in plan.initiatives.values():
        routes = initiative.spec.routes
        for path in routes.writes if writes else routes.reads:
            trie.insert(path, initiative.spec.id)
    return trie


ContentionKind = Literal["write_write", "write_read"]


class Contention(Model):
    """Two initiatives the plan lets run together whose scopes overlap."""

    initiatives: tuple[str, str]
    """Sorted, so the pair has one stable identity regardless of direction."""
    paths: list[str]
    kind: ContentionKind
    """`write_write` — a real conflict. `write_read` — a missing edge."""
    writer: str | None = None
    """For `write_read`, who produces. Sorting `initiatives` loses this."""
    reader: str | None = None
    """For `write_read`, who consumes — so the suggested edge has a direction."""


def contention(plan: Plan) -> list[Contention]:
    """Overlapping scopes between initiatives neither of which orders the other.

    Only unordered pairs matter: a dependency already serializes its ends, so
    an overlap along an edge is a handoff, not a conflict.
    """
    closure = nx.transitive_closure_dag(graph_of(plan))
    writes = _trie(plan, writes=True)
    reads = _trie(plan, writes=False)
    found: dict[tuple[tuple[str, str], ContentionKind], set[str]] = {}

    def ordered(left: str, right: str) -> bool:
        return closure.has_edge(left, right) or closure.has_edge(right, left)

    for initiative in plan.initiatives.values():
        source = initiative.spec.id
        for path in initiative.spec.routes.writes:
            for peer in writes.touching(path) - {source}:
                if ordered(source, peer):
                    continue
                pair = (min(source, peer), max(source, peer))
                found.setdefault((pair, "write_write"), set()).add(path)
            for peer in reads.touching(path) - {source}:
                if ordered(source, peer):
                    continue
                pair = (min(source, peer), max(source, peer))
                if (pair, "write_write") in found:
                    continue  # already a conflict; a missing edge is the lesser claim
                # Keyed by direction, so a mutual overlap stays two findings.
                found.setdefault(((source, peer), "write_read"), set()).add(path)
    return [
        Contention(
            initiatives=(min(pair), max(pair)),
            paths=sorted(paths),
            kind=kind,
            writer=pair[0] if kind == "write_read" else None,
            reader=pair[1] if kind == "write_read" else None,
        )
        for (pair, kind), paths in sorted(found.items())
    ]


def conflicts_with(plan: Plan, initiative_id: str, others: set[str]) -> bool:
    """Whether starting `initiative_id` now would overlap a running writer.

    The scheduler's serialization rule. Read overlap is not a conflict; only
    two writers in one subtree are.
    """
    # ponytail: recomputes the whole contention set per scheduler tick. Cache
    # it against the plan version if plans ever get large enough to notice.
    return any(
        initiative_id in found.initiatives
        and bool((set(found.initiatives) - {initiative_id}) & others)
        for found in contention(plan)
        if found.kind == "write_write"
    )


def ancestor_patches(plan: Plan, initiative_id: str) -> list[str]:
    """Every upstream patch this initiative must start from, in build order.

    Transitive, not just direct: in a diamond, `d` depends on `b` and `c`, and
    neither of their patches carries `a`'s work -- each checkpoint's patch holds
    only that initiative's own delta. Applying the whole ancestry in topological
    order is what reconstructs the state `d` is supposed to build on, and
    deduplication is why `a` is applied once rather than through both branches.
    """
    graph = graph_of(plan)
    ancestors = _ancestors(graph, initiative_id)
    patches: list[str] = []
    for node in nx.topological_sort(graph):
        if node not in ancestors:
            continue
        initiative = plan.initiatives[node]
        checkpoint = next(
            (
                attempt.checkpoint
                for attempt in reversed(initiative.attempts)
                if attempt.checkpoint is not None
            ),
            None,
        )
        if checkpoint is None:
            # A settled ancestor without a checkpoint is the same provisioning failure.
            raise CheckpointError(f"settled ancestor {node} has no checkpoint")
        if checkpoint.patch_path is None:
            raise CheckpointError(f"settled ancestor {node} has no patch artifact")
        patches.append(checkpoint.patch_path)
    return patches


# --- plan gate ---------------------------------------------------------------


def _ancestors(graph: "nx.DiGraph[str]", node: str) -> set[str]:
    """Everything upstream of one initiative."""
    ancestors = cast("Callable[[nx.DiGraph[str], str], set[str]]", nx.ancestors)
    return ancestors(graph, node)


def _descendants(graph: "nx.DiGraph[str]", node: str) -> set[str]:
    """Everything downstream of one initiative."""
    descendants = cast(
        "Callable[[nx.DiGraph[str], str], set[str]]", nx.descendants
    )
    return descendants(graph, node)


class NodeRisk(Model):
    initiative_id: str
    digest: str
    blast_radius: int
    """How many initiatives fail to become ready if this one never settles."""
    articulation: bool
    """Removing this node disconnects the plan: a single point of failure."""
    on_critical_path: bool


class RiskReport(Model):
    """The plan gate's structured two-minute decision, not a reading exercise."""

    plan_id: str
    version: int
    critical_path: list[str]
    max_concurrency: int
    nodes: list[NodeRisk]
    conflicts: list[Contention]
    suggested_edges: list[Contention]
    warnings: list[str]


def risk_report(plan: Plan, *, tiers: dict[str, str] | None = None) -> RiskReport:
    """Structural risk for one proposed plan version."""
    graph = graph_of(plan)
    path = critical_path(plan)
    on_path = set(path)
    articulation: set[str] = (
        set(nx.articulation_points(graph.to_undirected())) if graph else set()
    )
    found = contention(plan)
    conflicts = [item for item in found if item.kind == "write_write"]
    suggested = [item for item in found if item.kind == "write_read"]

    warnings = [
        f"initiatives {item.initiatives[0]} and {item.initiatives[1]} both write "
        + f"{', '.join(item.paths)}; they cannot run concurrently"
        for item in conflicts
    ]
    warnings.extend(
        f"initiative {item.reader} reads {', '.join(item.paths)}, written by "
        + f"{item.writer}, with no dependency between them"
        for item in suggested
    )
    # A cheap model on the critical path delays everything downstream of it,
    # which is the one place the saving is not worth it.
    model_tiers = tiers or {}
    warnings.extend(
        f"initiative {node} sits on the critical path with a "
        + f"{model_tiers[plan.initiatives[node].spec.assignment.model]} model "
        + f"({plan.initiatives[node].spec.assignment.model})"
        for node in path
        if plan.initiatives[node].spec.assignment.model in model_tiers
        and model_tiers[plan.initiatives[node].spec.assignment.model] != "frontier"
    )

    return RiskReport(
        plan_id=plan.id,
        version=plan.version,
        critical_path=path,
        max_concurrency=max_concurrency(plan),
        nodes=[
            NodeRisk(
                initiative_id=initiative_id,
                digest=initiative.spec.digest,
                blast_radius=len(_descendants(graph, initiative_id)),
                articulation=initiative_id in articulation,
                on_critical_path=initiative_id in on_path,
            )
            for initiative_id, initiative in plan.initiatives.items()
        ],
        conflicts=conflicts,
        suggested_edges=suggested,
        warnings=warnings,
    )


# --- running projections -----------------------------------------------------


class NodeStatus(Model):
    initiative_id: str
    name: str
    digest: str
    state: str
    depends_on: list[str]
    harness: str
    model: str
    attempts: int
    checkpoint_id: str | None
    ready: bool


class Overhead(Model):
    """The crude ratio. Sprint 4 replaces it with the attributed ledger."""

    orchestration_tokens: int
    """Everything Herdsman injects: compiled task packets, and later memory."""
    productive_tokens: int
    """Harness-reported usage, planner included. Never an estimate or a guess."""
    ratio: float | None
    target: float = TARGET_OVERHEAD_RATIO
    within_target: bool | None


class PlanGraph(Model):
    """The stable projection the UI and CLI render a running plan from."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    plan_id: str
    version: int
    approval: str
    nodes: list[NodeStatus]
    edges: list[tuple[str, str]]
    ready: list[str]
    critical_path: list[str]
    max_concurrency: int
    overhead: Overhead


def overhead(plan: Plan) -> Overhead:
    """Two counters and a division."""
    attempts = [
        attempt
        for initiative in plan.initiatives.values()
        for attempt in initiative.attempts
    ]
    orchestration = sum(attempt.packet_tokens for attempt in attempts)
    productive = sum(
        attempt.checkpoint.usage.input_tokens + attempt.checkpoint.usage.output_tokens
        for attempt in attempts
        if attempt.checkpoint is not None
        and attempt.checkpoint.usage is not None
        and attempt.checkpoint.usage.source == "harness"
    )
    if plan.planner_usage is not None and plan.planner_usage.source == "harness":
        # Frontier planning earns its harness-reported tokens; provider and
        # estimate values are not measurements of productive work.
        productive += (
            plan.planner_usage.input_tokens + plan.planner_usage.output_tokens
        )
    ratio = orchestration / productive if productive else None
    return Overhead(
        orchestration_tokens=orchestration,
        productive_tokens=productive,
        ratio=ratio,
        within_target=None if ratio is None else ratio <= TARGET_OVERHEAD_RATIO,
    )


def plan_graph(plan: Plan) -> PlanGraph:
    """Project the running graph and per-node status."""
    ready = set(plan.ready())
    return PlanGraph(
        plan_id=plan.id,
        version=plan.version,
        approval=plan.approval,
        nodes=[
            NodeStatus(
                initiative_id=initiative_id,
                name=initiative.spec.name,
                digest=initiative.spec.digest,
                state=initiative.state,
                depends_on=list(initiative.spec.depends_on),
                harness=initiative.spec.assignment.harness,
                model=initiative.spec.assignment.model,
                attempts=len(initiative.attempts),
                checkpoint_id=next(
                    (
                        attempt.checkpoint.id
                        for attempt in reversed(initiative.attempts)
                        if attempt.checkpoint is not None
                    ),
                    None,
                ),
                ready=initiative_id in ready,
            )
            for initiative_id, initiative in plan.initiatives.items()
        ],
        edges=[(u, v) for u, v in graph_of(plan).edges],
        ready=sorted(ready),
        critical_path=critical_path(plan),
        max_concurrency=max_concurrency(plan),
        overhead=overhead(plan),
    )


__all__ = [
    "Contention",
    "NodeRisk",
    "NodeStatus",
    "Overhead",
    "PlanGraph",
    "RiskReport",
    "ScopeTrie",
    "TARGET_OVERHEAD_RATIO",
    "ancestor_patches",
    "conflicts_with",
    "contention",
    "critical_path",
    "graph_of",
    "max_concurrency",
    "overhead",
    "plan_graph",
    "risk_report",
]
