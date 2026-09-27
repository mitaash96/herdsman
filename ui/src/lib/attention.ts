import type { AttentionItem } from './daemon';

const surfaces: Record<string, string> = {
	plan_gate: 'plan gate',
	checkpoint_review: 'checkpoint review (path-set lists are partial)',
	blocked_on_user: 'member drawer → Answer',
	failed: 'member drawer → Retry',
	stalled: 'member drawer → Nudge'
};

export function attentionSurface(item: AttentionItem): string {
	return surfaces[item.kind] ?? 'Run';
}

export const shouldNotify = (pathname: string, hidden: boolean): boolean =>
	hidden || pathname !== '/home';

export function waiting(items: AttentionItem[] | undefined): number | null {
	return items === undefined ? null : items.filter((item) => item.blocking).length;
}
