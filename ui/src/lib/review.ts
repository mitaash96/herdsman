/**
 * Checkpoint review's model: what one initiative's evidence shows, what has
 * been decided about it, and what a verdict does to everyone downstream.
 *
 * R1's `field.ts` answers *where* a member sits. R3's `gate.ts` answers what a
 * decomposition commits you to. This answers the third question: is this piece
 * of work acceptable, and who is waiting on the answer.
 *
 * Two joins carry the weight, and both exist because no single daemon route
 * holds the whole picture:
 *
 * - **Manifest × decision.** `GET /plans/{id}` serialises every preserved
 *   `Checkpoint` in full — checks, caveats, shas, the patch reference. `GET
 *   /plans/{id}/checkpoints` serialises the review *lifecycle* — version
 *   number, decision, reviewer, reason, supersession. `versionsOf` joins them
 *   by checkpoint id, and says plainly when one side is missing rather than
 *   filling the gap with a default.
 * - **Verdict × consumers.** Approval is what releases a gated node's
 *   dependents (`Plan._releases_consumers`), so a verdict is never local. The
 *   downstream sentence is computed from the graph the operator is looking at,
 *   naming the exact members, never "downstream work may be affected".
 *
 * What is deliberately *not* here: any claim about file content. The daemon
 * projects `changes_since_approved` as a set subtraction over path names and
 * serves no route returning artifact bytes, so every comparison in this file is
 * a change list and is labelled as one. R4 is partial by that measure, on
 * purpose — see the surface brief.
 *
 * Everything here is pure. `demo()` in `dev/field-check.ts` is the runnable check.
 */

import type {
	Checkpoint,
	CheckpointReport,
	CheckpointVersionView,
	CheckResult,
	Contract,
	Decision,
	Initiative,
	InitiativeReviewView,
	PlanGraph,
	Taint,
	Verdict
} from './daemon';

/* --- one version, both halves ------------------------------------------- */

/**
 * A preserved checkpoint version: its decision from the report, its evidence
 * from the fold. Either half can be absent — the two reads are independent and
 * one of them can fail — so both are nullable and the view says which is unread.
 */
export interface Version {
	/** 1-based record order. `null` when only the fold answered. */
	number: number | null;
	id: string;
	attempt_id: string;
	decision: Decision;
	decided_at: string | null;
	decided_by: string;
	reason: string;
	approved_at: string | null;
	superseded: boolean;
	/** The manifest. `null` when the folded plan is unread — not an empty one. */
	manifest: Checkpoint | null;
	/** True when the review lifecycle is unread and `decision` is a placeholder. */
	unread: boolean;
}

/** The member-state vocabulary, applied to a review decision. */
export const DECISION_STATE: Record<Decision, string> = {
	pending: 'balanced',
	approved: 'seated',
	rejected: 'failed',
	changes_requested: 'failed'
};

/** What each decision is called on screen. Never an id, never a verb. */
export const DECISION_WORD: Record<Decision, string> = {
	pending: 'Awaiting review',
	approved: 'Approved',
	rejected: 'Rejected',
	changes_requested: 'Changes requested'
};

export function versionsOf(
	initiative: Initiative | null,
	view: InitiativeReviewView | null
): Version[] {
	const manifests = new Map<string, Checkpoint>(
		(initiative?.checkpoint_versions ?? []).map((c) => [c.id, c])
	);
	if (view) {
		return view.versions.map((v) => ({
			number: v.version,
			id: v.checkpoint_id,
			attempt_id: v.attempt_id,
			decision: v.decision,
			decided_at: v.decided_at,
			decided_by: v.decided_by,
			reason: v.reason,
			approved_at: v.approved_at,
			superseded: v.superseded,
			manifest: manifests.get(v.checkpoint_id) ?? null,
			unread: false
		}));
	}
	/* The review read failed and the fold answered. The evidence is real; the
	   decision is not known, and `pending` would be a claim. */
	return (initiative?.checkpoint_versions ?? []).map((c, at) => ({
		number: at + 1,
		id: c.id,
		attempt_id: c.attempt_id,
		decision: 'pending' as Decision,
		decided_at: null,
		decided_by: '',
		reason: '',
		approved_at: null,
		superseded: at < (initiative?.checkpoint_versions.length ?? 0) - 1,
		manifest: c,
		unread: true
	}));
}

/* --- the manifest: what the evidence actually shows ---------------------- */

