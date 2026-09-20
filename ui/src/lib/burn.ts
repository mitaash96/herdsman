/**
 * The burn instruments' pure model: what Run's cost plate is drawn from.
 *
 * Every function here is total and takes its inputs, so `dev/field-check.ts`
 * can assert the claims the plate rests on without a browser. The claims are:
 * the daemon's own `within_target` is read and never recomputed, a cap that
 * was never declared is neither zero nor unlimited, no member is drawn without
 * a unit to be measured against, and no absence is dressed as a zero.
 *
 * It imports nothing at runtime except `bank.ts`'s formatter and phase
 * vocabulary — `daemon.ts`'s types are erased — which is what lets the check
 * run under bare `node`.
 */

import { PHASE_WORD, tokens } from './bank.ts';
import type { BurnDown, MakespanETA, Overhead, TokenAnomaly, TokenTotals } from './daemon';

/**
 * Weight is load, the Load Bank's own numbers: the productive and
 * orchestration segments are drawn at the heavy run, the remaining headroom
 * at the hairline. Asserted in the model check so a later edit cannot drift
 * them apart.
 */
export const BURN_SEGMENT_WEIGHT = {
	productive: 2.5,
	orchestration: 2.5,
	headroom: 1
} as const;

export interface BurnSegment {
	kind: keyof typeof BURN_SEGMENT_NAME;
	share: number;
}

/** What each segment is called where a reader without colour meets it. */
export const BURN_SEGMENT_NAME = {
	productive: 'measured productive work',
	orchestration: 'orchestration overhead',
	headroom: 'remaining budget'
} as const;

/**
 * The burn member's segments, against the declared cap as the one unit.
 *
 * Load left, headroom right, exactly as the Load Bank draws a member. Returns
 * `null` when no cap was declared: with no denominator there is no shared
 * unit, and a member drawn against itself is always exactly full and says
 * nothing. A cap of zero is a declared ceiling, not an undeclared one, so it
 * draws an empty member rather than refusing to draw.
 */
export function burnSegments(
	productive: number,
	orchestration: number,
	cap: number | null
): BurnSegment[] | null {
	if (cap === null) return null;
	const burn = Math.min(productive + orchestration, cap);
	if (cap <= 0 || burn <= 0) return [];
	// The fold refuses the *next* attempt past a cap; it does not refund an
	// overrun, so `accounted` can exceed `cap`. The segments are shares of the
	// unit: when the burn overshoots, they fill the member and no headroom is
	// drawn — never a negative width.
	if (burn >= cap) {
		const total = productive + orchestration;
		return [
			{ kind: 'productive', share: productive / total },
			{ kind: 'orchestration', share: orchestration / total }
		];
	}
	const headroom = (cap - burn) / cap;
	return [
		{ kind: 'productive', share: productive / cap },
		{ kind: 'orchestration', share: orchestration / cap },
		{ kind: 'headroom', share: headroom }
	];
}

export interface RatioReading {
	value: string | null;
	gloss: string;
	state: 'seated' | 'failed' | 'slack';
}

export const NO_RATIO =
	'No productive tokens have been measured, so there is no ratio to take. ' +
	'Orchestration measured against nothing is not a percentage.';

/**
 * The overhead reading. `within_target` is the daemon's fact and is never
 * recomputed here — an impossible pair of figures still reads as the flag
 * says, because a second opinion on one fact is a defect however consistent.
 * The cell states the measurement and its target; the breach, when there is
 * one, is the overhead anomaly row's sentence and is stated exactly once.
 */
export function ratioReading(overhead: Overhead): RatioReading {
	if (overhead.ratio === null) {
		return { value: null, gloss: NO_RATIO, state: 'slack' };
	}
	return {
		value: `${(overhead.ratio * 100).toFixed(1)}%`,
		gloss: 'orchestration against measured productive work; the target is 20%',
		state: overhead.within_target === true ? 'seated' : overhead.within_target === false ? 'failed' : 'slack'
	};
}

export interface BudgetReading {
	value: string | null;
	gloss: string;
	/** Whether a member is drawn against this cap at all. */
	drawMember: boolean;
}

export const NO_CAP =
	'No budget was declared for this run. There is no ceiling to enforce and ' +
	'none is implied — an undeclared cap is not an unlimited one.';

