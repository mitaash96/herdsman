/**
 * The Load Bank's pure model: what each run's member is drawn from.
 *
 * Every function here is total and takes its inputs, so `dev/field-check.ts`
 * can assert the claims the drawing rests on without a browser. The claims
 * are: a member's segments cover the run exactly once, load is drawn at the
 * left and slack at the right, and no figure this file returns turns an
 * unknown into a zero.
 *
 * It imports nothing at runtime — `daemon.ts`'s types are erased — which is
 * what lets the check run under bare `node`.
 */

import type { AttentionItem, Fleet, FleetSpend, RunRollup, RunSpend } from './daemon';

/**
 * The member vocabulary, in the order a member is drawn left to right.
 *
 * Load first, slack last: a member takes up load from its left end, which is
 * also `take-up-load`'s transform origin. `failed` sits inside the loaded
 * stretch rather than after it, because a break in the load path is where the
 * load stopped, not something waiting behind it.
 */
export const SEGMENT_ORDER = [
	'settled',
	'running',
	'failed',
	'paused',
	'pending',
	'cancelled'
] as const;

export type SegmentState = (typeof SEGMENT_ORDER)[number];

/**
 * How each initiative state is drawn on the member.
 *
 * Weight is the signal and colour never carries it alone, exactly as the
 * member states do elsewhere: `load` is the heavy run a member under load is
 * drawn at, `held` the lighter run of work in place but not loaded, `none`
 * the hairline of work carrying nothing. A reader with no colour still sees
 * how much of the fleet is carrying.
 */
export const SEGMENT_WEIGHT: Record<SegmentState, 'load' | 'held' | 'none'> = {
	settled: 'load',
	running: 'load',
	failed: 'load',
	paused: 'held',
	pending: 'none',
	cancelled: 'none'
};

/** What a state is called on screen. The operator's word, not the fold's. */
export const SEGMENT_NAME: Record<SegmentState, string> = {
	settled: 'settled',
	running: 'running',
	failed: 'failed',
	paused: 'paused',
	pending: 'not started',
	cancelled: 'cancelled'
};

export interface Segment {
	state: SegmentState;
	count: number;
	/** Share of the run's initiatives, 0–1. Shares sum to 1 across segments. */
	share: number;
}

/**
 * One member's segments, longest-lived load first and slack last.
 *
 * States with no initiatives are dropped rather than drawn at zero width: a
 * segment that is there but invisible is a hairline that means nothing.
 */
export function segmentsOf(counts: Record<string, number>, total: number): Segment[] {
	if (total <= 0) return [];
	return SEGMENT_ORDER.map((state) => ({
		state,
		count: counts[state] ?? 0,
		share: (counts[state] ?? 0) / total
	})).filter((segment) => segment.count > 0);
}

/**
 * The share of a run that has taken up load: settled, running or broken.
 *
 * Not the same as `RunRollup.progress`, which is settled over total. This is
 * how much of the member is drawn heavy, and it is what the one authored
 * motion plays on.
 */
export function loadedShare(counts: Record<string, number>, total: number): number {
	if (total <= 0) return 0;
	const loaded = SEGMENT_ORDER.filter((state) => SEGMENT_WEIGHT[state] === 'load').reduce(
		(sum, state) => sum + (counts[state] ?? 0),
		0
	);
	return loaded / total;
}

/**
 * The status word, and the member state that word is set in.
 *
 * `awaiting_approval` is slack rather than loaded: a plan nobody approved is
 * seated in the structure and carrying nothing. `failed` is the only status
 * that takes the broken state, because a run with one failed initiative and
 * others still running is reported as `running` by the daemon's own
 * precedence, and this build does not get a second opinion.
 */