/** One required check and whether this version's evidence satisfies it. */
export interface CheckRow {
	name: string;
	/** Required by the contract, as opposed to run on the executor's own account. */
	required: boolean;
	/** `null` means it never ran — which is a violation, not a pass. */
	result: CheckResult | null;
}

/**
 * Every check that bears on this version, required ones first and in the
 * contract's own order, then anything else the attempt ran.
 *
 * A required check that never ran is the case worth the trouble: it is a
 * `missing-check` violation and reads nothing like a failure, so it carries a
 * null result rather than a synthesised failed one.
 */
export function checksOf(manifest: Checkpoint | null, contract: Contract | null): CheckRow[] {
	if (!manifest) return [];
	const ran = new Map(manifest.checks.map((c) => [c.name, c]));
	const required = contract?.required_checks ?? [];
	const rows: CheckRow[] = required.map((name) => ({
		name,
		required: true,
		result: ran.get(name) ?? null
	}));
	const named = new Set(required);
	for (const check of manifest.checks) {
		if (!named.has(check.name)) rows.push({ name: check.name, required: false, result: check });
	}
	return rows;
}

/** One required artifact and whether the version's changed paths carry it. */
export interface ArtifactRow {
	path: string;
	required: boolean;
	present: boolean;
}

/**
 * The version's artifacts: the contract's required paths first, marked present
 * or missing, then every other path this attempt touched.
 */
export function artifactsOf(manifest: Checkpoint | null, contract: Contract | null): ArtifactRow[] {
	if (!manifest) return [];
	const touched = new Set(manifest.changed_paths);
	const required = contract?.required_paths ?? [];
	const rows: ArtifactRow[] = required.map((path) => ({
		path,
		required: true,
		present: touched.has(path)
	}));
	const named = new Set(required);
	for (const path of manifest.changed_paths) {
		if (!named.has(path)) rows.push({ path, required: false, present: true });
	}
	return rows;
}

/* --- comparison against the approved version ----------------------------- */

/**
 * How one version's paths compare with the approved base.
 *
 * The daemon's own `changes_since_approved` is `set(latest) - set(approved)`:
 * the *added* half only. A path the approved version touched and this one does
 * not is just as much a change, and reading a field called "changes since
 * approved" as complete when it is one-sided is the kind of quiet wrongness
 * this build refuses. So all three sets are computed here from the two
 * versions' own `changed_paths`, and the view says which half the daemon
 * projects.
 *
 * None of this is a content diff. Two versions can touch exactly the same
 * paths with entirely different bytes, and this comparison would be empty.
 */
export interface Changes {
	/** In this version, not in the approved one. */
	added: string[];
	/** In both. Their *content* may differ; nothing here can tell you. */
	carried: string[];
	/** In the approved version, not in this one. */
	dropped: string[];
	/** The version this was read against, or `null` when none was ever approved. */
	base: number | null;
	/** True when both versions touch exactly the same paths. */
	identical: boolean;
}

export function changesOf(
	version: Version,
	base: Version | null
): Changes | null {
	if (!version.manifest || !base?.manifest || base.id === version.id) return null;
	const now = new Set(version.manifest.changed_paths);
	const then = new Set(base.manifest.changed_paths);
	const added = [...now].filter((p) => !then.has(p)).sort();
	const carried = [...now].filter((p) => then.has(p)).sort();
	const dropped = [...then].filter((p) => !now.has(p)).sort();
	return {
		added,
		carried,
		dropped,
		base: base.number,
		identical: added.length === 0 && dropped.length === 0
	};
}

/* --- what a verdict is allowed to be ------------------------------------- */

/**
 * The verdicts the fold will accept on a version in this decision state.
 *
 * These are `Plan._apply`'s own rules, not a UI convention, and they are not
 * symmetrical: an approval can be withdrawn by rejecting it (which is what
 * taints the consumers it released), but a rejection is final — the answer to
 * refused evidence is a revised version, not a change of mind. Changes can be
 * requested only while nothing has been decided.
 */
export function allowed(decision: Decision): Verdict[] {
	switch (decision) {
		case 'pending':
			return ['approve', 'changes', 'reject'];
		case 'changes_requested':
			return ['approve', 'reject'];
		case 'approved':
			return ['reject'];
		case 'rejected':
			return [];
	}
}

