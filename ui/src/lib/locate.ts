/**
 * The Index Band's model (unit F2): one flat index of everything the band can
 * navigate to, built from enumerations that already exist.
 *
 * The one rule that shapes every address here: a deep link is never composed
 * where the daemon already wrote one. Runs and attention items carry
 * `link.path` and it is used verbatim; only members and checkpoints, which no
 * route addresses, are composed here — from the exact shape `runTarget()`
 * already parses — and assets from the shape `daemon.asset` already encodes.
 *
 * Matching is a case-folded substring over mark and gloss, nothing fuzzier: a
 * fuzzy matcher is a dependency and a surprise, and an operator typing a run
 * id wants that run id.
 *
 * Everything here is pure — the imports are types only, so nothing exists at
 * runtime — and every claim this file makes is asserted in
 * `ui/dev/field-check.ts`.
 */

import type { AssetSummary, CheckpointReport, Fleet, PlanGraph, RunRollup } from './daemon';
import type { MemberState } from './field';
import type { View } from './views';

export type LocateKind = 'attention' | 'view' | 'run' | 'member' | 'checkpoint' | 'asset';

export interface LocateRow {
	kind: LocateKind;
	/** Stable, unique across the whole index; used for the option element id. */
	key: string;
	/** The identifier column: run id, initiative id, checkpoint id, asset ref, view name. */
	mark: string;
	/** One line of context: a brief, a name, an asset's kind, a view's purpose. */
	gloss: string;
	/** The right-hand cell: a state word, "archived", a chord, or ''. */
	state: string;
	/** The member state vocabulary for that cell: slack|balanced|loaded|seated|failed. */
	memberState: MemberState;
	/** The address. Already substituted; never a template. */
	path: string;
}

export interface LocateGroup {
	kind: LocateKind;
	label: string;
	rows: LocateRow[];
	total: number;
}

export type LocateSources = {
	views: readonly View[];
	fleet?: Fleet | null;
	archived?: Fleet | null;
	assets?: readonly AssetSummary[] | null;
	graph?: PlanGraph | null;
	report?: CheckpointReport | null;
	planId?: string | null;
};

/**
 * The `g` chords beside the views, so they live in code rather than folklore.
 * The shell reads this table for the `g`-then-key chord; the band prints the
 * chord in each view row's state cell.
 */
export const CHORDS: Record<'r' | 'h' | 'l' | 'k', View['id']> = {
	r: 'run',
	h: 'home',
	l: 'library',
	k: 'kitchen'
};

/** The chord a view is reached by, printed in its state cell. */
function chordOf(id: View['id']): string {
	for (const [key, value] of Object.entries(CHORDS)) {
		if (value === id) return `g ${key}`;
	}
	return '';
}

/** The member state one run row reads at. */
function runStateOf(run: RunRollup, isArchived: boolean): { word: string; state: MemberState } {
	const word = `${run.status.replace(/_/g, ' ')}${isArchived ? ' · archived' : ''}`;
	if (isArchived) return { word, state: 'slack' }; // out of active navigation: no load either way
	switch (run.status) {
		case 'running':
			return { word, state: 'loaded' };
		case 'failed':
			return { word, state: 'failed' };
		case 'settled':
			return { word, state: 'seated' };
		default:
			return { word, state: 'balanced' };
	}
}

/** The member state one attention kind reads at. Red is load only: a run
 *  whose path is discontinuous failed, and everything else — including an
 *  unknown kind from a newer daemon — is waiting on the operator, not failing. */
function attentionStateOf(kind: string): MemberState {
	switch (kind) {
		case 'failed':
		case 'stalled':
			return 'failed';
		default:
			return 'slack';
	}
}

/** The member state one plan member reads at, on the field's own mapping. */
function memberStateOf(node: PlanGraph['nodes'][number]): MemberState {
	switch (node.state) {
		case 'running':
			return 'loaded';
		case 'settled':
			return 'seated';
		case 'failed':
			return 'failed';
		case 'cancelled':
			return 'slack';
		default:
			return 'balanced';
	}
}

/** The member state one checkpoint decision reads at. */
function decisionStateOf(decision: CheckpointReport['initiatives'][number]['versions'][number]['decision']): MemberState {
	switch (decision) {
		case 'approved':
			return 'seated';
		case 'rejected':
		case 'changes_requested':
			return 'failed';
		default:
			return 'balanced';
	}
}

/** The member state one asset status reads at, as the register draws it. */
function assetStateOf(status: AssetSummary['status']): MemberState {
	switch (status) {
		case 'active':
			return 'seated';
		case 'retired':
			return 'slack';
		default:
			return 'failed'; // stale and conflicted: the shelf's own load path is broken
	}
}

/**
 * The whole index, in the band's listing order: what needs you, the views,
 * the runs, then the members and checkpoints of the plan currently addressed.
 * A group with no rows is dropped by `groupRows`, never drawn empty.
 */
