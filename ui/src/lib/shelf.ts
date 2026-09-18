/**
 * The Library's pure model: the shelf, and the closure the sheet is drawn from.
 *
 * Nothing here reads the daemon or touches the DOM, so every claim in
 * `dev/field-check.ts` is checked against the same functions the view renders.
 *
 * The one idea: an asset is never read alone. `closureOf` is the client-side
 * twin of `Library.resolve` in `herdsman/library.py` — depth-first from the
 * root, each ref emitted once, cycles broken and reported rather than followed —
 * and it exists because the daemon's `POST /library/validate` returns the
 * *findings* for a closure but not the closure itself, and the sheet has to draw
 * the walk. The findings stay the daemon's: what this file decides is order,
 * depth and each ring's state, all of it from the same `references` the daemon
 * walked.
 */

import type { AssetKind, AssetOrigin, AssetStatus, AssetSummary, LibraryIssue } from './daemon';

/**
 * Shelf order. Not alphabetical: it is the order the pipeline uses them in —
 * a role is filled by an agent under a contract, which uses skills, and a
 * checkpoint template is what the work hands back.
 */
export const KINDS: readonly AssetKind[] = [
	'role',
	'contract',
	'agent',
	'skill',
	'checkpoint-template'
];

/** What each kind is called on the shelf, and what it is for in one line. */
export const KIND_WORD: Record<AssetKind, string> = {
	role: 'Roles',
	contract: 'Contracts',
	agent: 'Agents',
	skill: 'Skills',
	'checkpoint-template': 'Checkpoint templates',
	'memory-leaf': 'Memory'
};

export const KIND_GLOSS: Record<AssetKind, string> = {
	role: 'A named capability an initiative is assigned to.',
	contract: 'What a checkpoint must show before the work can settle.',
	agent: 'The persona and standing instructions an executor carries.',
	skill: 'A procedure an executor can be handed for one job.',
	'checkpoint-template': 'The shape of the handoff document a role returns.',
	'memory-leaf': 'A remembered claim and its evidence.'
};

export const ORIGIN_WORD: Record<AssetOrigin, string> = {
	bundled: 'Bundled',
	project: 'Project'
};

export const STATUS_WORD: Record<AssetStatus, string> = {
	active: 'Active',
	stale: 'Stale',
	conflicted: 'Conflicted',
	retired: 'Retired'
};

/** The member state an asset's own status reads as. */
export function statusState(status: AssetStatus): string {
	switch (status) {
		case 'active':
			return 'seated';
		case 'retired':
			return 'slack';
		case 'conflicted':
			return 'failed';
		case 'stale':
			return 'slack';
	}
}

/** Why a ref is in the closure, and whether its load path is whole. */
export type LinkState = 'root' | 'present' | 'retired' | 'missing' | 'cycle';

export interface ClosureNode {
	ref: string;
	/** How far from the root, for the chain's indent. Root is 0. */
	depth: number;
	state: LinkState;
	/** Absent for `missing` and `cycle`, which have no asset to show. */
	asset: AssetSummary | null;
	/** The path that reached it, for a finding that has to say where from. */
	via: string[];
	/** This asset's own cost; 0 where there is no asset. */
	tokens: number;
	/** Every token in the closure up to and including this node. */
	running: number;
}

export interface Closure {
	root: string;
	nodes: ClosureNode[];
	/** The whole closure's effective context cost. */
	tokens: number;
	/** Refs the walk could not resolve, in walk order. */
	missing: string[];
	/** Refs that resolve to an archived asset, in walk order. */
	retired: string[];
	/** Cycle back-edges, in walk order. */
	cycles: string[];
}

/**
 * The root plus everything it references, depth-first, each ref once.
 *
 * `index` must hold every *readable* asset, retired included — browse the shelf
 * with `status=all` and filter for display. An index that already dropped
 * retired assets would report a live archived reference as missing, which is a
 * different finding with a different fix.
 */
export function closureOf(root: string, index: Map<string, AssetSummary>): Closure {
	const nodes: ClosureNode[] = [];
	const seen = new Set<string>();
	const missing: string[] = [];
	const retired: string[] = [];
	const cycles: string[] = [];
	let running = 0;

	const walk = (ref: string, depth: number, trail: string[]) => {
		if (trail.includes(ref)) {
			cycles.push(ref);
			nodes.push({
				ref,
				depth,
				state: 'cycle',
				asset: null,
				via: [...trail],
				tokens: 0,
				running
			});
			return;
		}
		if (seen.has(ref)) return;

		const asset = index.get(ref);
		if (asset === undefined) {
			// Marked seen like any other ref. Two assets in one closure can declare
			// the same broken reference, and it is still *one* ref that resolves to
			// nothing: emitting it twice would draw two rings, stack the same
			// "this reference is broken" block twice, and make the readout's count
			// a count of declarations rather than of broken references. The
			// daemon's findings still name each declaring path, because each
			// carries its own trail in `detail`.
			seen.add(ref);
			missing.push(ref);
			nodes.push({
				ref,
				depth,
				state: 'missing',
				asset: null,
				via: [...trail],
				tokens: 0,
				running
			});
			return;
		}

		seen.add(ref);
		if (asset.status === 'retired') retired.push(ref);
		running += asset.tokens;
		nodes.push({
			ref,
			depth,
			state:
				trail.length === 0 ? 'root' : asset.status === 'retired' ? 'retired' : 'present',
			asset,
			via: [...trail],
			tokens: asset.tokens,
			running
		});

		// A memory leaf's references are its evidence, not assets, so the walk
		// stops there — exactly where `Library.resolve` stops.
		if (asset.kind === 'memory-leaf') return;
		for (const child of asset.references) walk(child, depth + 1, [...trail, ref]);
	};

	walk(root, 0, []);
	return { root, nodes, tokens: running, missing, retired, cycles };
}

