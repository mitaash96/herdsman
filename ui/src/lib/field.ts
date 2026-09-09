/**
 * The Contention Field's model: one plan's DAG resolved into ruled lanes.
 *
 * The lanes are not a schedule, and the difference is the whole honesty of
 * this view. A lane is a **chain** — a sequence of initiatives each of which
 * transitively depends on the one before it, so they can never overlap. By
 * Dilworth's theorem the smallest number of such chains equals the largest
 * antichain, which is exactly the `max_concurrency` the daemon computes in
 * `herdsman/graph.py`. So the lane count is a structural fact about the plan
 * (the most agents it can ever keep busy), not a guess about when work runs,
 * and `agrees` re-checks that claim against the daemon on every projection.
 *
 * Horizontal position is `depth`: the longest ancestor chain above a member,
 * which is the earliest rank it could possibly occupy. Also structure, also
 * not a time.
 *
 * Everything here is pure. `demo()` at the bottom is the runnable check.
 */

import type { Contention, NodeStatus, PlanGraph, RiskReport } from './daemon';

/** The shell's member vocabulary (`app.css`), which the domain maps onto exactly. */
export type MemberState = 'slack' | 'balanced' | 'loaded' | 'seated' | 'failed';

export interface Member {
	node: NodeStatus;
	/** Row in the field. Index into `lanes`. */
	lane: number;
	/** Column in the field: the longest dependency chain above this member. */
	depth: number;
	state: MemberState;
	/** `cancelled` has no member state of its own; it is slack that was struck out. */
	cancelled: boolean;
	onCriticalPath: boolean;
	/** Dependencies that have not settled — the counterforce holding this one. */
	blockedBy: string[];
}

export interface Field {
	members: Member[];
	/** Reading order: lane, then depth. The register renders exactly this. */
	byId: Map<string, Member>;
	lanes: Member[][];
	/** Column count; `depth` runs 0 … columns-1. */
	columns: number;
	/** Edges that leave their lane. Same-lane order is carried by the lane's own run. */
	crossings: { from: Member; to: Member }[];
	/** The critical path's members in order, when every id resolves. */
	criticalPath: Member[];
	/**
	 * Whether the chain cover matches the daemon's `max_concurrency`. False is a
	 * bug in this file, never a property of the plan — the view says so rather
	 * than drawing a lane count it cannot justify.
	 */
	agrees: boolean;
}

/** Contention that touches one member, with the peer resolved. */
export interface Touch {
	peer: string;
	kind: Contention['kind'];
	paths: string[];
	/** For `write_read`, true when this member is the one that writes. */
	writes: boolean;
}

function topological(ids: string[], edges: [string, string][]): string[] {
	const indegree = new Map(ids.map((id) => [id, 0]));
	const out = new Map<string, string[]>(ids.map((id) => [id, []]));
	for (const [u, v] of edges) {
		if (!indegree.has(u) || !indegree.has(v)) continue;
		out.get(u)!.push(v);
		indegree.set(v, indegree.get(v)! + 1);
	}
	// Seeded in the plan's own initiative order, so a tie never reshuffles
	// between two reads of the same plan.
	const queue = ids.filter((id) => indegree.get(id) === 0);
	const order: string[] = [];
	for (let i = 0; i < queue.length; i++) {
		const id = queue[i];
		order.push(id);
		for (const next of out.get(id)!) {
			const left = indegree.get(next)! - 1;
			indegree.set(next, left);
			if (left === 0) queue.push(next);
		}
	}
	// The daemon rejects a cyclic proposal at fold time, so this cannot happen
	// against a real plan; appending the remainder keeps a corrupt read
	// drawable instead of throwing inside a render.
	for (const id of ids) if (!order.includes(id)) order.push(id);
	return order;
}

/**
 * Everything each node can reach, transitively.
 *
 * ponytail: sets of ids, O(n²) memory. A plan is 5–30 initiatives and the
 * daemon itself runs `transitive_closure_dag` per request; swap in bitsets if
 * a plan ever reaches the thousands.
 */
function reachability(order: string[], edges: [string, string][]): Map<string, Set<string>> {
	const successors = new Map<string, string[]>(order.map((id) => [id, []]));
	for (const [u, v] of edges) if (successors.has(u) && successors.has(v)) successors.get(u)!.push(v);
	const reach = new Map<string, Set<string>>();
	for (const id of [...order].reverse()) {
		const set = new Set<string>();
		for (const next of successors.get(id)!) {
			set.add(next);
			for (const far of reach.get(next) ?? []) set.add(far);
		}
		reach.set(id, set);
	}
	return reach;
}

/**
 * A minimum chain cover, as a maximum bipartite matching over the closure
 * (Kuhn's augmenting paths). Chains = n − |matching|, which is Dilworth's
 * theorem and the same identity `max_concurrency` is computed from.
 *
 * The critical path is offered to the matching first so it tends to land in
 * one lane and draw as a single horizontal run. Augmentation may still take
 * those pairs apart; a maximum matching is the guarantee, that layout is not.
 */
