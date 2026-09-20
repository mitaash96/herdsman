import type { RecoveryAttempt, RecoveryReport, ResumeOutcome } from './daemon';

export type RecoveryRow = RecoveryAttempt & {
	outcome: ResumeOutcome | 'unprobed' | 'not reported';
};

export const OUTCOME_WORD: Record<string, string> = {
	reattached: 'reattached',
	settled: 'settled',
	'review-pending': 'waiting for review',
	failed: 'closed as failed',
	skipped: 'already closed'
};

export const OUTCOME_STATE: Record<string, 'seated' | 'balanced' | 'failed' | 'slack'> = {
	reattached: 'seated',
	settled: 'seated',
	'review-pending': 'balanced',
	failed: 'failed',
	skipped: 'slack'
};

const OUTCOME_SENTENCES: Record<string, string> = {
	reattached:
		'Its pane was still alive. The daemon followed it to its end and collected what the agent produced while nothing was listening. The attempt count did not move.',
	settled:
		'Its checkpoint had already been recorded before the daemon died. The settlement that was interrupted finished. The work was not re-observed and was not re-run.',
	'review-pending':
		'Its checkpoint was recorded and is still undecided, so nothing was written. It is waiting for a reviewer, and it will still be listed here until one decides. Its evidence is in the member’s checkpoint section.',
	failed:
		'Its pane was gone, so the attempt was closed with a recorded reason. The worktree and every preserved artifact stay. The reason is in the member’s drawer, and retrying is a decision made there.',
	skipped: 'Something else closed this attempt between the report and the probe. Nothing was written for it.'
};

export function staleRows(report: RecoveryReport | null): RecoveryRow[] {
	if (!report) return [];
	return report.stale.map((attempt) => ({
		...attempt,
		outcome: report.outcomes[attempt.initiative_id] ??
			(Object.keys(report.outcomes).length > 0 ? 'not reported' : 'unprobed')
	}));
}

export function unlistedOutcomes(report: RecoveryReport): [string, ResumeOutcome][] {
	const staleIds = new Set(report.stale.map((attempt) => attempt.initiative_id));
	return Object.entries(report.outcomes).filter(([id]) => !staleIds.has(id));
}

export function outcomeSentences(outcomes: Record<string, ResumeOutcome>): [string, string][] {
	const seen = new Set<string>();
	const result: [string, string][] = [];
	for (const outcome of Object.values(outcomes)) {
		if (seen.has(outcome)) continue;
		seen.add(outcome);
		result.push([OUTCOME_WORD[outcome] ?? 'unrecognised outcome', OUTCOME_SENTENCES[outcome] ?? `The daemon returned an unrecognised outcome: ${outcome}.`]);
	}
	return result;
}

export function summarize(outcomes: Record<string, ResumeOutcome>): string {
	const counts = new Map<string, number>();
	for (const outcome of Object.values(outcomes)) counts.set(outcome, (counts.get(outcome) ?? 0) + 1);
	if (counts.size === 0) return 'Nothing on this plan is stale now. The attempts listed before were reconciled; what each one came to is in its own member.';
	const parts = ['reattached', 'settled', 'review-pending', 'failed', 'skipped'].flatMap((key) => {
		const count = counts.get(key);
		return count ? [`${count} ${OUTCOME_WORD[key] ?? key}`] : [];
	});
	return `Reconciled ${Object.values(outcomes).length} attempts: ${parts.join(', ')}. Nothing was deleted and nothing new was started.`;
}

export function reconcileLines(count: number): string[] {
	return [
		`Probes herdr once for what is still alive, then resolves each of these ${count} attempts to exactly one outcome.`,
		'An attempt whose pane is still alive in a Herdsman workspace is reattached: the daemon watches it to its end and collects the work the agent did while nothing was listening. It is never re-run.',
		'An attempt whose pane is gone is closed with a recorded failure naming the missing pane. Its worktree and its evidence stay. Retrying it afterwards is a decision you make on the member, not something this does.',
		'An attempt that already recorded a checkpoint is finished under the settlement policy that was interrupted. A recorded checkpoint is never re-observed and completed work is never re-run.',
		'No pending or failed initiative is started. This reconciles what was running; it does not drive the plan.',
		'Herdsman-owned panes and worktrees that no attempt claims are reported and never removed. Deleting one is a separate, explicit discard.'
	];
}

export const FOUR_WAY = [
	['THIS', 'reconciles attempts a dead daemon left open. Work already recorded is finished, not repeated. Nothing new starts.'],
	['RETRY', 'a new attempt on a member that failed, in a fresh worktree on a fresh packet. It is offered in that member’s own drawer, and only there.'],
	['RESTART', 're-issues one live attempt’s own command in its own pane. The attempt does not change and the attempt count does not move. Also in the member’s drawer.'],
	['REPLANNING', 'changing the plan’s structure after what you have learned. That is not built, and this is not it: reconciling never edits the graph, adds a member, or removes one.']
] as const;
