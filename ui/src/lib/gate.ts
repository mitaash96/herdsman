/**
 * The approval gate's model: what a proposed revision costs, permits and risks.
 *
 * R1's `field.ts` answers *where* each initiative sits. This answers the
 * different question the gate asks: given this decomposition, what am I
 * agreeing to? Nothing here is a schedule, a duration or a limit — the
 * substrate has no estimate of any of the three before a run, and saying so is
 * part of the model rather than a caption bolted on at the view.
 *
 * Two derivations carry the weight:
 *
 * - **Callouts.** `risk_report` already computes conflicts, suggested edges and
 *   per-node articulation; this ranks them into what the daemon will *refuse*
 *   (a write/write pair the lanes permit but dispatch will not), what the shape
 *   of the plan makes structurally expensive (a member everything waits behind),
 *   and what is merely advisory (an unordered read of somebody's write).
 * - **Rationale.** No initiative carries a "why". What it carries is `routes`
 *   and `depends_on`, and where a dependency's writes meet this one's reads,
 *   the reason for the edge is a fact rather than a guess. Where they do not
 *   meet, this says the edge is declared and stops — it never invents a motive.
 *
 * Everything here is pure. `demo()` in `dev/field-check.ts` is the runnable check.
 */

import type { Initiative, Plan, PlanGraph, RiskReport, Usage } from './daemon';
import type { Member } from './field';

/** Hard limits first, then structure, then advice. Rendered in this order. */
export type CalloutKind = 'scope' | 'choke' | 'overlap';

export interface Callout {
	kind: CalloutKind;
	/** The member states this build already has. Only `scope` is a real break. */
	state: 'failed' | 'balanced' | 'slack';
	/** Initiatives this callout is about; the view links each one. */
	about: string[];
	/** The sentence, written for an operator deciding in two minutes. */
	text: string;
	/** Advisory callouts are counted and ranked below the hard ones. */
	advisory: boolean;
}

export interface RegisterRow {
	member: Member;
	/** Planner-authored, from the folded plan. `null` when that read failed. */
	spec: Initiative['spec'] | null;
	/** The first paragraph of the brief; the drawer holds the rest. */
	lead: string | null;
	/** True when the brief has more than the one paragraph shown here. */
	truncated: boolean;
	/** Why this member waits, stated only as far as `routes` actually says. */
	because: string | null;
}

export interface Shape {
	members: number;
	/** The chain cover: the most agents this plan can ever keep busy. */
	lanes: number;
	/**
	 * Members on the longest chain — the same count, in the same unit, that
	 * R1's Critical path readout shows a few centimetres to the left. Two
	 * numbers for one fact on one screen is a defect however honest each is.
	 */
	chain: number;
	/** Members that become runnable the moment this revision is approved. */
	readyOnApproval: number;
}

/** Planning cost, the one token figure a proposed plan has. */
export interface Budget {
	/** `null` when the planner reported nothing — unknown, never zero. */
	planning: Usage | null;
	/** Total of the two counters, for the readout. */
	planningTokens: number | null;
}

export function shapeOf(graph: PlanGraph, lanes: number): Shape {
	return {
		members: graph.nodes.length,
		lanes,
		chain: graph.critical_path.length,
		readyOnApproval: graph.nodes.filter((n) => n.ready).length
	};
}

export function budgetOf(plan: Plan | null): Budget {
	const planning = plan?.planner_usage ?? null;
	return {
		planning,
		planningTokens: planning ? planning.input_tokens + planning.output_tokens : null
	};
}

/** How a usage figure was arrived at. Provenance is never dropped. */
export const PROVENANCE: Record<Usage['source'], string> = {
	harness: 'reported by the harness that ran the planner',
	provider: 'reported by the model provider, not the harness',
	estimate: 'estimated — not a measurement of anything'
};

function list(ids: string[]): string {
	if (ids.length <= 1) return ids.join('');
	if (ids.length === 2) return `${ids[0]} and ${ids[1]}`;
	return `${ids.slice(0, -1).join(', ')} and ${ids[ids.length - 1]}`;
}

/** At most three paths in a sentence; a shared directory can hold dozens. */
function paths(all: string[]): string {
	const shown = all.slice(0, 3).join(', ');
	return all.length > 3 ? `${shown} and ${all.length - 3} more` : shown;
}

/**
 * The callouts, ranked. Returns `null` when the risk report did not answer —
 * unread is not the same as none, and the view has to be able to say which.
 */
