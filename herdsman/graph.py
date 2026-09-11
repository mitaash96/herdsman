"""Graph calculations over a plan.

NetworkX computes; Herdsman decides. Nothing here mutates state or persists
anything — every function is a pure projection of an already-folded `Plan`, so
the daemon can answer a graph question without touching the event store twice.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import ClassVar, Literal, cast

import networkx as nx
from pydantic import ConfigDict

from .checkpoint import CheckpointError
from .classes import InitiativeSpec, Model, Plan, ScopeTrie

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
    brief_version: int
    """The brief version new attempts would run on; 1 is the planner's."""
    attempts: int
    checkpoint_id: str | None
    ready: bool


class NodeImpact(Model):
    initiative_id: str
    state: str
    attempts: int


class DownstreamImpact(Model):
    """What a disruptive action on one initiative would disturb."""

    initiative_id: str
    """The action's target; not itself part of the impact."""
    descendants: list[NodeImpact]
    """Everything downstream, in build order."""
    started: list[str]
    """Descendants that already ran — work the action would strand or redo."""


class Overhead(Model):
    """Attributed ratio with an explicit deterministic derivation."""

    orchestration_tokens: int
    """Everything Herdsman injects: compiled task packets, and later memory."""
    productive_tokens: int
    """Actual provider/harness usage, planner included. Never an estimate."""
    ratio: float | None
    target: float = TARGET_OVERHEAD_RATIO
    within_target: bool | None
    derivation: str = "selected orchestration tokens / actual provider-or-harness productive tokens"
    provenance: list[str] = []
    recalibration_tokens: int = 0
    """What re-planning cost, attributed on its own so it never hides inside
    planning: the ledger's `recalibration_replay` rows only — never inferred
    from `planner_usage_history` positions, and unknown usage stays unknown."""
    recalibration_calls: int = 0
    """Selected `recalibration_replay` measurements behind the token figure."""
    recalibration_derivation: str = "no recalibration_replay measurement recorded"


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
    """Project attributed packet/orchestration and productive token totals."""
    from .observability import token_ledger

    measured = token_ledger(plan)
    ratio = measured.orchestration_tokens / measured.productive_tokens if measured.productive_tokens else None
    recalibration_calls = sum(
        1
        for entry in measured.entries
        if entry.category == "recalibration_replay"
    )
    return Overhead(
        orchestration_tokens=measured.orchestration_tokens,
        productive_tokens=measured.productive_tokens,
        ratio=ratio,
        within_target=None if ratio is None else ratio <= TARGET_OVERHEAD_RATIO,
        provenance=measured.provenance,
        recalibration_tokens=measured.by_category.get("recalibration_replay", 0),
        recalibration_calls=recalibration_calls,
        recalibration_derivation=(
            "selected recalibration_replay measurements grouped by semantic work identity"
            if recalibration_calls
            else "no recalibration_replay measurement recorded"
        ),
    )