export function buildIndex({
	views,
	fleet,
	archived,
	assets,
	graph,
	report,
	planId
}: LocateSources): LocateRow[] {
	const rows: LocateRow[] = [];

	/* The daemon's own order — oldest first — and its own deep links, verbatim.
	   Only blocking items are indexed: the attention feed is H2's surface, and
	   a locator is not a second notification list. */
	for (const item of fleet?.attention ?? []) {
		if (!item.blocking) continue;
		rows.push({
			kind: 'attention',
			key: `attention:${item.key}`,
			mark: item.plan_id,
			gloss: item.summary,
			state: item.kind.replace(/_/g, ' '),
			memberState: attentionStateOf(item.kind),
			path: item.link.path
		});
	}

	for (const view of views) {
		rows.push({
			kind: 'view',
			key: `view:${view.id}`,
			mark: view.name,
			gloss: view.purpose,
			state: chordOf(view.id),
			memberState: 'balanced',
			path: view.href
		});
	}

	for (const run of fleet?.runs ?? []) {
		const { word, state } = runStateOf(run, false);
		rows.push({
			kind: 'run',
			key: `run:${run.plan_id}`,
			mark: run.plan_id,
			gloss: run.brief,
			state: word,
			memberState: state,
			path: run.link.path
		});
	}
	for (const run of archived?.runs ?? []) {
		const { word, state } = runStateOf(run, true);
		rows.push({
			kind: 'run',
			key: `run-archived:${run.plan_id}`,
			mark: run.plan_id,
			gloss: run.brief,
			state: word,
			memberState: state,
			path: run.link.path
		});
	}

	/* Only members and checkpoints are composed here, because no route
	   addresses them; the composition is the shape `runTarget()` already parses. */
	/* Guarded on the id as well as the graph, exactly as the checkpoints below
	   are: a member address without a plan is not a weaker link, it is a link to
	   the unaddressed Run view wearing an initiative id. */
	if (graph && planId) {
		for (const node of graph.nodes) {
			const ready = node.state === 'pending' && node.ready;
			rows.push({
				kind: 'member',
				key: `member:${node.initiative_id}`,
				mark: node.initiative_id,
				gloss: node.name,
				state: `${node.state}${ready ? ', ready' : ''}`,
				memberState: memberStateOf(node),
				path: `/run?plan=${encodeURIComponent(planId)}&initiative=${encodeURIComponent(node.initiative_id)}`
			});
		}
	}

	if (planId && report) {
		for (const initiative of report.initiatives) {
			for (const version of initiative.versions) {
				rows.push({
					kind: 'checkpoint',
					key: `checkpoint:${version.checkpoint_id}`,
					mark: version.checkpoint_id,
					gloss: `${initiative.initiative_id} · v${version.version} · ${version.decision}`,
					state: version.decision.replace(/_/g, ' '),
					memberState: decisionStateOf(version.decision),
					path: `/run?plan=${encodeURIComponent(planId)}&initiative=${encodeURIComponent(initiative.initiative_id)}&checkpoint=${encodeURIComponent(version.checkpoint_id)}`
				});
			}
		}
	}

	for (const summary of assets ?? []) {
		rows.push({
			kind: 'asset',
			key: `asset:${summary.ref}`,
			mark: summary.ref,
			gloss: summary.title !== '' ? summary.title : summary.name,
			state: summary.status,
			memberState: assetStateOf(summary.status),
			path: `/library?asset=${summary.ref
				.split('/')
				.map((part) => encodeURIComponent(part))
				.join('/')}`
		});
	}

	return rows;
}

/**
 * Case-folded substring over mark then gloss, ranked: a mark that starts with
 * the query, then a mark that contains it, then a gloss match; ties keep the
 * daemon's own order. An empty query is the identity. Nothing fuzzy.
 */
export function filterRows(rows: LocateRow[], query: string): LocateRow[] {
	const q = query.toLowerCase();
	if (q === '') return rows;
	const ranked: { at: number; rank: number; row: LocateRow }[] = [];
	rows.forEach((row, at) => {
		const mark = row.mark.toLowerCase();
		const rank = mark.startsWith(q)
			? 0
			: mark.includes(q)
				? 1
				: row.gloss.toLowerCase().includes(q)
					? 2
					: -1;
		if (rank !== -1) ranked.push({ at, rank, row });
	});
	return ranked.sort((a, b) => a.rank - b.rank || a.at - b.at).map((entry) => entry.row);
}

const KIND_ORDER: readonly LocateKind[] = ['attention', 'view', 'run', 'member', 'checkpoint', 'asset'];

const KIND_LABEL: Record<LocateKind, string> = {
	attention: 'Waiting on you',
	view: 'View',
	run: 'Run',
	member: 'Member',
	checkpoint: 'Checkpoint',
	asset: 'Asset'
};

/**
 * The listing groups, in the fixed kind order, empties dropped, each capped
 * at `cap` rows with the true total carried beside it for the band's `n of m`.
 */
export function groupRows(rows: LocateRow[], cap = 6): LocateGroup[] {
	const groups: LocateGroup[] = [];
	for (const kind of KIND_ORDER) {
		const all = rows.filter((row) => row.kind === kind);
		if (all.length === 0) continue;
		groups.push({ kind, label: KIND_LABEL[kind], rows: all.slice(0, cap), total: all.length });
	}
	return groups;
}

/**
 * Move by one with wrap at both ends — `field.ts`'s roving step idea, but a
 * band wraps where a roving tab stop clamps. Unknown or null key enters the
 * list at the end the movement points at.
 */
export function step(rows: LocateRow[], activeKey: string | null, delta: number): string | null {
	if (rows.length === 0) return null;
	const at = activeKey === null ? -1 : rows.findIndex((row) => row.key === activeKey);
	if (at === -1) return rows[delta > 0 ? 0 : rows.length - 1].key;
	return rows[(at + delta + rows.length) % rows.length].key;
}
