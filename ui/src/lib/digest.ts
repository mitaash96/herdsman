import type { DigestEntry } from './daemon';

export const SEEN_KEY = 'herdsman-home-seen';
export type DigestWindow = 'since' | 'day' | 'week';
export const boundary = (window: DigestWindow, seen: string | null, now = Date.now()): string =>
	window === 'since' && seen ? seen : new Date(now - (window === 'week' ? 7 : 1) * 86_400_000).toISOString();

/** Insertion order follows the daemon chronology; no re-ranking within a run. */
export function grouped(entries: DigestEntry[]): [string, DigestEntry[]][] {
	const plans = new Map<string, DigestEntry[]>();
	for (const item of entries) plans.set(item.plan_id, [...(plans.get(item.plan_id) ?? []), item]);
	return [...plans];
}

export const latestOnly = (entries: DigestEntry[]): boolean => entries.length === 200;

export function typeCounts(entries: DigestEntry[]): string {
	const counts = new Map<string, number>();
	for (const entry of entries) counts.set(entry.type, (counts.get(entry.type) ?? 0) + 1);
	return `${entries.length} changes: ${[...counts].map(([type, count]) => `${count} ${type.replaceAll('_', ' ')}`).join(', ')}`;
}
