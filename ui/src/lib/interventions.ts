/**
 * Unit R6's pure model: which intervention is available, why one is not, and
 * what confirming it actually does.
 *
 * Everything here mirrors a rule the daemon or the fold already enforces
 * (`Daemon._live_attempt`, `Daemon.retry_initiative`, and the `TaskRedirected`
 * / `TaskReassigned` / `TaskNudged` / `OperatorAnswered` / `ProcessRestarted`
 * arms of `Plan._apply`). Nothing here is a second, softer opinion: when this
 * file says a control is unavailable it is naming the sentence the daemon
 * would answer with, so an operator never presses a control the fold will
 * refuse and never reads a refusal this build invented.
 *
 * It imports nothing at runtime, so `ui/dev/field-check.ts` can assert it.
 */

import type { Attempt, DownstreamImpact, Initiative } from './daemon';

/** The member writes, in the order they are offered. */
export type Action =
	| 'retry'
	| 'restart'
	| 'reassign'
	| 'redirect'
	| 'nudge'
	| 'answer'
	| 'pause'
	| 'unpause'
	| 'cancel';

export const ACTIONS: Action[] = [
	'retry',
	'restart',
	'reassign',
	'redirect',
	'nudge',
	'answer',
	'pause',
	'unpause',
	'cancel'
];

export const ACTION_WORD: Record<Action, string> = {
	retry: 'Retry',
	restart: 'Restart process',
	reassign: 'Reassign',
	redirect: 'Redirect',
	nudge: 'Nudge',
	answer: 'Answer',
	pause: 'Hold',
	unpause: 'Release hold',
	cancel: 'Cancel'
};

/**
 * One line under each control saying what it *is*, because the six are easy to
 * confuse and three of them are commonly mistaken for each other. A retry is a
 * new attempt, a restart is the same attempt's process, and a redirect changes
 * what the next attempt is told — they are not degrees of the same thing.
 */
export const ACTION_GLOSS: Record<Action, string> = {
	retry: 'a new attempt on the current brief, in a fresh worktree',
	restart: "the same attempt's command, re-issued in its own pane",
	reassign: 'a different harness or model, for the next attempt only',
	redirect: 'a new brief version the next attempt runs on',
	nudge: 'free text delivered to the agent that is running now',
	answer: 'a reply to something the running agent asked for',
	pause: 'stops new attempts starting here; an attempt already running is not interrupted',
	unpause: 'lifts the hold; the member becomes retryable again',
	cancel: 'stops this member for good; nothing downstream is released'
};

/**
 * Whether an action can strand or redo work that already ran, and therefore
 * owes a downstream reading before it is confirmed.
 *
 * A restart is deliberately not on this list: it re-issues one live attempt's
 * own command in its own worktree and changes nothing about the plan, so
 * attaching a downstream consequence to it would be theatre. A nudge and an
 * answer reach a pane and nothing else.
 */
export function disruptive(action: Action): boolean {
	return action === 'retry' || action === 'reassign' || action === 'redirect' || action === 'cancel';
}

export interface Availability {
	action: Action;
	available: boolean;
	/** The rule that refuses it, in the daemon's own terms. Null when available. */
	refused: string | null;
}

/** The brief version new attempts run on. 1 is the planner's, which is not stored. */
export function currentBriefVersion(initiative: Initiative): number {
	return initiative.brief_versions.length + 1;
}

/** The version a redirect would create, which is always the next one after that. */
export function nextBriefVersion(initiative: Initiative): number {
	return initiative.brief_versions.length + 2;
}

/**
 * The attempt an agent is listening on, or null.
 *
 * `Daemon._live_attempt` takes the *last* attempt of a running initiative and
 * requires it to have recorded a pane; an earlier attempt's pane belongs to an
 * agent that is gone, so messaging it would deliver to nobody. This returns
 * exactly what that would resolve, so the surface and the daemon agree.
 */
export function liveAttempt(initiative: Initiative): Attempt | null {
	if (initiative.state !== 'running') return null;
	const last = initiative.attempts[initiative.attempts.length - 1];
	return last?.pane_ref ? last : null;
}

