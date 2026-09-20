import type {
	CheckpointReport,
	NodeChange,
	NodeRevision,
	Plan,
	PlanGraph,
	RecalibrationReport,
	RevisionImpact
} from './daemon';

export const CHANGE_ORDER: NodeChange[] = ['unchanged', 'edited', 'split', 'merged', 'new', 'removed'];
export type RevisionBand = 'fixed' | 'moved' | 'added' | 'unchanged';

export interface RevisionRow {
	node: NodeRevision;
	band: RevisionBand;
	state: 'seated' | 'balanced' | 'slack' | 'failed';
	selectableId: string | null;
	name: string | null;
	lineage: string;
	markers: string[];
	cardinality: string | null;
	attempts: string | null;
	cap: string | null;
	message: string;
	fixedReasons: string[];
}

export interface RevisionBandView {
	key: RevisionBand;
	label: string;
	rows: RevisionRow[];
	open: boolean;
	foot: string;
}

export function formatCap(value: number | null): string {
	return value === null ? 'no cap' : new Intl.NumberFormat().format(value);
}

function ids(ids: string[]): string {
	return ids.length ? ids.join(', ') : 'none';
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
	return `${count} ${count === 1 ? singular : pluralWord}`;
}

function capLine(node: NodeRevision): string | null {
	if (node.old_token_caps.length === 0 && node.new_token_caps.length === 0) return null;
	const same = node.old_token_caps.length === node.new_token_caps.length &&
		node.old_token_caps.every((value, index) => value === node.new_token_caps[index]);
	if (same) return null;
	const before = node.old_token_caps.map(formatCap).join(' · ') || 'none';
	const after = node.new_token_caps.map(formatCap).join(' · ') || 'none';
	return `token cap ${before} → ${after}`;
}

export function fixedIds(plan: Plan | null, reviews: CheckpointReport | null): Set<string> {
	const fixed = new Set<string>();
	if (!plan) return fixed;
	for (const [id, initiative] of Object.entries(plan.initiatives)) {
		const live = initiative.attempts.some((attempt) => attempt.ended_at === null);
		const approved = reviews?.initiatives.find((item) => item.initiative_id === id)?.approved_checkpoint_id;
		if (initiative.state === 'settled' || live || approved) fixed.add(id);
	}
	return fixed;
}

export function fixedReasons(id: string, plan: Plan | null, reviews: CheckpointReport | null): string[] {
	const initiative = plan?.initiatives[id];
	if (!initiative) return [];
	const reasons: string[] = [];
	if (initiative.state === 'settled') reasons.push('settled — its work is done and recorded.');
	if (initiative.attempts.some((attempt) => attempt.ended_at === null)) {
		reasons.push('an attempt is running — its worktree and pane are still in flight.');
	}
	if (reviews?.initiatives.find((item) => item.initiative_id === id)?.approved_checkpoint_id) {
		reasons.push('holds an approved checkpoint — consumers rest on that artifact identity.');
	}
	return reasons;
}

function bandFor(node: NodeRevision, fixed: Set<string>): RevisionBand {
	if (node.new_ids.some((id) => fixed.has(id))) return 'fixed';
	if (node.change === 'unchanged') return 'unchanged';
	if (node.change === 'new' || node.change === 'removed') return 'added';
	return 'moved';
}

function stateFor(node: NodeRevision, band: RevisionBand, impact: RevisionImpact): RevisionRow['state'] {
	if (node.edge_state === 'unresolved' || node.new_ids.some((id) => impact.stranded.includes(id))) return 'failed';
	if (band === 'fixed') return 'seated';
	if (node.change === 'removed' || node.change === 'merged') return 'slack';
	return 'balanced';
}

function messageFor(node: NodeRevision, band: RevisionBand, plan: Plan | null, impact: RevisionImpact): string {
	if (node.change === 'removed') return 'name unread — this node is no longer in the plan.';
	if (node.edge_state === 'unresolved') return 'a dependency on this node could not be traced to anything recorded on either side, so this build cannot say whether its edges survived.';
	if (node.edge_state === 'changed') return 'its dependencies are not the ones it had, though its brief, scope and claims are unchanged.';
	if (band === 'fixed') return 'the planner was given this node by digest only and was not allowed to revise it.';
	if (node.change === 'unchanged') return 'same brief, same scope, same claims, same gates.';
	if (node.change === 'new') return 'a new allocation enters the plan at this revision.';
	if (node.change === 'split') return 'its claims are now carried by separate nodes.';
	if (node.change === 'merged') return 'its claims are now carried by one node.';
	if (impact.dropped.some((id) => node.old_ids.includes(id))) return 'its old record moves to the retired plan history.';
	return plan ? 'this node changed within the plan revision.' : 'the plan did not answer; this row carries the daemon verdict only.';
}

