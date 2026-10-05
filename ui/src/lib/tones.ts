/**
 * The one state vocabulary (DS §3.3): field members, schedule, dock, subtasks,
 * attempts, run rows, Locate results and needs-you items all speak it. The
 * tone is a CSS hook (`data-tone`), the word is what a reader hears.
 */
export type Tone =
	| 'running'
	| 'needs'
	| 'failed'
	| 'ready'
	| 'waiting'
	| 'settled'
	| 'paused'
	| 'cancelled'
	| 'starting'
	| 'idle';

export const TONE_WORD: Record<Tone, string> = {
	running: 'Running',
	needs: 'Needs you',
	failed: 'Failed',
	ready: 'Ready',
	waiting: 'Waiting',
	settled: 'Settled',
	paused: 'Paused',
	cancelled: 'Cancelled',
	starting: 'Starting',
	idle: 'Idle'
};

/**
 * A member's tone from its daemon state. `needsYou` is the caller's knowledge
 * (a checkpoint awaiting review, an open question): it outranks running and
 * pending, never failed.
 */
export function memberTone(node: { state: string; ready?: boolean }, needsYou = false): Tone {
	if (node.state === 'failed') return 'failed';
	if (needsYou) return 'needs';
	switch (node.state) {
		case 'running': return 'running';
		case 'settled': return 'settled';
		case 'paused': return 'paused';
		case 'cancelled': return 'cancelled';
		case 'starting': return 'starting';
		case 'pending': return node.ready ? 'ready' : 'waiting';
		default: return 'waiting';
	}
}

/** Effort level → tick count (DS §9.11), from the model's own ladder when known. */
export function effortTicks(effort: string | null | undefined, ladder?: readonly string[]): number {
	if (!effort) return 0;
	const levels = ladder?.length ? ladder : ['low', 'medium', 'high', 'xhigh'];
	const i = levels.indexOf(effort);
	if (i < 0) return 0;
	return Math.max(1, Math.round(((i + 1) / levels.length) * 4));
}