/** Which assets name this one — the other direction, which references cannot answer. */
export function referencedBy(ref: string, rows: readonly AssetSummary[]): string[] {
	return rows.filter((row) => row.references.includes(ref)).map((row) => row.ref);
}

export interface ShelfFilter {
	kind: AssetKind | 'all';
	origin: AssetOrigin | 'all';
	/** `active` is the shelf's own default; `all` includes retired assets. */
	status: AssetStatus | 'all';
	query: string;
}

export const ALL: ShelfFilter = { kind: 'all', origin: 'all', status: 'active', query: '' };

/**
 * The shelf, filtered.
 *
 * `query` matches ref and title case-folded, the same two fields the daemon's
 * own `browse` matches, so typing here and passing `?query=` would agree. It
 * filters a list already on screen; it never names an asset, which is what
 * `notes/ui/contract.md` refuses a text field for.
 */
export function filterShelf(
	rows: readonly AssetSummary[],
	filter: ShelfFilter
): AssetSummary[] {
	const needle = filter.query.trim().toLowerCase();
	return rows.filter((row) => {
		if (filter.kind !== 'all' && row.kind !== filter.kind) return false;
		if (filter.origin !== 'all' && row.origin !== filter.origin) return false;
		if (filter.status !== 'all' && row.status !== filter.status) return false;
		if (needle !== '' && !`${row.ref} ${row.title}`.toLowerCase().includes(needle))
			return false;
		return true;
	});
}

export interface KindGroup {
	kind: AssetKind;
	rows: AssetSummary[];
}

/** The shelf grouped under its kind labels, kinds with nothing in them dropped. */
export function groupByKind(rows: readonly AssetSummary[]): KindGroup[] {
	const groups: KindGroup[] = [];
	for (const kind of KINDS) {
		const held = rows.filter((row) => row.kind === kind);
		if (held.length > 0) groups.push({ kind, rows: held });
	}
	// A kind outside the shelf's five still gets a group rather than vanishing;
	// the daemon's kind set is closed, but this surface does not enforce it.
	const rest = rows.filter((row) => !KINDS.includes(row.kind));
	if (rest.length > 0) groups.push({ kind: rest[0].kind, rows: [...rest] });
	return groups;
}

/** Issues that name one ref, so a node can carry its own findings. */
export function issuesFor(ref: string, issues: readonly LibraryIssue[]): LibraryIssue[] {
	return issues.filter((issue) => issue.ref === ref);
}

/** Findings about the set rather than any one asset in it. */
export function setIssues(root: string, issues: readonly LibraryIssue[]): LibraryIssue[] {
	return issues.filter((issue) => issue.code === 'context-size' || issue.ref === root);
}

/** Has the live shelf moved away from what an approval froze? */
export type Drift = 'same' | 'edited' | 'gone';

export interface FrozenRow {
	ref: string;
	kind: AssetKind;
	name: string;
	title: string;
	origin: AssetOrigin;
	references: string[];
	body: string;
	digest: string;
	tokens: number;
	drift: Drift;
	/** The live asset's revision, when one still exists and differs. */
	liveDigest: string | null;
}

/**
 * One approved plan version's frozen set, compared against the live shelf.
 *
 * The snapshot never changes — that is what makes it a snapshot — so every
 * difference is the shelf's. `edited` means the asset is still there under a
 * different revision; `gone` means nothing on the shelf answers to that ref any
 * more. Neither is an error: an approval froze bytes precisely so a later edit
 * could not reach backwards into it.
 */
export function compareFrozen(
	frozen: readonly {
		ref: string;
		kind: AssetKind;
		name: string;
		title: string;
		origin: AssetOrigin;
		references: string[];
		body: string;
		digest: string;
		tokens: number;
	}[],
	index: Map<string, AssetSummary>
): FrozenRow[] {
	return frozen.map((asset) => {
		const live = index.get(asset.ref);
		const drift: Drift =
			live === undefined ? 'gone' : live.digest === asset.digest ? 'same' : 'edited';
		return {
			...asset,
			references: [...asset.references],
			drift,
			liveDigest: drift === 'edited' && live ? live.digest : null
		};
	});
}

/** The member state a drift reads as: unchanged is seated, moved on is slack. */
export function driftState(drift: Drift): string {
	return drift === 'same' ? 'seated' : 'slack';
}

export const DRIFT_WORD: Record<Drift, string> = {
	same: 'Matches the shelf',
	edited: 'Shelf has since changed',
	gone: 'No longer on the shelf'
};

/** Thousands separators, the one spelling of a count in this build. */
export const count = (n: number): string => n.toLocaleString();