/**
 * Why there is no live pane, as a sentence.
 *
 * Restart, nudge and answer are refused by the same three checks in the same
 * order, so this deliberately names no verb: all three get the *same string*,
 * and the surface prints one sentence naming three actions instead of three
 * paragraphs that differ only in their first two words.
 */
function noLivePane(initiative: Initiative): string | null {
	if (initiative.state !== 'running') {
		return `Only a running task has a live pane, and this member is ${initiative.state}. There is no agent listening to deliver to.`;
	}
	if (initiative.attempts.length === 0) {
		return 'No attempt has started here, so there is no live pane to deliver to.';
	}
	const last = initiative.attempts[initiative.attempts.length - 1];
	if (!last.pane_ref) {
		return `Attempt ${last.id} recorded no pane, so there is nowhere to deliver to. An attempt can start before herdr answers with one.`;
	}
	return null;
}

/**
 * Every action, with the exact rule refusing the ones that are unavailable.
 *
 * Unavailable is never a hidden control and never a dead one: the surface
 * renders the sentence, which is the only thing that tells an operator what
 * would have to change.
 */
export function availability(initiative: Initiative, approved: boolean): Availability[] {
	const state = initiative.state;
	const ceiling = initiative.spec.policy.max_attempts;
	const settledOrGone = state === 'settled' || state === 'cancelled';

	const refusals: Record<Action, string | null> = {
		retry:
			state !== 'failed'
				? `A retry starts a new attempt on failed work, and this member is ${state}. Nothing has failed here to retry.`
				: initiative.attempts.length >= ceiling
					? `This member has used all ${ceiling} of its attempts. The fold refuses a further one, so there is no retry left — what to do next is a plan-level decision, not another run.`
					: !approved
						? 'The plan revision is not approved, so no attempt may start — this one included. Close this to read and approve the revision.'
						: null,
		restart: noLivePane(initiative),
		reassign: settledOrGone
			? `Only an active or retryable task can be reassigned, and this member is ${state}. Its attempts keep the assignment they ran under.`
			: null,
		redirect: settledOrGone
			? `Only an active or retryable task can be redirected, and this member is ${state}. Its recorded brief versions stay readable either way.`
			: null,
		nudge: noLivePane(initiative),
		answer: noLivePane(initiative),
		pause:
			state === 'paused'
				? 'This member is already held.'
			: !['pending', 'failed', 'running'].includes(state)
				? `Only a member that is pending, failed or running can be held, and this one is ${state}.`
				: null,
		unpause:
			state !== 'paused'
				? `Only a held member can be released, and this one is ${state}.`
				: null,
		cancel:
			settledOrGone
				? `A settled or cancelled member cannot be cancelled. This one is ${state}, and it is already out of the structure.`
				: null
	};

	return ACTIONS.map((action) => ({
		action,
		available: refusals[action] === null,
		refused: refusals[action]
	}));
}

/**
 * The refused actions, with the ones that share a cause collapsed into one
 * entry.
 *
 * Three of the six are refused by the same check, so listing them separately
 * prints one sentence three times — a defect this surface's own predecessor
 * was corrected for. Identical reasons are one reason.
 */
export function heldGroups(offers: Availability[]): { actions: Action[]; refused: string }[] {
	const groups: { actions: Action[]; refused: string }[] = [];
	for (const offer of offers) {
		if (offer.available || offer.refused === null) continue;
		const existing = groups.find((group) => group.refused === offer.refused);
		if (existing) existing.actions.push(offer.action);
		else groups.push({ actions: [offer.action], refused: offer.refused });
	}
	return groups;
}

export interface ImpactContext {
	initiative: Initiative;
	/** The daemon's own downstream projection, or null while it is unread. */
	impact: DownstreamImpact | null;
	/** For a reassignment: the pair the operator typed, when both halves are set. */
	assignment?: { harness: string; model: string } | null;
	/** For a redirect: whether the target is a checkpoint rather than a new brief. */
	fromCheckpoint?: boolean;
}

/**
 * What pressing confirm does, in members the operator can see on the field
 * behind this sheet.
 *
 * The first line is always what the action does to *this* member; the lines
 * after it are what it does to everything else, and they are written from the
 * daemon's `DownstreamImpact` rather than from a graph walk repeated here. A
 * null impact is stated as unread — never as "nothing downstream".
 */
