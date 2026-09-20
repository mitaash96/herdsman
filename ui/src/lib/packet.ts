/**
 * The packet inspector's pure model (unit R7).
 *
 * Imports types only, so nothing of `herdsman/classes.py` exists at runtime,
 * and every claim it makes is asserted in `ui/dev/field-check.ts` — the same
 * precedent as `review.ts` (R4) and `interventions.ts` (R6).
 *
 * What it owns is everything the inspector asserts rather than computes in
 * markup: which attempt to open on, which attempts can be compared, the
 * daemon's changed/added verdict, and the sentences provenance prints.
 * Nothing here sorts a snapshot's sections and nothing here computes a diff —
 * the order is the daemon's and the diff is the daemon's.
 */
import type { Attempt, PacketDiff, PacketSection, PacketSnapshot } from './daemon';

/**
 * The seven-way classification a section's `value` renders by.
 * `raw` is the escape hatch for a shape a newer daemon serves that this build
 * does not know how to lay out — it renders as the daemon serves it, in JSON,
 * under a line that says exactly that. It is not a JSON tree viewer.
 */
export type SectionBody =
	| { kind: 'empty' }
	| { kind: 'null' }
	| { kind: 'string'; text: string }
	| { kind: 'list'; items: string[] }
	| { kind: 'object'; value: Record<string, unknown> }
	| { kind: 'objectList'; items: Record<string, unknown>[] }
	| { kind: 'raw'; json: string };

/**
 * `""` and `[]` are the same fact — the section was compiled and carries
 * nothing — and both say `None declared`. `null` is a different fact: the
 * section carries no value at all. Anything this build does not recognise
 * falls through to `raw` rather than throwing, so a newer daemon's section
 * renders truthfully instead of crashing the sheet.
 */
export function sectionBody(value: unknown): SectionBody {
	if (value === null) return { kind: 'null' };
	if (typeof value === 'string') {
		return value.length === 0 ? { kind: 'empty' } : { kind: 'string', text: value };
	}
	if (Array.isArray(value)) {
		if (value.length === 0) return { kind: 'empty' };
		if (value.every((item) => typeof item === 'string')) {
			return { kind: 'list', items: value as string[] };
		}
		if (
			value.every(
				(item) => typeof item === 'object' && item !== null && !Array.isArray(item)
			)
		) {
			return { kind: 'objectList', items: value as Record<string, unknown>[] };
		}
		return { kind: 'raw', json: JSON.stringify(value, null, 2) };
	}
	if (typeof value === 'object') {
		return { kind: 'object', value: value as Record<string, unknown> };
	}
	return { kind: 'raw', json: JSON.stringify(value, null, 2) };
}

/**
 * A string renders as a code chip when it is one bare token — a path, an id,
 * a digest — and as prose when it is a sentence.
 *
 * # ponytail: one no-whitespace rule, nothing smarter. A pointer line like
 * "run-scoped leaf: ml-x@3" keeps its spaces and reads as prose; splitting
 * prose to find the id inside it needs a real parser, add one when a section
 * actually demands it.
 */
export function codeLike(item: string): boolean {
	return item.length > 0 && !/\s/.test(item);
}

/**
 * The sections in exactly the order the snapshot serves them, element for
 * element. The list is the packet's own construction record; this build never
 * sorts, groups or filters it, and a shuffled input passes through unchanged.
 */
export function sectionRows(snapshot: PacketSnapshot): PacketSection[] {
	return [...snapshot.sections];
}

/**
 * The attempt to open on: the latest one that has a snapshot. The latest
 * attempt is what an operator opening a failed member is asking about, and
 * falling back past a snapshot-less one avoids opening on an empty state when
 * readable data exists. `null` means no attempt has a snapshot — the missing
 * state, not a failure.
 */
export function defaultAttempt(attempts: Attempt[]): Attempt | null {
	for (let at = attempts.length - 1; at >= 0; at--) {
		if (attempts[at].packet_snapshot !== null) return attempts[at];
	}
	return null;
}

/**
 * The attempts a comparison may name: every attempt but the selected one that
 * carries a snapshot. A snapshot-less attempt is never offered — the only
 * thing the diff route could do with it is answer its 404.
 */
export function comparableAttempts(selected: Attempt | null, attempts: Attempt[]): Attempt[] {
	if (selected === null) return [];
	return attempts.filter(
		(attempt) => attempt.id !== selected.id && attempt.packet_snapshot !== null
	);
}