def downstream_impact(plan: Plan, initiative_id: str) -> DownstreamImpact:
    """What a disruptive action on one initiative would disturb, purely.

    The intervention surface shows this before a retry, redirect, or
    reassignment commits: descendants still pending are merely delayed, but
    descendants that already ran consumed the target's evidence and would
    have to re-run on the new one.
    """
    if initiative_id not in plan.initiatives:
        raise ValueError(f"unknown initiative {initiative_id}")
    graph = graph_of(plan)
    below = _descendants(graph, initiative_id)
    order = [node for node in nx.topological_sort(graph) if node in below]
    return DownstreamImpact(
        initiative_id=initiative_id,
        descendants=[
            NodeImpact(
                initiative_id=node,
                state=plan.initiatives[node].state,
                attempts=len(plan.initiatives[node].attempts),
            )
            for node in order
        ],
        started=[node for node in order if plan.initiatives[node].attempts],
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
                harness=initiative.current_assignment.harness,
                model=initiative.current_assignment.model,
                brief_version=len(initiative.brief_versions) + 1,
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


# --- recalibration diff ------------------------------------------------------


NodeChange = Literal["unchanged", "edited", "split", "merged", "new", "removed"]
"""How one node moved between two plan versions, by content-addressed identity."""

EdgeState = Literal["same", "changed", "unresolved"]
"""Whether a node's declared dependencies survived the revision."""


class NodeRevision(Model):
    """One node, or one node group, classified between two plan versions."""

    change: NodeChange
    old_ids: list[str]
    """Ids the node carried before the revision; empty for a new node."""
    new_ids: list[str]
    """Ids it carries after; empty for a removed node, several for a split."""
    old_digest: str | None
    """The content digest — defined only when exactly one id sits on that side."""
    new_digest: str | None
    old_token_caps: list[int | None]
    """The admission cap each id on the old side carries, in id order.

    ``None`` is a real value — a node with no cap — while an empty list means
    that side has no id at all, so a split's several caps stay readable
    instead of collapsing into one guessed value.
    """
    new_token_caps: list[int | None]
    """The same for the new side.

    A budget is not content identity: the digest names the work, so a
    cap-only change stays ``unchanged`` there. It is an operator-relevant
    constraint on the same node, so it is disclosed here — a revision that
    moves one allowance can never look like no revision at all.
    """
    renamed: bool
    """Same content under a different id: the node was renumbered, not changed."""
    edge_state: EdgeState
    old_attempts: int
    new_attempts: int


class PlanRevision(Model):
    """The old/new DAG diff a recalibration shows before the approval gate."""

    plan_id: str
    from_version: int
    to_version: int
    nodes: list[NodeRevision]
    counts: dict[str, int]
    """Every change category, present even at zero, so a reader never guesses."""
    ambiguous: list[str]
    """Digests the diff refused to name — a rename shared by duplicates, or
    claim multisets that overlap instead of partitioning. Those nodes stay
    honest new plus removed rather than a guessed split."""
    derivation: str


class AllowanceReset(Model):
    """One freshly allocated node's attempt allowance, disclosed at approval.

    Every node a revision newly allocates gets a row, so no fresh allowance
    can be granted unseen. The row says how well the revision knows the
    source it replaces:

    - ``proven``: the sources are exact — a split's or merge's old nodes, or
      the one surviving node whose dropped claims are exactly the child's.
      ``source_ids`` names them and ``consumed_attempts`` is their recorded
      attempt total (``0`` is a recorded fact, not a guess).
    - ``candidates``: several surviving nodes could be the source; every one
      is listed in ``candidate_source_ids`` and ``consumed_attempts`` stays
      ``None`` rather than naming a payer nobody proved.
    - ``unknown``: the child reuses claims some surviving node dropped and
      also carries claims nobody dropped, so no source can be proven; the
      possible contributors are listed in ``candidate_source_ids``.
    - ``new``: no surviving node dropped any of the child's claims — a
      genuinely new allocation, not a reset.
    """

    initiative_id: str
    source_ids: list[str] = []
    """The exact sources; only set when ``source_status`` is ``proven``."""
    consumed_attempts: int | None = None
    """Attempts the sources already spent; ``None`` when not attributable."""
    source_status: Literal["proven", "candidates", "unknown", "new"] = "proven"
    candidate_source_ids: list[str] = []
    """Every possible source, for ``candidates`` and ``unknown`` rows."""


class RevisionImpact(Model):
    """What one revision disturbs, disclosed before the plan is approved."""

    plan_id: str
    from_version: int
    to_version: int
    downstream: list[NodeImpact]
    """Nodes downstream of what the revision touched, in build order."""
    stranded: list[str]
    """Work the revision would leave behind: a node that ran whose recorded
    attempts no longer exist under its lineage. Empty for every revision the
    fold accepts — completed work is never reverted graph-wide."""
    dropped: list[str]
    """Ids the revision took out of the live plan, carried ones excluded."""
    plan_token_cap_from: int | None
    """The plan-wide admission cap before the revision; ``None`` is no cap."""
    plan_token_cap_to: int | None
    """And after it — so a revised plan budget is approved, never assumed."""
    allowance_resets: list[AllowanceReset]
    derivation: str


def _claims(spec: InitiativeSpec) -> "Counter[str]":
    """A node's declared claims: its subtask briefs, with multiplicity."""
    return Counter(spec.subtasks)


def _one_digest(plan: Plan, node_ids: list[str]) -> str | None:
    """The digest a one-id side has; a group of nodes has no single digest."""
    return plan.initiatives[node_ids[0]].spec.digest if len(node_ids) == 1 else None


def _partition(
    target: "Counter[str]", candidates: "dict[str, Counter[str]]"
) -> "list[str] | None":
    """The candidate ids whose claims partition `target` exactly, or None.

    Duplicate-safe and bounded: candidates arrive already restricted to
    non-empty strict sub-multisets, and each one's claims are removed from
    what is left, so a candidate overlapping one already placed makes the
    whole set ambiguous (`None`) instead of letting the diff pick an order.
    Each claim is visited at most once, so there is no subset search to blow
    up: a split that needs one is not a split this diff can honestly name.
    """
    remaining = Counter(target)
    chosen: list[str] = []
    for candidate_id in sorted(candidates):
        claims = candidates[candidate_id]
        if claims & remaining != claims:
            return None
        remaining -= claims
        chosen.append(candidate_id)
    return chosen if len(chosen) >= 2 and not remaining else None


def _edge_state(
    previous: Plan,
    current: Plan,
    old_ids: list[str],
    new_ids: list[str],
    moved: "dict[str, list[str]]",
) -> EdgeState:
    """Whether a node's declared dependencies survived the revision.

    Old `depends_on` entries are translated only through matches the revision
    established — an identity, a rename, or a split/merge partition — and a
    dependency the diff cannot trace to something recorded before, on either
    side, makes the whole node `unresolved` rather than quietly `same`.
    """
    old_deps = {
        dependency
        for node_id in old_ids
        for dependency in previous.initiatives[node_id].spec.depends_on
    }
    new_deps = {
        dependency
        for node_id in new_ids
        for dependency in current.initiatives[node_id].spec.depends_on
    }
    translated: set[str] = set()
    for dependency in old_deps:
        if dependency in moved:
            translated.update(moved[dependency])
        elif dependency in current.initiatives:
            translated.add(dependency)
        else:
            return "unresolved"
    established = set(previous.initiatives) | {
        new_id for ids in moved.values() for new_id in ids
    }
    if any(dependency not in established for dependency in new_deps):
        return "unresolved"
    return "same" if translated == new_deps else "changed"


def _revision_record(
    previous: Plan,
    current: Plan,
    *,
    change: NodeChange,
    old_ids: list[str],
    new_ids: list[str],
    moved: "dict[str, list[str]]",
    renamed: bool = False,
) -> NodeRevision:
    """One classified node or node group, with its edges and attempt counts."""
    return NodeRevision(
        change=change,
        old_ids=sorted(old_ids),
        new_ids=sorted(new_ids),
        old_digest=_one_digest(previous, old_ids),
        new_digest=_one_digest(current, new_ids),
        old_token_caps=[
            previous.initiatives[node_id].spec.token_cap
            for node_id in sorted(old_ids)
        ],
        new_token_caps=[
            current.initiatives[node_id].spec.token_cap
            for node_id in sorted(new_ids)
        ],
        renamed=renamed,
        edge_state=_edge_state(previous, current, old_ids, new_ids, moved),
        old_attempts=sum(
            len(previous.initiatives[node_id].attempts) for node_id in old_ids
        ),
        new_attempts=sum(
            len(current.initiatives[node_id].attempts) for node_id in new_ids
        ),
    )


def plan_revision(previous: Plan, current: Plan) -> PlanRevision:
    """Classify how one plan version moved to the next, purely.

    Identity is content-addressed: the same id and digest is `unchanged`, the
    same id with a new digest is `edited`, and the same digest under a new id
    is a `renamed` node — not a changed one. That distinction is the whole
    point: a merely renumbered node is not new work and not new risk. The
    digest names the work, so the operator-relevant attributes outside it are
    disclosed beside it instead of folded into it: the dependency edges in
    `edge_state`, and the admission caps in `old_token_caps` and
    `new_token_caps`. Only
    unmatched nodes can be `split` or `merged`, and only from a unique
    disjoint claim-multiset partition; every other unmatched node stays
    honest `new` plus `removed`, with the digests it could not reconcile
    listed in `ambiguous`.
    """
    if previous.id != current.id:
        raise ValueError(f"plan {current.id} cannot be revised against {previous.id}")
    if previous.version >= current.version:
        raise ValueError(
            f"plan revision needs a later version than {previous.version}, "
            + f"got {current.version}"
        )
    old_live = previous.initiatives
    new_live = current.initiatives
    matched_old: set[str] = set()
    matched_new: set[str] = set()
    groups: list[tuple[NodeChange, list[str], list[str], bool]] = []
    moved: dict[str, list[str]] = {}
    ambiguous: set[str] = set()

    for node_id in sorted(old_live.keys() & new_live.keys()):
        before = old_live[node_id].spec
        after = new_live[node_id].spec
        matched_old.add(node_id)
        matched_new.add(node_id)
        moved[node_id] = [node_id]
        groups.append(
            ("unchanged" if before.digest == after.digest else "edited", [node_id], [node_id], False)
        )

    # A different id with identical content is the same node renumbered.
    old_by_digest: dict[str, list[str]] = {}
    for node_id in sorted(set(old_live) - matched_old):
        old_by_digest.setdefault(old_live[node_id].spec.digest, []).append(node_id)
    new_by_digest: dict[str, list[str]] = {}
    for node_id in sorted(set(new_live) - matched_new):
        new_by_digest.setdefault(new_live[node_id].spec.digest, []).append(node_id)
    for digest in sorted(set(old_by_digest) & set(new_by_digest)):
        olds = old_by_digest[digest]
        news = new_by_digest[digest]
        if len(olds) > 1 or len(news) > 1:
            ambiguous.add(digest)  # never guess which duplicate moved where
            continue
        matched_old.add(olds[0])
        matched_new.add(news[0])
        moved[olds[0]] = [news[0]]
        groups.append(("unchanged", [olds[0]], [news[0]], True))

    old_claims = {
        node_id: _claims(old_live[node_id].spec)
        for node_id in sorted(set(old_live) - matched_old)
    }
    new_claims = {
        node_id: _claims(new_live[node_id].spec)
        for node_id in sorted(set(new_live) - matched_new)
    }
    taken_old: set[str] = set()
    taken_new: set[str] = set()

    # One dropped node whose claims two or more new nodes partition exactly.
    for old_id in sorted(old_claims):
        if old_id in taken_old:
            continue
        target = old_claims[old_id]
        subsets = {
            node_id: claims
            for node_id, claims in new_claims.items()
            if claims and claims < target
        }
        chosen = (
            _partition(target, subsets) if len(subsets) >= 2 and not set(subsets) & taken_new else None
        )
        if chosen is None:
            if subsets:
                ambiguous.add(old_live[old_id].spec.digest)
                ambiguous.update(new_live[node_id].spec.digest for node_id in subsets)
            continue
        taken_old.add(old_id)
        taken_new.update(chosen)
        moved[old_id] = chosen
        groups.append(("split", [old_id], chosen, False))

    # Several dropped nodes whose claims one new node covers exactly.
    for new_id in sorted(new_claims):
        target = new_claims[new_id]
        if not target or new_id in taken_new:
            continue
        subsets = {
            node_id: claims
            for node_id, claims in old_claims.items()
            if claims and claims < target
        }
        chosen = (
            _partition(target, subsets) if len(subsets) >= 2 and not set(subsets) & taken_old else None
        )
        if chosen is None:
            if subsets:
                ambiguous.add(new_live[new_id].spec.digest)
                ambiguous.update(old_live[node_id].spec.digest for node_id in subsets)
            continue
        taken_old.update(chosen)
        taken_new.add(new_id)
        for node_id in chosen:
            moved[node_id] = [new_id]
        groups.append(("merged", chosen, [new_id], False))

    for node_id in sorted(set(old_live) - matched_old - taken_old):
        groups.append(("removed", [node_id], [], False))
    for node_id in sorted(set(new_live) - matched_new - taken_new):
        groups.append(("new", [], [node_id], False))

    records = [
        _revision_record(
            previous,
            current,
            change=change,
            old_ids=old_ids,
            new_ids=new_ids,
            moved=moved,
            renamed=renamed,
        )
        for change, old_ids, new_ids, renamed in groups
    ]
    records.sort(key=lambda record: (record.old_ids or record.new_ids, record.new_ids))
    counts: dict[str, int] = {
        change: 0
        for change in ("unchanged", "edited", "split", "merged", "new", "removed")
    }
    for record in records:
        counts[record.change] += 1
    return PlanRevision(
        plan_id=current.id,
        from_version=previous.version,
        to_version=current.version,
        nodes=records,
        counts=counts,
        ambiguous=sorted(ambiguous),
        derivation=(
            "content-addressed identity: same id and digest is unchanged, same "
            + "id with a new digest is edited, the same digest under a new id "
            + "is a rename, and only a unique disjoint claim-multiset "
            + "partition names split or merged — everything else is honest "
            + "new plus removed"
        ),
    )


def _allowance_resets(
    previous: Plan, current: Plan, revision: PlanRevision
) -> list[AllowanceReset]:
    """Every newly allocated node's allowance, with the best honest source.

    Split and merge children name their sources exactly. Same-id residual
    extraction does not: the anchor keeps its id while the child arrives as
    `new`, so the only link is the claims themselves. A child whose claims
    are a non-empty sub-multiset of one surviving node's dropped claims is a
    proven reset; several such nodes make the payer a candidate list; a child
    mixing dropped and new claims is an unknown partial attribution; and a
    child reusing nobody's dropped claims is a plain new allocation. Unknown
    and candidate rows are disclosed with `consumed_attempts` left unset —
    never omitted, never guessed to zero.
    """
    resets = [
        AllowanceReset(
            initiative_id=node_id,
            source_ids=sorted(record.old_ids),
            consumed_attempts=sum(
                len(previous.initiatives[source].attempts)
                for source in record.old_ids
            ),
            source_status="proven",
        )
        for record in revision.nodes
        if record.change in {"split", "merged"}
        for node_id in record.new_ids
    ]
    children = {
        node_id: _claims(current.initiatives[node_id].spec)
        for record in revision.nodes
        if record.change == "new"
        for node_id in record.new_ids
    }
    # ponytail: multiset containment over surviving same-id nodes; add
    # explicit lineage fields if revisions must be reconstructible from the
    # event stream alone.
    if children:
        removed: dict[str, "Counter[str]"] = {}
        for node_id in sorted(set(previous.initiatives) & set(current.initiatives)):
            dropped = _claims(previous.initiatives[node_id].spec) - _claims(
                current.initiatives[node_id].spec
            )
            if dropped:
                removed[node_id] = dropped
        for node_id in sorted(children):
            claims = children[node_id]
            containing = [
                source_id
                for source_id, dropped in removed.items()
                if claims and claims <= dropped
            ]
            if len(containing) == 1:
                source = containing[0]
                resets.append(
                    AllowanceReset(
                        initiative_id=node_id,
                        source_ids=[source],
                        consumed_attempts=len(previous.initiatives[source].attempts),
                        source_status="proven",
                    )
                )
                continue
            if len(containing) > 1:
                resets.append(
                    AllowanceReset(
                        initiative_id=node_id,
                        source_status="candidates",
                        candidate_source_ids=containing,
                    )
                )
                continue
            partial = sorted(
                source_id
                for source_id, dropped in removed.items()
                if claims and claims & dropped
            )
            if partial:
                resets.append(
                    AllowanceReset(
                        initiative_id=node_id,
                        source_status="unknown",
                        candidate_source_ids=partial,
                    )
                )
                continue
            resets.append(
                AllowanceReset(initiative_id=node_id, source_status="new")
            )
    return sorted(resets, key=lambda reset: reset.initiative_id)


def revision_impact(
    previous: Plan, current: Plan, revision: PlanRevision | None = None
) -> RevisionImpact:
    """What a revision disturbs: its downstream, its drop, its resets, purely.

    Disclosed before approval, because the recalibration diff is the operator's
    two-minute decision. A revision cannot strand completed work: a node the
    fold re-declares keeps its attempts under its own or a carried id, and a
    dropped node's attempts move with it into `Plan.retired`, so `stranded` is
    empty for every revision the fold accepts — a non-empty entry means
    recorded work was lost.

    Budgets ride along: the plan-wide cap and every node's are disclosed as
    from/to values, because a revision that only moves an allowance leaves no
    digest behind to notice and is still spending the operator's tokens.
    """
    revision = revision or plan_revision(previous, current)
    graph = graph_of(current)
    roots = {
        node_id
        for record in revision.nodes
        if record.change != "unchanged" or record.edge_state != "same"
        for node_id in record.new_ids
        if node_id in current.initiatives
    }
    below: set[str] = set()
    for node_id in roots:
        below |= _descendants(graph, node_id)
    order = [
        node_id for node_id in nx.topological_sort(graph) if node_id in below
    ]
    kept: dict[str, set[str]] = {}
    for initiative in [*current.initiatives.values(), *current.retired]:
        for known in initiative.known_ids:
            kept.setdefault(known, set()).update(
                attempt.id for attempt in initiative.attempts
            )
    carried_ids = {
        known
        for initiative in current.initiatives.values()
        for known in initiative.known_ids
    }
    return RevisionImpact(
        plan_id=current.id,
        from_version=revision.from_version,
        to_version=revision.to_version,
        downstream=[
            NodeImpact(
                initiative_id=node_id,
                state=current.initiatives[node_id].state,
                attempts=len(current.initiatives[node_id].attempts),
            )
            for node_id in order
        ],
        stranded=sorted(
            node_id
            for node_id, initiative in previous.initiatives.items()
            if initiative.attempts
            and not {attempt.id for attempt in initiative.attempts}
            <= kept.get(node_id, set())
        ),
        dropped=sorted(
            node_id
            for node_id in previous.initiatives
            if node_id not in current.initiatives and node_id not in carried_ids
        ),
        plan_token_cap_from=previous.token_cap,
        plan_token_cap_to=current.token_cap,
        allowance_resets=_allowance_resets(previous, current, revision),
        derivation=(
            "current-graph descendants of every revised node, compared against "
            + "the previous plan's recorded attempts; every newly allocated "
            + "node discloses its fresh allowance — exact sources and consumed "
            + "attempts where the revision proves them, candidate or unknown "
            + "sources when it cannot, and an explicit new allocation for work "
            + "no surviving node dropped; both plan-wide and per-node admission "
            + "caps are disclosed as from/to values, since a budget is not part "
            + "of content-addressed identity"
        ),
    )


__all__ = [
    "AllowanceReset",
    "Contention",
    "DownstreamImpact",
    "EdgeState",
    "NodeChange",
    "NodeImpact",
    "NodeRevision",
    "NodeRisk",
    "NodeStatus",
    "Overhead",
    "PlanGraph",
    "PlanRevision",
    "RevisionImpact",
    "RiskReport",
    "ScopeTrie",
    "TARGET_OVERHEAD_RATIO",
    "ancestor_patches",
    "conflicts_with",
    "contention",
    "critical_path",
    "downstream_impact",
    "graph_of",
    "max_concurrency",
    "overhead",
    "plan_graph",
    "plan_revision",
    "revision_impact",
    "risk_report",
]