export function impactLines(action: Action, context: ImpactContext): string[] {
	const { initiative, impact } = context;
	const id = initiative.spec.id;
	const attempts = initiative.attempts.length;
	const ceiling = initiative.spec.policy.max_attempts;
	const lines: string[] = [];

	if (action === 'retry') {
		lines.push(
			`Starts attempt ${attempts + 1} of ${ceiling} on brief version ${currentBriefVersion(initiative)}, in a fresh worktree and under ${assignmentWord(initiative)}. The ${attempts === 1 ? 'failed attempt' : 'earlier attempts'} and ${attempts === 1 ? 'its' : 'their'} evidence stay in the history; nothing is deleted.`
		);
		if (attempts + 1 === ceiling) {
			lines.push(
				`This is the last attempt the fold will admit for ${id}. A failure after it cannot be retried.`
			);
		}
	} else if (action === 'restart') {
		const live = liveAttempt(initiative);
		lines.push(
			`Re-issues attempt ${live ? live.id : '—'}'s own command in its own pane and worktree. Same packet, same attempt: the attempt count stays at ${attempts} of ${ceiling} and this is not a retry.`
		);
		lines.push(
			'The agent currently in that pane is interrupted first, so the re-issued command reaches a fresh prompt. Anything it had not written is lost with it.'
		);
	} else if (action === 'reassign') {
		const pair = context.assignment;
		lines.push(
			pair
				? `The next attempt runs on ${pair.harness}/${pair.model}. ${initiative.state === 'running' ? 'The running attempt finishes on its own snapshot' : 'Past attempts keep their own'}, so history is not rewritten.`
				: `The next attempt runs on the pair you choose. ${initiative.state === 'running' ? 'The running attempt finishes on its own snapshot' : 'Past attempts keep their own'}, so history is not rewritten.`
		);
		lines.push(
			'Nothing starts because of this. It changes what the next attempt would be, and there is no next attempt until you start one.'
		);
	} else if (action === 'redirect') {
		lines.push(
			context.fromCheckpoint
				? `Records brief version ${nextBriefVersion(initiative)} for ${id}, derived from the checkpoint you chose. New attempts continue from it.`
				: `Records brief version ${nextBriefVersion(initiative)} for ${id}. New attempts run on it; version ${currentBriefVersion(initiative)} stays readable and stays what earlier attempts ran on.`
		);
		if (initiative.state === 'running') {
			lines.push(
				'The running attempt keeps the version it started on, so nothing live is disturbed. Use a nudge to reach the agent that is working now.'
			);
		}
		lines.push(
			'The redirect is recorded as ground truth, so later compiled packets carry the new brief rather than the one it replaced.'
		);
	} else if (action === 'cancel') {
		lines.push(
			`Stops ${id} for good. Its worktree and every preserved artifact stay, and its recorded history stays readable. No retry, redirect, reassignment or settlement reaches it afterwards — this is terminal, like settling, and it cannot be undone from here.`
		);
		const live = liveAttempt(initiative);
		if (live) lines.push(`The agent in attempt ${live.id}'s pane is interrupted so it stops burning tokens.`);
	} else if (action === 'pause') {
		lines.push(
			`The scheduler stops admitting new attempts for ${id}. An attempt that is running now is not interrupted and settles under the usual policy — holding holds the queue, it does not stop the agent. Cancelling is what stops an agent.`
		);
		const live = liveAttempt(initiative);
		if (live) lines.push(`Attempt ${live.id} keeps running while this is held. If the daemon dies while it does, reconciliation is what picks it up.`);
	} else if (action === 'unpause') {
		lines.push(
			initiative.attempts.length > 0
				? `Releases the hold. ${id} goes back to failed, not back to running — the attempt it was holding is over. Nothing starts because of this; a retry in this drawer is what starts the next attempt.`
				: `Releases the hold. ${id} goes back to pending and becomes startable again when its dependencies allow. Nothing starts because of this.`
		);
	} else if (action === 'nudge') {
		const live = liveAttempt(initiative);
		lines.push(
			`Delivered to attempt ${live ? live.id : '—'}'s pane while it is running. It changes no state, releases nothing, and does not end or restart the attempt.`
		);
	} else {
		const live = liveAttempt(initiative);
		lines.push(
			`Delivered to attempt ${live ? live.id : '—'}'s pane as an answer to what it asked. It is recorded as the answer to that subject, which is what lets a repeat of the same question be answered without you.`
		);
	}

	if (!disruptive(action)) return lines;

	if (impact === null) {
		lines.push(
			'What this disturbs downstream is unread — the impact read did not answer. That is unknown, not nothing.'
		);
		return lines;
	}

	const started = impact.started;
	const idle = impact.descendants.filter((node) => !started.includes(node.initiative_id));

	if (action === 'cancel') {
		if (impact.descendants.length === 0) {
			lines.push('Nothing depends on this member, so nothing downstream is disturbed.');
			return lines;
		}
		if (started.length > 0) {
			lines.push(
				`${started.join(', ')} already ran on what this member produced. ${started.length === 1 ? 'It' : 'They'} remain resting on work this cancellation preserves, and that history is not rewritten.`
			);
		}
		if (idle.length > 0) {
			lines.push(
				`${idle.map((node) => node.initiative_id).join(', ')} ${idle.length === 1 ? 'remains' : 'remain'} pending and ${idle.length === 1 ? 'is' : 'are'} not released by cancellation, because a cancelled member never settles.`
			);
		}
		return lines;
	}

	if (impact.descendants.length === 0) {
		lines.push('Nothing depends on this member, so nothing downstream is disturbed.');
		return lines;
	}
	if (started.length > 0) {
		lines.push(
			`${started.join(', ')} already ran on what this member produced. ${started.length === 1 ? 'It' : 'They'} would be resting on work this replaces, and ${started.length === 1 ? 'that attempt' : 'those attempts'} may have to be redone.`
		);
	}
	if (idle.length > 0) {
		lines.push(
			`${idle.map((node) => node.initiative_id).join(', ')} ${idle.length === 1 ? 'is' : 'are'} downstream and ${idle.length === 1 ? 'has' : 'have'} not started, so ${idle.length === 1 ? 'it is' : 'they are'} unaffected until this settles.`
		);
	}
	return lines;
}