export function statusOf(status: string): { word: string; state: string } {
	switch (status) {
		case 'awaiting_approval':
			return { word: 'awaiting approval', state: 'slack' };
		case 'running':
			return { word: 'running', state: 'loaded' };
		case 'settled':
			return { word: 'settled', state: 'seated' };
		case 'failed':
			return { word: 'failed', state: 'failed' };
		case 'paused':
			return { word: 'paused', state: 'balanced' };
		case 'idle':
			return { word: 'idle', state: 'balanced' };
		case 'empty':
			return { word: 'no initiatives', state: 'slack' };
		default:
			// A newer daemon's status still reads, and is not claimed as loaded.
			return { word: status.replace(/_/g, ' '), state: 'balanced' };
	}
}

/**
 * What this run needs from the user, and the one place to go and look.
 *
 * Home counts; it does not list. The attention feed, its per-kind actions and
 * its ordering are H2's, and building a second one here would leave two
 * surfaces to keep in step. What the overview owes is a precise link, so the
 * oldest blocking item's own deep link is carried through — the daemon
 * already built it, and it addresses the initiative or checkpoint, not just
 * the plan.
 */
export function needsUser(run: RunRollup): {
	count: number;
	oldest: AttentionItem | null;
	/** True when the daemon sent no attention at all: unknown, not none. */
	unknown: boolean;
} {
	const items = run.attention;
	if (items === undefined) return { count: 0, oldest: null, unknown: true };
	const blocking = items.filter((item) => item.blocking);
	return { count: blocking.length, oldest: blocking[0] ?? null, unknown: false };
}

/** What one attention kind is called where it is counted, not listed. */
export function kindName(kind: string): string {
	switch (kind) {
		case 'plan_gate':
			return 'a plan waiting for approval';
		case 'checkpoint_review':
			return 'a checkpoint waiting for review';
		case 'blocked_on_user':
			return 'an agent waiting on an answer';
		case 'failed':
			return 'a failed initiative';
		case 'stalled':
			return 'a silent attempt';
		default:
			return kind.replace(/_/g, ' ');
	}
}

/**
 * A token count in the width a readout cell has.
 *
 * Tabular by inheritance, so 9.9k and 41.2k align in a column. Never rounds a
 * small number away: under ten thousand the exact figure fits and is shown.
 */
export function tokens(value: number): string {
	if (value < 10_000) return value.toLocaleString('en-US');
	if (value < 1_000_000) return `${(value / 1000).toFixed(1)}k`;
	return `${(value / 1_000_000).toFixed(2)}M`;
}

export interface SpendReading {
	/** The figure, or `null` when nothing has been measured. */
	value: string | null;
	/** Why there is no figure, or how this one was measured. */
	gloss: string;
	/** What is left of a declared cap, or `null` when none is declared. */
	available: string | null;
}

/** What a phase is called where it is read. The operator's word, not the fold's. */
export const PHASE_WORD: Record<string, string> = {
	actual: 'measured',
	preflight: 'preflight',
	estimate: 'estimated'
};

/**
 * One run's or the fleet's spend, with the provenance of every figure.
 *
 * Three different absences, and they are not the same sentence. An older
 * daemon sends no spend at all; a run nobody has measured has sources but no
 * figure to trust; a run with no declared cap has a real burn and no ceiling.
 * None of them is drawn as a spend of zero, and none of them is drawn as a
 * budget being kept.
 */
export function spendReading(spend: RunSpend | FleetSpend | undefined): SpendReading {
	if (spend === undefined) {
		return {
			value: null,
			gloss: 'this daemon does not project token spend',
			available: null
		};
	}
	if (spend.sources.length === 0) {
		return {
			value: null,
			gloss: 'nothing measured yet — unknown, not zero',
			available: null
		};
	}
	const how = spend.sources.map((phase) => PHASE_WORD[phase] ?? phase).join(' · ');
	if (spend.cap === null || spend.remaining === null) {
		return {
			value: tokens(spend.accounted),
			gloss: `${how}; no budget declared, and none is implied`,
			available: null
		};
	}
	return {
		value: tokens(spend.accounted),
		gloss: `${how}; a declared cap, enforced when an attempt starts`,
		// The remainder alone sits at the same weight as the fleet's whole spend
		// over a different denominator, and a reader scanning the row subtracts
		// them. Printing the ceiling it is a remainder of makes the arithmetic
		// visible instead of leaving it to the gloss.
		available: `${tokens(spend.remaining)} of ${tokens(spend.cap)}`
	};
}