/** Why a verdict is not on offer. One sentence, naming the rule. */
export function refused(decision: Decision, verdict: Verdict): string | null {
	if (allowed(decision).includes(verdict)) return null;
	if (decision === 'rejected') {
		return 'This version was rejected. The fold refuses every further verdict on it — refused evidence is answered with a revised version, not a second opinion.';
	}
	if (verdict === 'approve') {
		return 'Already approved. Approval is appended once and the daemon refuses a second.';
	}
	return 'Changes can be requested only while review is still pending; this version has been decided.';
}

export const VERDICT_WORD: Record<Verdict, string> = {
	approve: 'Approve',
	changes: 'Request changes',
	reject: 'Reject'
};

/* --- who is downstream, and what a verdict does to them ------------------ */

export interface Consumer {
	id: string;
	name: string;
	/** Direct dependent, or reached through one. */
	direct: boolean;
	/** Its other dependencies that have not settled — it waits on these too. */
	alsoWaitingOn: string[];
	/** Taints already recorded against it that name this initiative's evidence. */
	tainted: Taint[];
}

/**
 * Everyone whose start this initiative gates, in the graph's own order.
 *
 * Readiness is a conjunction — `Plan.ready()` requires *every* dependency
 * settled and releasing — so a direct consumer with other unsettled
 * dependencies does not become ready on this approval alone, and saying it
 * would is the sort of small lie that costs an operator an hour.
 */
export function consumersOf(
	graph: PlanGraph,
	initiativeId: string,
	attention: Taint[]
): Consumer[] {
	const dependents = new Map<string, string[]>();
	for (const node of graph.nodes) {
		for (const on of node.depends_on) {
			dependents.set(on, [...(dependents.get(on) ?? []), node.initiative_id]);
		}
	}
	const settled = new Set(
		graph.nodes.filter((n) => n.state === 'settled').map((n) => n.initiative_id)
	);
	const byId = new Map(graph.nodes.map((n) => [n.initiative_id, n]));
	const direct = new Set(dependents.get(initiativeId) ?? []);

	const seen = new Set<string>([initiativeId]);
	const queue = [...direct];
	const reached: string[] = [];
	while (queue.length > 0) {
		const id = queue.shift();
		if (id === undefined || seen.has(id)) continue;
		seen.add(id);
		reached.push(id);
		queue.push(...(dependents.get(id) ?? []));
	}

	/* The graph's own node order, so the list reads in the same sequence as the
	   schedule the operator just came from. */
	const order = graph.nodes.map((n) => n.initiative_id).filter((id) => reached.includes(id));
	return order.map((id) => {
		const node = byId.get(id);
		return {
			id,
			name: node?.name ?? id,
			direct: direct.has(id),
			alsoWaitingOn: (node?.depends_on ?? []).filter(
				(on) => on !== initiativeId && !settled.has(on)
			),
			tainted: attention.filter((t) => t.initiative_id === id && t.producer_id === initiativeId)
		};
	});
}

/**
 * The sentence shown when a verdict is armed: exactly what pressing confirm
 * does, in members the operator can see on the field behind this sheet.
 *
 * `settles` is whether this verdict ends the initiative's run — true only when
 * approving the *current* version of an initiative the daemon is waiting on.
 */