function attemptsFor(node: NodeRevision): string | null {
	if (node.change === 'removed' && node.old_attempts > 0) return `${plural(node.old_attempts, 'recorded attempt')} move to the plan's retired record`;
	if (node.renamed && node.old_attempts === node.new_attempts && node.old_attempts > 0) {
		return `kept its ${plural(node.old_attempts, 'recorded attempt')}, its checkpoints and its failures under the new id`;
	}
	if (node.old_attempts === node.new_attempts && node.new_attempts > 0) return `kept its ${plural(node.new_attempts, 'recorded attempt')} through this revision`;
	if (node.old_attempts !== node.new_attempts && (node.old_attempts > 0 || node.new_attempts > 0)) {
		return `${node.old_attempts} recorded attempts before this revision; ${node.new_attempts} under the new id${node.new_ids.length === 1 ? '' : 's'} — see Allowances below`;
	}
	return null;
}

function markerFor(node: NodeRevision, cap: string | null): string[] {
	const markers: string[] = [];
	if (node.renamed) markers.push('RENUMBERED');
	if (node.change === 'edited') markers.push('EDITED');
	if (node.change === 'split') markers.push(`SPLIT FROM ${ids(node.old_ids)}`);
	if (node.change === 'merged') markers.push(`MERGED FROM ${ids(node.old_ids)}`);
	if (node.change === 'new') markers.push('NEW');
	if (node.edge_state === 'changed') markers.push('EDGES CHANGED');
	if (node.edge_state === 'unresolved') markers.push('EDGES UNRESOLVED');
	if (cap) markers.push('ALLOWANCE MOVED');
	return markers;
}

export function rowFor(node: NodeRevision, report: RecalibrationReport, plan: Plan | null, reviews: CheckpointReport | null): RevisionRow {
	const fixed = fixedIds(plan, reviews);
	const band = bandFor(node, fixed);
	const selectableId = node.new_ids.find((id) => Boolean(plan?.initiatives[id])) ?? null;
	const cap = capLine(node);
	const cardinality = node.change === 'split' || node.change === 'merged' ? `${node.old_ids.length} → ${node.new_ids.length}` : null;
	const name = selectableId ? plan?.initiatives[selectableId]?.spec.name ?? null : null;
	return {
		node, band, state: stateFor(node, band, report.impact), selectableId, name,
		lineage: `${ids(node.old_ids)}${node.old_ids.length && node.new_ids.length ? ' → ' : ''}${ids(node.new_ids)}`,
		markers: markerFor(node, cap), cardinality, attempts: attemptsFor(node), cap,
		message: messageFor(node, band, plan, report.impact),
		fixedReasons: selectableId ? fixedReasons(selectableId, plan, reviews) : []
	};
}

export function bandsOf(report: RecalibrationReport, graph: PlanGraph | null, plan: Plan | null, reviews: CheckpointReport | null): RevisionBandView[] {
	const fixed = fixedIds(plan, reviews);
	const rows = report.revision.nodes.map((node) => rowFor(node, report, plan, reviews));
	const fixedRows = rows.filter((row) => row.band === 'fixed');
	const moved = rows.filter((row) => row.band === 'moved');
	const added = rows.filter((row) => row.band === 'added');
	const unchanged = rows.filter((row) => row.band === 'unchanged');
	const movedAllowances = unchanged.filter((row) => row.cap).length;
	const unchangedCount = report.revision.counts.unchanged ?? unchanged.length;
	return [
		{ key: 'fixed', label: `Fixed ——— ${fixedRows.length}, not revisable`, rows: fixedRows, open: true, foot: `The planner was given these nodes by digest only and was not allowed to revise them. Nothing here reruns, and nothing here is renumbered away. The ${fixedRows.length} fixed nodes this sheet can see, and any attempt the daemon is still settling, are the smallest true answer — that attempt is fixed too, but no projection says which node it is.` },
		{ key: 'moved', label: `Moved ——— ${moved.length}`, rows: moved, open: true, foot: moved.length ? 'These existing nodes were reshaped by the daemon.' : 'This revision reshaped no existing node.' },
		{ key: 'added', label: `Added and dropped ——— ${added.length}`, rows: added, open: true, foot: report.impact.dropped.length ? `${report.impact.dropped.length} ids left the live plan; their records are retired, not deleted.` : 'Nothing left the live plan in this revision.' },
		{ key: 'unchanged', label: `Unchanged ——— ${unchanged.length} of ${unchangedCount}${fixedRows.length ? `, ${fixedRows.length} fixed above` : ''}${movedAllowances ? `, ${movedAllowances} allowance moved` : ''}`, rows: unchanged, open: movedAllowances > 0, foot: 'Renumbering is not a change. A node whose content is identical under a different id keeps its attempts, checkpoints, redirects and failures; the old id is retired and can never be given to anything else.' }
	];
}

export function downstreamSentence(impact: RevisionImpact): string {
	return impact.downstream.length ? 'Everything below what this revision touched, in the order it would run.' : 'Nothing sits below what this revision touched.';
}

export function refusalMessage(status: number | null, message: string): 'first' | 'refusal' | 'failed' {
	if (status === 409 && message.toLowerCase().includes('no revision')) return 'first';
	if (status === 409) return 'refusal';
	return 'failed';
}