function chains(
	order: string[],
	reach: Map<string, Set<string>>,
	criticalPath: string[]
): string[][] {
	const next = new Map<string, string>(); // u -> its successor in a chain
	const previous = new Map<string, string>(); // v -> its predecessor

	const augment = (u: string, seen: Set<string>): boolean => {
		for (const v of reach.get(u) ?? []) {
			if (seen.has(v)) continue;
			seen.add(v);
			const held = previous.get(v);
			if (held === undefined || augment(held, seen)) {
				if (held !== undefined) next.delete(held);
				previous.set(v, u);
				next.set(u, v);
				return true;
			}
		}
		return false;
	};

	for (let i = 0; i + 1 < criticalPath.length; i++) {
		const [u, v] = [criticalPath[i], criticalPath[i + 1]];
		if (!reach.has(u) || previous.has(v) || next.has(u)) continue;
		previous.set(v, u);
		next.set(u, v);
	}
	for (const u of order) if (!next.has(u)) augment(u, new Set());

	const heads = order.filter((id) => !previous.has(id));
	return heads.map((head) => {
		const chain = [head];
		for (let at = next.get(head); at !== undefined; at = next.get(at)) chain.push(at);
		return chain;
	});
}

function memberState(node: NodeStatus): MemberState {
	switch (node.state) {
		case 'settled':
			return 'seated'; // load transferred, permanently in the structure
		case 'running':
			return 'loaded'; // under load right now
		case 'failed':
			return 'failed'; // the load path is discontinuous
		case 'pending':
			// Readiness is the daemon's, computed from the folded plan: a ready
			// member's load path is complete and simply unloaded; a blocked one
			// is still hanging slack.
			return node.ready ? 'balanced' : 'slack';
		default:
			return 'slack'; // cancelled, and anything a later sprint adds
	}
}

export function buildField(graph: PlanGraph): Field {
	const ids = graph.nodes.map((n) => n.initiative_id);
	const order = topological(ids, graph.edges);
	const rank = new Map(order.map((id, i) => [id, i]));
	const reach = reachability(order, graph.edges);

	const depth = new Map<string, number>(ids.map((id) => [id, 0]));
	for (const id of order) {
		const node = graph.nodes.find((n) => n.initiative_id === id)!;
		for (const dependency of node.depends_on) {
			const above = depth.get(dependency);
			if (above !== undefined) depth.set(id, Math.max(depth.get(id)!, above + 1));
		}
	}

	const settled = new Set(graph.nodes.filter((n) => n.state === 'settled').map((n) => n.initiative_id));
	const onPath = new Set(graph.critical_path);
	const cover = chains(order, reach, graph.critical_path);

	// The lane carrying most of the critical path leads, so the plan's floor on
	// wall-clock time is the first run the operator reads.
	const ranked = cover
		.map((chain) => ({
			chain,
			shared: chain.filter((id) => onPath.has(id)).length,
			head: rank.get(chain[0]) ?? 0
		}))
		.sort((a, b) => b.shared - a.shared || b.chain.length - a.chain.length || a.head - b.head);

	const byId = new Map<string, Member>();
	const lanes: Member[][] = ranked.map(({ chain }, lane) => {
		const seats = chain
			.map((id) => {
				const node = graph.nodes.find((n) => n.initiative_id === id)!;
				const member: Member = {
					node,
					lane,
					depth: depth.get(id)!,
					state: memberState(node),
					cancelled: node.state === 'cancelled',
					onCriticalPath: onPath.has(id),
					blockedBy: node.depends_on.filter((d) => !settled.has(d))
				};
				byId.set(id, member);
				return member;
			})
			.sort((a, b) => a.depth - b.depth);
		return seats;
	});

	const crossings: Field['crossings'] = [];
	for (const [u, v] of graph.edges) {
		const from = byId.get(u);
		const to = byId.get(v);
		if (from && to && from.lane !== to.lane) crossings.push({ from, to });
	}

	return {
		members: lanes.flat(),
		byId,
		lanes,
		columns: Math.max(1, ...ids.map((id) => depth.get(id)! + 1)),
		crossings,
		criticalPath: graph.critical_path.map((id) => byId.get(id)).filter((m): m is Member => !!m),
		agrees: lanes.length === graph.max_concurrency
	};
}

/**
 * Contention indexed per initiative, so the schedule can name a member's peers
 * without rescanning the report. Absent risk data yields an empty index, which
 * the view must render as *unread*, never as *none*.
 */
export function contentionIndex(risk: RiskReport | null): Map<string, Touch[]> {
	const index = new Map<string, Touch[]>();
	if (!risk) return index;
	const add = (id: string, touch: Touch) => {
		const found = index.get(id);
		if (found) found.push(touch);
		else index.set(id, [touch]);
	};
	for (const item of [...risk.conflicts, ...risk.suggested_edges]) {
		const [a, b] = item.initiatives;
		add(a, { peer: b, kind: item.kind, paths: item.paths, writes: item.writer === a });
		add(b, { peer: a, kind: item.kind, paths: item.paths, writes: item.writer === b });
	}
	return index;
}

/** Where a run stands, derived — not a control and not the approval gate. */
export function phaseOf(graph: PlanGraph): 'proposed' | 'running' | 'settled' {
	if (graph.approval !== 'approved') return 'proposed';
	const done = (n: NodeStatus) => n.state === 'settled' || n.state === 'cancelled';
	return graph.nodes.length > 0 && graph.nodes.every(done) ? 'settled' : 'running';
}

/** Roving-tabindex step over an ordered id list. Shared by the field and the schedule. */
export function step(order: string[], from: string | null, by: number): string | null {
	if (order.length === 0) return null;
	const at = from === null ? -1 : order.indexOf(from);
	if (at === -1) return order[by > 0 ? 0 : order.length - 1];
	return order[Math.min(order.length - 1, Math.max(0, at + by))];
}
