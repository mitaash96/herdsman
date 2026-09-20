import type { InitiativeState, Plan } from './daemon';

export type RunPhase =
	| 'awaiting_approval'
	| 'empty'
	| 'running'
	| 'settled'
	| 'failed'
	| 'paused'
	| 'idle'
	| 'unread';

/** Mirrors fleet.run_status precedence in herdsman/fleet.py. */
export function runPhase(plan: Plan | null | undefined): RunPhase {
	if (!plan) return 'unread';
	if (plan.approval !== 'approved') return 'awaiting_approval';
	const initiatives = Object.values(plan.initiatives);
	if (initiatives.length === 0) return 'empty';
	const states = initiatives.map((initiative) => initiative.state);
	if (states.some((state) => state === 'running')) return 'running';
	if (states.every((state) => state === 'settled' || state === 'cancelled')) return 'settled';
	if (states.some((state) => state === 'failed')) return 'failed';
	if (states.some((state) => state === 'paused')) return 'paused';
	return 'idle';
}

const QUALIFICATION: Record<RunPhase, string> = {
	awaiting_approval: 'Replay opens once a run has stopped. This one has not been approved yet.',
	empty: 'Replay opens once a run has stopped. This revision proposed no initiatives, so there is no record to read.',
	running: 'Replay opens once a run has stopped. This one still has work in flight.',
	paused: 'Replay opens once a run has stopped. This one is paused, and paused work can still start.',
	idle: 'Replay opens once a run has stopped. This one has members that can still start.',
	settled: '',
	failed: '',
	unread: 'Replay opens once a run has stopped, and whether this one has cannot be read until the plan projection answers.'
};

export function qualifies(phase: RunPhase): boolean {
	return phase === 'settled' || phase === 'failed';
}

export function qualificationSentence(phase: RunPhase): string {
	return QUALIFICATION[phase];
}

export interface ReplayStop {
	at: string;
	labels: string[];
	label: string;
}

function add(records: Map<string, string[]>, at: string | null | undefined, label: string): void {
	if (!at) return;
	const labels = records.get(at) ?? [];
	if (!labels.includes(label)) labels.push(label);
	records.set(at, labels);
}

/** Build the ordinal index from dated records retained by the folded Plan. */
export function stopsOf(plan: Plan): ReplayStop[] {
	if (!plan.created_at) throw new Error('Plan.created_at is required for replay');
	const records = new Map<string, string[]>();
	add(records, plan.created_at, 'Plan was proposed');
	for (const initiative of Object.values(plan.initiatives)) {
		for (const attempt of initiative.attempts) {
			add(records, attempt.started_at, `Attempt ${attempt.id} started`);
			add(records, attempt.ended_at, `Attempt ${attempt.id} ended`);
		}
		for (const version of initiative.brief_versions) {
			add(records, version.at, `Brief version ${version.version} recorded`);
		}
		for (const [checkpointId, decision] of Object.entries(initiative.checkpoint_decisions ?? {})) {
			add(records, decision.decided_at, `Checkpoint ${checkpointId} decided`);
			add(records, decision.approved_at, `Checkpoint ${checkpointId} approved`);
		}
	}
	for (const decision of plan.policy_decisions ?? []) {
		add(records, decision.at, `Automatic decision for ${decision.initiative_id}`);
	}
	for (const [attemptId, at] of Object.entries(plan.live_until ?? {})) {
		add(records, at, `Attempt ${attemptId} stopped being live`);
	}
	if (records.size === 0) throw new Error('Plan.created_at is required for replay');
	return [...records.entries()]
		.sort(([a], [b]) => Date.parse(a) - Date.parse(b))
		.map(([at, labels]) => ({ at, labels, label: labels.join('; ') }));
}

export function boundOf(stops: ReplayStop[], index: number): string {
	return stops[Math.max(0, Math.min(index, stops.length - 1))]?.at ?? '';
}

/** Resolve a bookmark to the nearest stop at or before it; never snap forward. */
export function nearestStop(stops: ReplayStop[], at: string): number {
	if (stops.length === 0) return 0;
	const target = Date.parse(at);
	let found = 0;
	for (let index = 1; index < stops.length; index++) {
		if (Date.parse(stops[index].at) > target) break;
		found = index;
	}
	return found;
}

export const LAST_STOP_DIFFERS = "The record's last dated moment does not yet show the run as it finished. Return to live to read the run as it stands.";

export function stopReading(
	stops: ReplayStop[],
	index: number,
	historicalPhase: RunPhase,
	livePhase: RunPhase
): string {
	return index === stops.length - 1 && historicalPhase !== livePhase ? LAST_STOP_DIFFERS : '';
}

export const POLICY_RULE_IDS = [
	'approve.contract',
	'approve.checks_green',
	'approve.diff_size',
	'approve.scope',
	'stop_loss.budget',
	'stop_loss.retry_ceiling',
	'escalate.operator_review'
] as const;

export type PolicyRuleId = (typeof POLICY_RULE_IDS)[number];
export const POLICY_RULE_NAMES: Record<PolicyRuleId, string> = {
	'approve.contract': 'contract',
	'approve.checks_green': 'checks',
	'approve.diff_size': 'diff size',
	'approve.scope': 'declared scope',
	'stop_loss.budget': 'token budget',
	'stop_loss.retry_ceiling': 'attempt ceiling',
	'escalate.operator_review': 'operator review'
};

export function ruleName(id: string): string | null {
	return (POLICY_RULE_IDS as readonly string[]).includes(id)
		? POLICY_RULE_NAMES[id as PolicyRuleId]
		: null;
}

let historical = false;
export function setHistorical(value: boolean): void {
	historical = value;
}
export function isHistorical(): boolean {
	return historical;
}

export type InitiativeStateForReplay = InitiativeState;