/**
 * How long ago, at the coarseness a supervision window can actually trust.
 *
 * Never "0 seconds ago": the poll interval alone makes that a lie, and an
 * operator reading a fleet wants the order of magnitude, not a stopwatch.
 */
export function ago(iso: string, now: number): string {
	const then = Date.parse(iso);
	if (Number.isNaN(then)) return '—';
	const seconds = Math.max(0, Math.round((now - then) / 1000));
	if (seconds < 60) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 48) return `${hours} h ago`;
	return `${Math.round(hours / 24)} d ago`;
}

/**
 * How long one run's member is drawn, as a share of the sheet's width.
 *
 * Every member is on one fleet-wide unit: its length is its initiative count
 * measured against the largest listed run, so equal counts draw equal lengths
 * and the red across the bank is comparable in one pass. Drawing every member
 * full width made length mean *proportion within its own run* — three live
 * initiatives of seven drew a long red stretch and three of twenty-seven drew a
 * short one, identical load and opposite silhouette — which is the count you
 * have to assemble by reading, and the thing this composition exists to refuse.
 *
 * Floored well above zero: a one-initiative run beside a forty-initiative one
 * must still be a member somebody can see and a target somebody can hit.
 */
export const MIN_MEMBER_SHARE = 0.12;

export function memberShare(total: number, largest: number): number {
	if (largest <= 0 || total <= 0) return MIN_MEMBER_SHARE;
	return Math.max(MIN_MEMBER_SHARE, Math.min(1, total / largest));
}

/** The largest listed run, the unit every member is drawn against. */
export function largestRun(view: Fleet): number {
	return view.runs.reduce((most, run) => Math.max(most, run.total), 0);
}

/**
 * The fleet's own member: every listed run's initiatives as one structure.
 *
 * Drawn only when there is more than one run. At one run it would be the same
 * member twice on one screen, which is the defect of showing two numbers for
 * one fact wearing a drawing's clothes.
 */
export function fleetMember(view: Fleet): { segments: Segment[]; total: number } | null {
	if (view.runs.length < 2) return null;
	const total = SEGMENT_ORDER.reduce((sum, state) => sum + (view.counts[state] ?? 0), 0);
	if (total === 0) return null;
	return { segments: segmentsOf(view.counts, total), total };
}

/**
 * How many listed runs need the user at all, and how many items that is.
 *
 * Two different questions an operator asks in the same glance: how much of
 * the fleet is stuck, and how much work clearing it is.
 */
export function blockedRuns(view: Fleet): {
	runs: number;
	items: number;
	/** Listed runs that reported no attention at all. Their blockers are not in
	 * `items`, so a total printed without saying so would be a count that
	 * silently excluded them — an unknown presented as a zero. */
	silent: number;
	/** True when nothing reported at all: the whole figure is unknown. */
	unknown: boolean;
} {
	// Summed from the rows' own attention rather than from `Fleet.notifications`,
	// which carries the same items merged. Two derivations of one fact can
	// disagree, and this pair did: mid-write, the merged list and the rows
	// answered differently for one render and the readout said eight items
	// across zero runs. One traversal, one classifier, and the fleet figure is
	// the sum of the figures printed beside each run by construction.
	let runs = 0;
	let items = 0;
	let silent = 0;
	for (const run of view.runs) {
		const blocked = needsUser(run);
		if (blocked.unknown) {
			silent += 1;
			continue;
		}
		if (blocked.count === 0) continue;
		runs += 1;
		items += blocked.count;
	}
	return { runs, items, silent, unknown: silent === view.runs.length };
}