/**
 * The pair, ordered earlier-started → later regardless of which one the
 * operator is reading, so the daemon's `after − before` always reads forward
 * in time. Equal timestamps (two attempts started in the same tick) fall back
 * to record order: the earlier of the two in the attempts list is before.
 */
export function comparisonPair(
	selected: Attempt,
	other: Attempt,
	attempts: Attempt[]
): { before: Attempt; after: Attempt } {
	const earlier = (at: Attempt) => new Date(at.started_at).getTime();
	const order = attempts.findIndex((a) => a.id === selected.id);
	const otherOrder = attempts.findIndex((a) => a.id === other.id);
	if (earlier(other) < earlier(selected)) return { before: other, after: selected };
	if (earlier(other) > earlier(selected)) return { before: selected, after: other };
	// Same instant: record order decides, and it is still earlier → later.
	return order < otherOrder
		? { before: selected, after: other }
		: { before: other, after: selected };
}

/**
 * The verdict a comparison arms onto an existing row: `changed` or `added`,
 * never a ghost row. A removed section has no row in the selected snapshot —
 * it is named in the comparison plate and nowhere else.
 */
export function diffVerdict(
	diff: PacketDiff | null,
	name: string
): 'changed' | 'added' | null {
	if (!diff) return null;
	if (diff.added_sections.includes(name)) return 'added';
	if (diff.changed_sections.includes(name)) return 'changed';
	return null;
}

/**
 * A section disagrees with the band when it was measured differently. The
 * marker word is the whole of what the row says; the band's own reach line is
 * what makes every other row's silence mean something.
 */
export function provenanceMarker(
	snapshot: PacketSnapshot,
	section: PacketSection
): 'changed' | null {
	const first = snapshot.sections[0];
	if (!first) return null;
	return section.source === first.source &&
		section.phase === first.phase &&
		section.provenance === first.provenance
		? null
		: 'changed';
}

/**
 * What every section shares, per field — the value when they all agree, null
 * when they do not. Null is `mixed`, never a guess at a majority.
 */
export function commonProvenance(snapshot: PacketSnapshot): {
	phase: string | null;
	source: string | null;
	provenance: string | null;
} {
	const sections = snapshot.sections;
	const same = <K extends keyof PacketSection>(key: K): PacketSection[K] | null => {
		const first = sections[0];
		if (!first) return null;
		return sections.every((section) => section[key] === first[key]) ? first[key] : null;
	};
	return { phase: same('phase'), source: same('source'), provenance: same('provenance') };
}

/**
 * Provenance sentences. `estimate`, `harness` and `provider` have sentences
 * (the provider's is the one the drawer's own usage readout already prints);
 * anything else — including `gateway`, which the daemon's vocabulary names and
 * this build has no honest sentence for — prints the raw string. Guessing a
 * provenance is the one thing this section exists to not do.
 */
const SOURCE_SENTENCES: Partial<Record<PacketSection['source'], string>> = {
	estimate: 'estimated, not measured',
	harness: 'reported by the harness',
	provider: 'reported by the provider',
	tokenizer: 'counted by a tokenizer'
};
export function sourceSentence(source: string): string {
	return SOURCE_SENTENCES[source as PacketSection['source']] ?? source;
}

/**
 * `output_tokens` is 0 on every section this build has seen: nothing had been
 * generated when it was measured, which is not an output of zero. The display
 * prints the field only where it would not lie.
 */
export function outputTokens(section: PacketSection): number | null {
	return section.output_tokens === 0 ? null : section.output_tokens;
}

/** The section's figure in the row's right slot, split when an output exists. */
export function sectionTokens(section: PacketSection): string {
	const out = outputTokens(section);
	return out === null
		? section.input_tokens.toLocaleString()
		: `${section.input_tokens.toLocaleString()} in · ${out.toLocaleString()} out`;
}

/**
 * The band prints `total_tokens` — it is the record — and this says whether
 * the sections below actually sum to it. Disagreement means a newer or broken
 * daemon, and neither figure is silently picked over the other.
 */
export function totalsAgree(snapshot: PacketSnapshot): boolean {
	return (
		snapshot.sections.reduce(
			(sum, section) => sum + section.input_tokens + section.output_tokens,
			0
		) === snapshot.total_tokens
	);
}

/**
 * Empty lists round-trip as `None` rather than as absent: an operator must be
 * able to tell "the comparison ran and found no additions" from "the
 * comparison did not report additions".
 */
export function diffList(names: string[]): { items: string[]; none: boolean } {
	return { items: names.length === 0 ? ['None'] : names, none: names.length === 0 };
}