export function calloutsOf(
	graph: PlanGraph,
	risk: RiskReport | null,
	blocking: Map<string, number>
): Callout[] | null {
	if (!risk) return null;
	const callouts: Callout[] = [];

	// A hard limit: the lanes say these two may run at once and the daemon will
	// refuse to start the second (`run_initiative` rejects a contending writer).
	for (const conflict of risk.conflicts) {
		callouts.push({
			kind: 'scope',
			state: 'failed',
			about: [...conflict.initiatives],
			advisory: false,
			text:
				`${list([...conflict.initiatives])} both write ${paths(conflict.paths)}. ` +
				'Nothing orders them, so the lanes allow them at once — but the daemon ' +
				'refuses to start the second while the first is running, and the plan ' +
				'will not reach the parallelism its lane count promises.'
		});
	}

	/* Structure, not a fault: the member everything downstream waits behind.
	   `articulation` is the daemon's own word for it, but in any tail chain
	   every remaining node articulates, so listing them all is noise dressed as
	   risk. The signal an operator actually wants is the single worst one —
	   which member, if it stalls, stops the most work — so this reports the
	   maximum and whatever ties it, and stays quiet below two members. */
	const chokes = risk.nodes.filter((n) => n.articulation && (blocking.get(n.initiative_id) ?? 0) >= 2);
	const worst = Math.max(0, ...chokes.map((n) => blocking.get(n.initiative_id) ?? 0));
	for (const node of chokes) {
		const behind = blocking.get(node.initiative_id) ?? 0;
		if (behind < worst) continue;
		callouts.push({
			kind: 'choke',
			state: 'balanced',
			about: [node.initiative_id],
			advisory: false,
			text:
				`${node.initiative_id} gates ${behind} of the ${graph.nodes.length} members, ` +
				'and every path through them crosses it. Nothing below it starts until it ' +
				'settles, so its assignment carries more of this plan than its own scope suggests.'
		});
	}

	// Advisory: one shared file can suggest a dozen of these, which is exactly
	// why they are counted, ranked below, and never dressed as blockers.
	for (const edge of risk.suggested_edges) {
		const reader = edge.reader ?? edge.initiatives[0];
		const writer = edge.writer ?? edge.initiatives[1];
		callouts.push({
			kind: 'overlap',
			state: 'slack',
			about: [...edge.initiatives],
			advisory: true,
			text:
				`${reader} reads ${paths(edge.paths)}, which ${writer} writes, and no ` +
				'dependency orders them. Whether that matters depends on what the ' +
				'write does; the planner did not say.'
		});
	}

	return callouts;
}

/**
 * How many members sit downstream of each one, transitively.
 *
 * `NodeRisk.blast_radius` is the daemon's own count, but the risk report may
 * not have answered while the graph did, and a chokepoint reads off the graph
 * alone. This is the same number computed from the edges the field is drawn
 * from, so the two never disagree on screen.
 *
 * ponytail: O(n·e) over a 5–30 member plan, recomputed per projection. Memoise
 * if a plan ever reaches the thousands — `field.ts` says the same of its own.
 */
export function downstream(graph: PlanGraph): Map<string, number> {
	const out = new Map<string, string[]>(graph.nodes.map((n) => [n.initiative_id, []]));
	for (const [from, to] of graph.edges) out.get(from)?.push(to);
	const counts = new Map<string, number>();
	for (const node of graph.nodes) {
		const seen = new Set<string>();
		const queue = [...(out.get(node.initiative_id) ?? [])];
		while (queue.length > 0) {
			const next = queue.pop()!;
			if (seen.has(next)) continue;
			seen.add(next);
			queue.push(...(out.get(next) ?? []));
		}
		counts.set(node.initiative_id, seen.size);
	}
	return counts;
}

/**
 * Why this member waits on the ones it does — as far as `routes` actually says.
 *
 * A dependency whose writes this member reads has a reason recorded in the
 * plan. One whose writes it does not read has an edge the planner declared and
 * no recorded reason, and that is what this returns: the fact, not a motive
 * invented to fill the sentence.
 */
export function because(spec: Initiative['spec'], plan: Plan | null): string | null {
	const deps = spec.depends_on;
	if (deps.length === 0) return null;
	if (!plan) return `waits on ${list([...deps])}`;
	const reads = new Set(spec.routes.reads);
	const explained = deps.filter((id) => {
		const writes = plan.initiatives[id]?.spec.routes.writes ?? [];
		return writes.some((path) => reads.has(path));
	});
	if (explained.length === deps.length) {
		return `waits on ${list([...deps])} — reads what ${deps.length === 1 ? 'it writes' : 'they write'}`;
	}
	if (explained.length === 0) {
		return `waits on ${list([...deps])} — declared, with no shared path to explain it`;
	}
	const rest = deps.filter((id) => !explained.includes(id));
	return (
		`waits on ${list([...deps])} — reads what ${list(explained)} ` +
		`write${explained.length === 1 ? 's' : ''}; ${list(rest)} ` +
		`${rest.length === 1 ? 'is' : 'are'} declared with no shared path`
	);
}

/** The lead paragraph of a brief, and whether anything was left behind. */
export function lead(brief: string): { lead: string; truncated: boolean } {
	const paragraphs = brief
		.split(/\n\s*\n/)
		.map((p) => p.trim())
		.filter(Boolean);
	if (paragraphs.length === 0) return { lead: '', truncated: false };
	return { lead: paragraphs[0], truncated: paragraphs.length > 1 };
}

/**
 * The register: every member in the field's own order, carrying what the
 * planner wrote rather than where it sits. The two renderings share one order
 * and one selection, so the register is a way into a member and never a
 * second, disagreeing list.
 */
export function registerOf(members: Member[], plan: Plan | null): RegisterRow[] {
	return members.map((member) => {
		const spec = plan?.initiatives[member.node.initiative_id]?.spec ?? null;
		const brief = spec ? lead(spec.brief) : null;
		return {
			member,
			spec,
			lead: brief ? brief.lead : null,
			truncated: brief ? brief.truncated : false,
			because: spec ? because(spec, plan) : null
		};
	});
}