/**
 * What the fold actually does with a declared cap, verified at
 * `classes.py`'s admission guard: a start is refused when it would carry the
 * run past the ceiling; nothing interrupts what is already running.
 */
export const ENFORCEMENT =
	'A declared cap is enforced when an attempt starts: the daemon refuses to ' +
	'start one whose packet would carry this run past the ceiling. It does not ' +
	'interrupt an attempt already running, and it is not a ceiling on what a ' +
	'running attempt can spend.';

export function budgetReading(cap: number | null, remaining: number | null): BudgetReading {
	if (cap === null) {
		return { value: null, gloss: NO_CAP, drawMember: false };
	}
	return {
		// The remainder alone sits at the same weight as the whole burn over a
		// different denominator; printing the ceiling it is a remainder of makes
		// the arithmetic visible instead of leaving it to the gloss.
		value: `${tokens(remaining ?? 0)} of ${tokens(cap)}`,
		gloss: 'the declared ceiling, enforced when an attempt starts',
		drawMember: true
	};
}

/** The ruled label's right-hand word: the highest phase present, H1's vocabulary. */
export function phaseWord(totals: TokenTotals): string {
	if (totals.actual > 0) return PHASE_WORD.actual;
	if (totals.preflight > 0) return PHASE_WORD.preflight;
	if (totals.estimate > 0) return PHASE_WORD.estimate;
	return 'nothing measured';
}

export const ESTIMATE_ONLY =
	'Every figure here is an estimate Herdsman made, not a count any harness ' +
	'or provider reported.';

/** True when the only thing the ledger has is labelled estimates. */
export function estimateOnly(totals: TokenTotals): boolean {
	return totals.actual === 0 && totals.preflight === 0 && totals.estimate > 0;
}

/** Phase words joined highest-precedence-first, `·`-separated, one per phase. */
export function joinedPhases(phases: string[]): string {
	const precedence = ['actual', 'preflight', 'estimate'];
	return precedence
		.filter((phase) => phases.includes(phase))
		.map((phase) => PHASE_WORD[phase] ?? phase)
		.join(' · ');
}