export function impactOf(
	verdict: Verdict,
	options: {
		initiativeId: string;
		version: number | null;
		state: string;
		current: boolean;
		consumers: Consumer[];
		approvedVersion: number | null;
	}
): string[] {
	const { initiativeId, version, state, current, consumers, approvedVersion } = options;
	const label = version === null ? 'this version' : `version ${version}`;
	const direct = consumers.filter((c) => c.direct);
	const names = (list: Consumer[]) =>
		list.length === 1 ? list[0].id : list.map((c) => c.id).join(', ');
	const lines: string[] = [];

	if (verdict === 'approve') {
		if (!current) {
			lines.push(
				`Approving ${label} records a decision on evidence that has already been superseded. It does not settle ${initiativeId} — only the current version can — and it does not release anything.`
			);
		} else if (state === 'running' || state === 'failed') {
			lines.push(
				`Approving ${label} settles ${initiativeId} in the same action: it is the current version and the daemon is holding the initiative for this decision.`
			);
		} else {
			lines.push(
				`Approving ${label} makes it the standing evidence for ${initiativeId}. The initiative is already ${state}, so nothing about its own run changes.`
			);
		}
		if (direct.length === 0) {
			lines.push('Nothing in this plan depends on it, so no member is waiting on this answer.');
		} else {
			const freed = direct.filter((c) => c.alsoWaitingOn.length === 0);
			const held = direct.filter((c) => c.alsoWaitingOn.length > 0);
			if (freed.length > 0) {
				lines.push(
					`${names(freed)} ${freed.length === 1 ? 'depends' : 'depend'} on ${initiativeId} and ${freed.length === 1 ? 'has' : 'have'} no other unsettled dependency, so ${freed.length === 1 ? 'it becomes' : 'they become'} ready to run.`
				);
			}
			for (const c of held) {
				lines.push(
					`${c.id} depends on ${initiativeId} but also waits on ${c.alsoWaitingOn.join(', ')}, so it does not become ready on this approval alone.`
				);
			}
		}
		if (approvedVersion !== null && approvedVersion !== version) {
			lines.push(
				`Version ${approvedVersion} stops being the standing evidence. Anything that already ran against it keeps its recorded work and is listed in the plan's attention until it runs again.`
			);
		}
		lines.push('Approval is recorded once and cannot be withdrawn except by rejecting it.');
		return lines;
	}

	const verb = verdict === 'reject' ? 'Rejecting' : 'Requesting changes on';
	if (state === 'running') {
		lines.push(
			`${verb} ${label} ends the run: the fold marks ${initiativeId} failed, because the attempt that produced this evidence is over and the initiative would otherwise sit awaiting a review that has happened.`
		);
	} else {
		lines.push(
			`${verb} ${label} leaves ${initiativeId} unsettled, so it releases nothing.`
		);
	}
	if (direct.length === 0) {
		lines.push('Nothing in this plan depends on it, so nothing downstream is held by this.');
	} else {
		lines.push(
			`${names(direct)} ${direct.length === 1 ? 'depends' : 'depend'} on ${initiativeId} and cannot start while its evidence stands refused.`
		);
	}
	const exposed = consumers.filter((c) => c.tainted.length > 0);
	if (approvedVersion !== null) {
		lines.push(
			`Version ${approvedVersion} was approved, so anything that already ran built on it. Refusing the current version does not undo that work${exposed.length > 0 ? ` — ${names(exposed)} already ${exposed.length === 1 ? 'rests' : 'rest'} on evidence the plan has flagged` : ''}; it stays recorded and stays listed in the plan's attention until it runs again on evidence that stands.`
		);
	}
	lines.push(
		verdict === 'reject'
			? 'Rejection is final for this version and the fold refuses every later verdict on it. Nothing is deleted: it stays here, with your reason, and the implementer answers it by recording a revised version.'
			: 'Nothing is deleted and nothing is final: the implementer answers by recording a revised version, which appends as the next one.'
	);
	return lines;
}

/* --- the section's own headline ----------------------------------------- */

export interface Summary {
	/** The ruled label's right-hand reading. */
	word: string;
	/** Which member state it is drawn in. */
	state: string;
	/** True when a decision is genuinely outstanding on the current version. */
	awaiting: boolean;
}

export function summarize(
	versions: Version[],
	view: InitiativeReviewView | null,
	policy: string
): Summary {
	if (versions.length === 0) {
		return { word: 'None recorded', state: 'slack', awaiting: false };
	}
	const latest = versions[versions.length - 1];
	if (latest.unread) {
		return { word: `${versions.length} unread`, state: 'slack', awaiting: false };
	}
	const label = latest.number === null ? 'Latest' : `v${latest.number}`;
	/* An automatic initiative settles on clean evidence without a reviewer, so
	   its pending decision is not a queue of one — it is a decision nobody was
	   ever going to be asked for. */
	if (latest.decision === 'pending' && policy !== 'required') {
		return { word: `${label} · no review required`, state: 'balanced', awaiting: false };
	}
	return {
		word: `${label} · ${DECISION_WORD[latest.decision].toLowerCase()}`,
		state: DECISION_STATE[latest.decision],
		awaiting: view?.awaiting_review ?? latest.decision === 'pending'
	};
}

/** This initiative's row in the plan-wide report, or null when it has none. */
export function reviewOf(
	report: CheckpointReport | null,
	initiativeId: string | null
): InitiativeReviewView | null {
	if (!report || !initiativeId) return null;
	return report.initiatives.find((i) => i.initiative_id === initiativeId) ?? null;
}