/** The pair a new attempt would run under: the override, else the planner's. */
export function assignmentWord(initiative: Initiative): string {
	const current = initiative.assignment_override ?? initiative.spec.assignment;
	return `${current.harness}/${current.model}`;
}

/**
 * Whether a typed pair is the one already in force. The fold refuses a
 * reassignment onto the current assignment, so the control is held rather than
 * sent to be turned away.
 */
export function sameAssignment(initiative: Initiative, harness: string, model: string): boolean {
	const current = initiative.assignment_override ?? initiative.spec.assignment;
	return current.harness === harness.trim() && current.model === model.trim();
}

export interface CheckpointChoice {
	id: string;
	/** The initiative that produced it. */
	producer: string;
	/** 1-based, in that initiative's own record order. */
	version: number;
	/** Whether it belongs to the member being redirected. */
	own: boolean;
}

/**
 * Every recorded checkpoint version in the plan, as redirect targets.
 *
 * The fold resolves `checkpoint_id` across *all* initiatives, not just this
 * one, so the list is plan-wide or it would hide legal targets. Which of them
 * is worth continuing from is a question about evidence, and reading evidence
 * is the checkpoint section's job — this only names what exists.
 */
export function checkpointChoices(
	initiatives: Record<string, Initiative>,
	forId: string
): CheckpointChoice[] {
	const choices: CheckpointChoice[] = [];
	for (const [producer, initiative] of Object.entries(initiatives)) {
		initiative.checkpoint_versions.forEach((version, index) => {
			choices.push({ id: version.id, producer, version: index + 1, own: producer === forId });
		});
	}
	/* The member's own versions first: continuing from your own recorded work is
	   the common case, and a plan-wide list otherwise buries it. */
	return choices.sort((a, b) =>
		a.own === b.own ? a.producer.localeCompare(b.producer) || a.version - b.version : a.own ? -1 : 1
	);
}