/** Elapsed or remaining time at the coarseness a supervision window can trust. */
export function coarse(seconds: number | null): string {
	if (seconds === null) return '—';
	if (seconds < 90) return 'moments';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min`;
	const hours = Math.round(minutes / 60);
	if (hours < 48) return `${hours} h`;
	return `${Math.round(hours / 24)} d`;
}

export interface EtaReading {
	value: string | null;
	gloss: string;
	state: 'seated' | 'slack';
}

export const UNKNOWN_ETA =
	'Herdsman computes one only from duration estimates the plan declares, and ' +
	'does not guess at the rest.';

export const PLAN_COMPLETE = 'Nothing is left to run.';

/**
 * The makespan reading. The daemon returns the figure or a reason; this never
 * re-derives the critical path and never fabricates a time. Note: the daemon
 * returns on the *first* initiative in fold order missing an estimate, so its
 * reason names an initiative, not the earliest blocking one — the sentence
 * says what the daemon said, and no more.
 */
export function etaReading(eta: MakespanETA): EtaReading {
	if (eta.eta === null) {
		return {
			value: null,
			gloss: `There is no finish time: ${eta.reason}. ${UNKNOWN_ETA}`,
			state: 'slack'
		};
	}
	if (eta.reason === 'plan complete' || eta.remaining_seconds === 0) {
		return { value: null, gloss: PLAN_COMPLETE, state: 'seated' };
	}
	return {
		value: `in about ${coarse(eta.remaining_seconds)}`,
		gloss: eta.reason,
		state: 'seated'
	};
}

export interface AnomalyGroup {
	code: string;
	/** The code as an operator reads it, not the fold's literal. */
	label: string;
	count: number;
	/** The members the group names, first-seen order, one id each. */
	ids: string[];
	/** One sentence per group — the grouped reading of the daemon's finding. */
	text: string;
}

export const MISSING_USAGE =
	'checkpoints closed without reporting usage. What those attempts actually ' +
	'spent is unknown, not zero.';

export const CONFLICTING_USAGE =
	'One measurement arrived twice with different values. The first was kept ' +
	'and the two were never added together; what this attempt really spent is ' +
	'in doubt.';

export const EXHAUSTED_BUDGET =
	'This ceiling is spent. No further attempt will be admitted against it.';

/** What an anomaly code is called where it is grouped. */
const CODE_NAME: Record<string, string> = {
	overhead: 'Overhead',
	'exhausted-budget': 'Budget spent',
	'missing-usage': 'Usage missing',
	'conflicting-usage': 'Conflicting counts'
};

/**
 * Anomalies grouped by code, not by member: the dense fixture alone produces
 * six missing-usage findings, and six rows of one sentence is a wall where a
 * count and its members are the fact. The `overhead` row carries the daemon's
 * own message verbatim — it is the one place the breach is stated as a
 * sentence, which is why the Overhead cell never states it.
 */
export function groupAnomalies(anomalies: TokenAnomaly[]): AnomalyGroup[] {
	const groups = new Map<string, AnomalyGroup>();
	for (const anomaly of anomalies) {
		let group = groups.get(anomaly.code);
		if (!group) {
			group = {
				code: anomaly.code,
				label: CODE_NAME[anomaly.code] ?? anomaly.code.replace(/_/g, ' '),
				count: 0,
				ids: [],
				text: ''
			};
			groups.set(anomaly.code, group);
		}
		group.count += 1;
		if (anomaly.initiative_id && !group.ids.includes(anomaly.initiative_id)) {
			group.ids.push(anomaly.initiative_id);
		}
	}
	for (const group of groups.values()) {
		switch (group.code) {
			case 'overhead':
				// The daemon's own words, verbatim — never paraphrased into a claim.
				group.text = anomalies.find((a) => a.code === 'overhead')?.message ?? '';
				break;
			case 'exhausted-budget':
				group.text = EXHAUSTED_BUDGET;
				break;
			case 'missing-usage':
				group.text = `${group.count} ${MISSING_USAGE}`;
				break;
			case 'conflicting-usage':
				group.text = CONFLICTING_USAGE;
				break;
		}
	}
	return [...groups.values()];
}

/** The plate's count is the daemon's, undivided — even when a cell renders one. */
export function anomalyCount(anomalies: TokenAnomaly[]): number {
	return anomalies.length;
}

export interface CeilingRow {
	id: string;
	value: string;
	state: 'seated' | 'failed';
}

export interface Ceilings {
	rows: CeilingRow[];
	/** Members that declare a cap, out of all live members. */
	declared: number;
	total: number;
}

/**
 * The ceilings list: one row per member that declared a cap. A member with
 * `null` remaining is *absent* — `null` means no cap was declared for it, and
 * `null` is not a member at zero. The label carries `n of m` so the absence is
 * a proportion, never a blank.
 */
export function ceilingsOf(caps: Record<string, number | null>): Ceilings {
	const rows: CeilingRow[] = [];
	for (const [id, remaining] of Object.entries(caps)) {
		if (remaining === null) continue;
		// remaining is the fold's max(cap - burn, 0): zero means at or past it.
		rows.push({
			id,
			value: `${tokens(remaining)} left`,
			state: remaining === 0 ? 'failed' : 'seated'
		});
	}
	return { rows, declared: rows.length, total: Object.keys(caps).length };
}

/** Categories with a non-zero figure, largest first, as a dimension string. */
export function categoryString(byCategory: Record<string, number>): string {
	return Object.entries(byCategory)
		.filter(([, total]) => total > 0)
		.sort(([, a], [, b]) => b - a)
		.map(([category, total]) => `${category.replace(/_/g, ' ')} ${tokens(total)}`)
		.join(' · ');
}

/** The one honesty claim that earns prose, under the plate. */
export const SELECTION_FOOT =
	'Where the same work was counted twice, the highest-precedence count ' +
	'replaces the others. Nothing here is a sum of alternatives.';

/** The six honest absences, asserted distinct so a copy edit cannot merge them. */
export const ABSENCE_SENTENCES = [
	NO_RATIO,
	ESTIMATE_ONLY,
	NO_CAP,
	UNKNOWN_ETA,
	PLAN_COMPLETE,
	ENFORCEMENT
] as const;
