/**
 * The one runnable check behind the Contention Field's model.
 *
 *     node ui/dev/field-check.ts
 *
 * Node strips the types; `field.ts` imports nothing at runtime, so this needs
 * no bundler and no test framework. What it asserts is the claim the whole
 * view rests on: the lane count is the plan's real parallelism ceiling, and
 * every lane is a chain that genuinely cannot overlap itself. If that breaks,
 * the drawing starts lying about concurrency.
 */

import { buildField, phaseOf, runTarget, step } from '../src/lib/field.ts';
import { CHORDS, buildIndex, filterRows, groupRows, step as stepRows, type LocateRow } from '../src/lib/locate.ts';
import { because, calloutsOf, downstream, lead, registerOf, shapeOf } from '../src/lib/gate.ts';
import {
	answeredFromMemory,
	answerableLeaves,
	carriedLeaves,
	currentVerdict,
	memoryRun,
	receiptsFor,
	salvageAvailability,
	salvageEvidence
} from '../src/lib/memory.ts';
import {
	allowed,
	artifactsOf,
	changesOf,
	checksOf,
	consumersOf,
	impactOf,
	summarize,
	versionsOf,
	walkthroughOf,
	type Version
} from '../src/lib/review.ts';
import {
	codeLike,
	commonProvenance,
	comparableAttempts,
	comparisonPair,
	defaultAttempt,
	diffList,
	diffVerdict,
	outputTokens,
	provenanceMarker,
	sectionBody,
	sectionRows,
	sourceSentence,
	totalsAgree
} from '../src/lib/packet.ts';
import {
	availability,
	checkpointChoices,
	currentBriefVersion,
	disruptive,
	heldGroups,
	impactLines,
	liveAttempt,
	nextBriefVersion,
	sameAssignment
} from '../src/lib/interventions.ts';
import { FOUR_WAY, outcomeSentences, staleRows, summarize as summarizeRecovery } from '../src/lib/recovery.ts';
import {
	SEGMENT_WEIGHT,
	ago,
	blockedRuns,
	fleetMember,
	largestRun,
	loadedShare,
	MIN_MEMBER_SHARE,
	memberShare,
	needsUser,
	segmentsOf,
	spendReading,
	statusOf,
	tokens
} from '../src/lib/bank.ts';
import {
	COURSES,
	EXAMPLE_DECLARATION,
	columnsOf,
	courseReached,
	memberState,
	rigReading,
	seatsOf
} from '../src/lib/kitchen.ts';
import {
	ABSENCE_SENTENCES,
	BURN_SEGMENT_WEIGHT,
	EXHAUSTED_BUDGET,
	PLAN_COMPLETE,
	anomalyCount,
	budgetReading,
	burnSegments,
	categoryString,
	ceilingsOf,
	coarse,
	estimateOnly,
	etaReading,
	groupAnomalies,
	joinedPhases,
	phaseWord,
	ratioReading
} from '../src/lib/burn.ts';
import {
	closureOf,
	compareFrozen,
	filterShelf,
	groupByKind,
	referencedBy,
	statusState
} from '../src/lib/shelf.ts';
import { bandsOf, formatCap, refusalMessage, rowFor } from '../src/lib/revision.ts';
import type { RecalibrationReport } from '../src/lib/daemon.ts';
import { outline, parseInline, parseMarkdown } from '../src/lib/markdown.ts';
import {
	boundOf,
	nearestStop,
	POLICY_RULE_IDS,
	qualifies,
	qualificationSentence,
	ruleName,
	runPhase,
	stopReading,
	stopsOf
} from '../src/lib/replay.ts';
import { VIEWS } from '../src/lib/views.ts';
import * as burnModule from '../src/lib/burn.ts';
import type {
	Attempt,
	AttentionItem,
	MemoryStatus,
	MemoryReceipt,
	Checkpoint,
	CheckpointVersionView,
	Contract,
	PacketDiff,
	PacketSnapshot,
	PacketSection,
	DownstreamImpact,
	Initiative,
	InitiativeReviewView,
	NodeStatus,
	Plan,
	PlanGraph,
	RiskReport,
	RunRollup,
	Taint,
	HarnessFacts,
	Kitchen,
	KitchenAdapter,
	KitchenCapabilities,
	KitchenReadiness,
	AssetSnapshot,
	AssetSummary,
	Walkthrough
} from '../src/lib/daemon.ts';

const node = (id: string, depends_on: string[], state = 'pending', ready = false): NodeStatus => ({
	initiative_id: id,
	name: `initiative ${id}`,
	digest: 'x',
	state,
	depends_on,
	harness: 'claude-code',
	model: 'claude-opus-5',
	attempts: 0,
	checkpoint_id: null,
	ready
});

const plan = (nodes: NodeStatus[], critical: string[], concurrency: number): PlanGraph => ({
	plan_id: 'check',
	version: 1,
	approval: 'approved',
	nodes,
	edges: nodes.flatMap((n) => n.depends_on.map((d) => [d, n.initiative_id] as [string, string])),
	ready: nodes.filter((n) => n.ready).map((n) => n.initiative_id),
	critical_path: critical,
	max_concurrency: concurrency,
	overhead: { orchestration_tokens: 0, productive_tokens: 0, ratio: null, target: 0.2, within_target: null }
});

let failures = 0;
function ok(claim: string, held: boolean) {
	if (!held) {
		failures++;
		console.error(`FAIL  ${claim}`);
	} else {
		console.log(`ok    ${claim}`);
	}
}

// The seeded Sprint 2 shape: I1,I2,I3 independent; I4 after I1,I2; I5 after I3,I4.
const sprint2 = plan(
	[
		node('I1', [], 'settled'),
		node('I2', [], 'running'),
		node('I3', [], 'pending', true),
		node('I4', ['I1', 'I2']),
		node('I5', ['I3', 'I4'])
	],
	['I1', 'I4', 'I5'],
	3
);
const field = buildField(sprint2);

ok('lane count equals the daemon max_concurrency', field.agrees);
ok('every member is seated exactly once', field.members.length === 5 && field.byId.size === 5);
ok('depth is the longest ancestor chain', field.byId.get('I5')!.depth === 2);
ok('a settled member is seated', field.byId.get('I1')!.state === 'seated');
ok('a running member is loaded', field.byId.get('I2')!.state === 'loaded');
ok('a ready member is balanced, not slack', field.byId.get('I3')!.state === 'balanced');
ok('a blocked member is slack', field.byId.get('I4')!.state === 'slack');
ok('a blocked member names only its unsettled dependencies', field.byId.get('I4')!.blockedBy.join() === 'I2');
const pausedField = buildField(plan([node('P', [], 'paused')], [], 1));
ok('a paused member is balanced but marked paused', pausedField.byId.get('P')!.state === 'balanced' && pausedField.byId.get('P')!.paused);
ok('a paused member is not the same triple as blocked pending', (() => {
	const blocked = buildField(plan([node('B', [], 'pending', false)], [], 1)).byId.get('B')!;
	const held = pausedField.byId.get('P')!;
	return blocked.state !== held.state || blocked.paused !== held.paused || blocked.cancelled !== held.cancelled;
})());
ok('the critical path resolves in order', field.criticalPath.map((m) => m.node.initiative_id).join() === 'I1,I4,I5');
ok('the leading lane carries the critical path', field.lanes[0].filter((m) => m.onCriticalPath).length >= 2);

// A lane is a chain: within one lane no member may run beside another, so
// depths must strictly increase and each member must be reachable from the last.
for (const lane of field.lanes) {
	const rising = lane.every((m, i) => i === 0 || m.depth > lane[i - 1].depth);
	ok(`lane ${lane[0].node.initiative_id}… has strictly rising depth`, rising);
}

// A pure chain can never run two things at once, and a pure antichain always can.
const chain = plan([node('A', []), node('B', ['A']), node('C', ['B'])], ['A', 'B', 'C'], 1);
ok('a pure chain draws one lane', buildField(chain).lanes.length === 1);
const wide = plan([node('A', []), node('B', []), node('C', [])], ['A'], 3);
ok('a pure antichain draws one lane per member', buildField(wide).lanes.length === 3);
ok('an antichain has no crossings', buildField(wide).crossings.length === 0);
ok('a diamond crosses lanes', buildField(sprint2).crossings.length > 0);

const empty = plan([], [], 0);
ok('an empty plan is drawable', buildField(empty).members.length === 0);

ok('a pending plan reads as proposed', phaseOf({ ...sprint2, approval: 'pending' }) === 'proposed');
ok('a plan with work outstanding reads as running', phaseOf(sprint2) === 'running');
ok(
	'a fully settled plan reads as settled',
	phaseOf(plan([node('A', [], 'settled'), node('B', [], 'cancelled')], [], 2)) === 'settled'
);

const order = ['a', 'b', 'c'];
ok('stepping clamps at the ends', step(order, 'c', 1) === 'c' && step(order, 'a', -1) === 'a');
ok('stepping from nothing enters the list', step(order, null, 1) === 'a');
ok('stepping from a vanished id re-enters the list', step(order, 'gone', 1) === 'a');
const target = runTarget(new URL('http://localhost/run?plan=p&initiative=a%2Fb&checkpoint=cp%201').searchParams);
ok('a fleet deep link opens its initiative', target.initiative === 'a/b');
ok('a fleet deep link addresses its checkpoint', target.checkpoint === 'cp 1');

/* --- the gate model (R3) ---------------------------------------------------
   What the approval gate claims, and what it must never claim. The gate is the
   one surface that turns a projection into an irreversible write, so the two
   things checked hardest are that unread never reads as none, and that a
   dependency with no shared path never gets a motive invented for it. */

const risk = (over: Partial<RiskReport> = {}): RiskReport => ({
	plan_id: 'check',
	version: 1,
	critical_path: [],
	max_concurrency: 1,
	nodes: [],
	conflicts: [],
	suggested_edges: [],
	warnings: [],
	...over
});

const gateShape = shapeOf(sprint2, buildField(sprint2).lanes.length);
// R1's readout counts members on the critical path. The gate must show the
// same count in the same unit, or one screen carries two numbers for one fact.
ok('the chain is counted in members, as R1 counts it', gateShape.chain === sprint2.critical_path.length);
ok('an unresolved critical path is zero, and the view renders it as unknown',
	shapeOf(plan([node('A', [], 'pending', true)], [], 1), 1).chain === 0);
ok('ready on approval counts the ready members', gateShape.readyOnApproval === 1);

ok('an unread risk report yields unread callouts, not none',
	calloutsOf(sprint2, null, downstream(sprint2)) === null);
ok('a clean risk report yields no callouts, which is not the same as unread',
	calloutsOf(sprint2, risk(), downstream(sprint2))?.length === 0);

// I1,I2,I3 independent; I4 after I1,I2; I5 after I3,I4. So I4 gates one (I5).
const reach = downstream(sprint2);
ok('downstream counts transitively', reach.get('I1') === 2 && reach.get('I5') === 0);

const conflicted = calloutsOf(
	sprint2,
	risk({
		conflicts: [{ initiatives: ['I2', 'I3'], paths: ['a.py'], kind: 'write_write', writer: null, reader: null }],
		suggested_edges: [{ initiatives: ['I1', 'I3'], paths: ['b.py'], kind: 'write_read', writer: 'I1', reader: 'I3' }]
	}),
	reach
) ?? [];
ok('a write/write pair is a hard callout', conflicted.some((c) => c.kind === 'scope' && !c.advisory));
ok('a suggested edge is advisory', conflicted.some((c) => c.kind === 'overlap' && c.advisory));
ok('a hard callout is the only one drawn as a break', conflicted.filter((c) => c.state === 'failed').length === 1);

// Every node in a tail chain articulates, so an unfiltered list is noise: only
// the worst chokepoint is reported, and nothing below two members is.
const chain5 = plan(
	[node('A', []), node('B', ['A']), node('C', ['B']), node('D', ['C'])],
	['A', 'B', 'C', 'D'],
	1
);
const chokes = calloutsOf(
	chain5,
	risk({
		nodes: ['A', 'B', 'C', 'D'].map((id) => ({
			initiative_id: id, digest: 'x', blast_radius: 0, articulation: true, on_critical_path: true
		}))
	}),
	downstream(chain5)
) ?? [];
ok('only the worst chokepoint is called out', chokes.length === 1 && chokes[0].about[0] === 'A');
ok('a chokepoint is not drawn as a break', chokes[0].state === 'balanced');

const folded = (specs: { id: string; reads?: string[]; writes?: string[]; deps?: string[]; brief?: string }[]): Plan => ({
	id: 'check',
	version: 1,
	brief: 'a brief',
	approval: 'pending',
	created_at: '2026-09-09T00:00:00Z',
	planner: null,
	planner_usage: null,
	initiatives: Object.fromEntries(
		specs.map((s) => [
			s.id,
			{
				spec: {
					id: s.id,
					name: `initiative ${s.id}`,
					brief: s.brief ?? 'do the thing',
					assignment: { harness: 'claude-code', model: 'claude-opus-5' },
					routes: { reads: s.reads ?? [], writes: s.writes ?? [] },
					depends_on: s.deps ?? [],
					approval: 'automatic',
					contract: null
				},
				subtasks: [],
				attempts: [],
				state: 'pending',
				/* R4 widened `Initiative`; the gate's fixture carries no evidence. */
				checkpoint_versions: []
			} as Initiative
		])
	)
});

const shared = folded([
	{ id: 'A', writes: ['x.py'] },
	{ id: 'B', reads: ['x.py'], deps: ['A'] },
	{ id: 'C', reads: ['y.py'], deps: ['A'] },
	{ id: 'D', reads: ['x.py'], deps: ['A', 'C'] }
]);
ok('a root has no reason to state', because(shared.initiatives['A'].spec, shared) === null);
ok('pending approval outranks work', runPhase(folded([{ id: 'A' }])) === 'awaiting_approval');
const phases = folded([{ id: 'A' }, { id: 'B' }]);
phases.approval = 'approved';
phases.initiatives.A.state = 'failed';
phases.initiatives.B.state = 'running';
ok('running work outranks failed outcome', runPhase(phases) === 'running');
phases.initiatives.B.state = 'pending';
ok('failed outranks pending idle work', runPhase(phases) === 'failed');
phases.initiatives.B.state = 'paused';
ok('paused is distinct from idle', runPhase(phases) === 'failed');
const stopped = folded([{ id: 'A' }]);
stopped.created_at = '2026-09-08T00:00:00Z';
stopped.approval = 'approved';
stopped.initiatives.A.state = 'settled';
stopped.initiatives.A.attempts = [{ id: 'a-A-1', started_at: '2026-09-09T00:00:01.123456+02:00', ended_at: '2026-09-09T00:00:03.123456+02:00' } as Attempt];
stopped.initiatives.A.brief_versions = [{ version: 2, brief: 'redirect', by: 'operator', at: '2026-09-09T00:00:03.123456+02:00', reason: '' }];
stopped.initiatives.A.checkpoint_decisions = {
	cp: { state: 'approved', decided_at: '2026-09-09T00:00:03.123456+02:00', decided_by: 'operator', reason: 'ok', approved_at: null }
};
stopped.policy_decisions = [{ initiative_id: 'A', attempt_id: 'a-A-1', checkpoint_id: 'cp', outcome: 'approved', rule_ids: [...POLICY_RULE_IDS.slice(0, 1)], reason: '', at: '2026-09-09T00:00:04.123456+02:00', seq: 1 }];
const replayStops = stopsOf(stopped);
ok('created_at is the first replay stop', replayStops[0].at === stopped.created_at);
ok('same instant records collapse and retain both labels', replayStops.some((stop) => stop.labels.length > 1));
ok('bounds preserve recorded timestamp bytes', boundOf(replayStops, 1) === '2026-09-09T00:00:01.123456+02:00');
ok('nearest stop never snaps forward', nearestStop(replayStops, '2026-09-08T00:00:00Z') === 0);
ok('last-stop divergence is only emitted at the last stop', stopReading(replayStops, replayStops.length - 1, 'running', 'settled') === "The record's last dated moment does not yet show the run as it finished. Return to live to read the run as it stands.");
ok('settled and failed qualify only', qualifies('settled') && qualifies('failed') && !qualifies('running'));
ok('qualification copy is distinct', new Set(['awaiting_approval', 'empty', 'running', 'paused', 'idle'].map((phase) => qualificationSentence(phase as Parameters<typeof qualificationSentence>[0]))).size === 5);
ok('policy rule names cover daemon ids', POLICY_RULE_IDS.every((id) => ruleName(id) !== null) && ruleName('new.rule') === null);

ok(
	'a dependency whose write is read is explained by the shared path',
	(because(shared.initiatives['B'].spec, shared) ?? '').includes('reads what it writes')
);
ok(
	'a dependency with no shared path is declared, never given a motive',
	(because(shared.initiatives['C'].spec, shared) ?? '').includes('no shared path')
);
ok(
	'a mixed dependency names which half is explained',
	(because(shared.initiatives['D'].spec, shared) ?? '').includes('C') &&
		(because(shared.initiatives['D'].spec, shared) ?? '').includes('no shared path')
);
ok(
	'an unread plan still states the edge, without claiming a reason',
	because(shared.initiatives['B'].spec, null) === 'waits on A'
);

ok('a one-paragraph brief is not truncated', lead('one line').truncated === false);
ok('a multi-paragraph brief keeps the first and says so',
	lead('first\n\nsecond').lead === 'first' && lead('first\n\nsecond').truncated);

const rows = registerOf(buildField(sprint2).members, null);
ok('the register survives an unread plan', rows.length === 5 && rows.every((r) => r.spec === null));
ok('the register keeps the field\'s order', rows[0].member.node.initiative_id === buildField(sprint2).members[0].node.initiative_id);

/* --- R4: the checkpoint review model -------------------------------------
   The claims this surface rests on: the two reads join without either one
   inventing the other's half, a change list is symmetrical where the daemon's
   own field is not, the verdicts on offer are the fold's own rules, and the
   downstream sentence never promises a readiness the graph does not support. */

const manifest = (id: string, paths: string[], extra: Partial<Checkpoint> = {}): Checkpoint => ({
	id,
	attempt_id: `a-${id}`,
	exit_code: 0,
	usage: { input_tokens: 10, output_tokens: 2, source: 'harness' },
	changed_paths: paths,
	base_sha: null,
	head_sha: null,
	checks: [],
	patch_path: null,
	caveats: [],
	...extra
});

const versionView = (
	number: number,
	id: string,
	extra: Partial<CheckpointVersionView> = {}
): CheckpointVersionView => ({
	version: number,
	checkpoint_id: id,
	attempt_id: `a-${id}`,
	decision: 'pending',
	decided_at: null,
	decided_by: '',
	reason: '',
	approved_at: null,
	superseded: false,
	exit_code: 0,
	failed_checks: [],
	failed_check_summaries: {},
	changed_paths: [],
	patch_path: null,
	...extra
});

const withVersions = (all: Checkpoint[]): Initiative =>
	({ checkpoint_versions: all, spec: { approval: 'required' }, subtasks: [], attempts: [], state: 'running' }) as unknown as Initiative;

const reviewView = (versions: CheckpointVersionView[], extra: Partial<InitiativeReviewView> = {}): InitiativeReviewView => ({
	initiative_id: 'C1',
	name: 'C1',
	policy: 'required',
	state: 'running',
	awaiting_review: false,
	approved_version: null,
	approved_checkpoint_id: null,
	changes_since_approved: [],
	violations: [],
	versions,
	...extra
});

// The join. Each read can fail on its own, and neither may fill the other in.
const joined = versionsOf(
	withVersions([manifest('c1', ['a.py']), manifest('c2', ['a.py', 'b.py'])]),
	reviewView([
		versionView(1, 'c1', { decision: 'rejected', superseded: true }),
		versionView(2, 'c2')
	])
);
ok('a version carries its decision and its manifest together',
	joined.length === 2 && joined[1].decision === 'pending' && joined[1].manifest?.changed_paths.length === 2);
ok('a manifest with no review row is marked unread, never pending',
	versionsOf(withVersions([manifest('c1', ['a.py'])]), null)[0].unread === true);
ok('a review row with no manifest reports the manifest missing, not empty',
	versionsOf(null, reviewView([versionView(1, 'c1')]))[0].manifest === null);
ok('no evidence and no report is an empty history, not a phantom version',
	versionsOf(null, null).length === 0);

// The change list. The daemon projects the added half; this projects all three.
const base = joined[0];
const head = joined[1];
const diff = changesOf(head, base);
ok('a change list names what was added', diff?.added.join() === 'b.py');
ok('a change list names what is carried through', diff?.carried.join() === 'a.py');
const dropped = changesOf(
	versionsOf(withVersions([manifest('c9', ['a.py'])]), reviewView([versionView(1, 'c9')]))[0],
	joined[1]
);
ok('a change list names what the daemon\'s own field cannot: a dropped path',
	dropped?.dropped.join() === 'b.py');
ok('two versions touching the same paths are identical, which is not unchanged',
	changesOf(
		versionsOf(withVersions([manifest('cx', ['a.py'])]), reviewView([versionView(1, 'cx')]))[0],
		versionsOf(withVersions([manifest('cy', ['a.py'])]), reviewView([versionView(1, 'cy')]))[0]
	)?.identical === true);
ok('a version compared against itself yields no comparison at all',
	changesOf(head, head) === null);

// Checks. A required check that never ran is neither a pass nor a failure.
const contract = {
	id: 'c', role: 'implementer',
	required_checks: ['pytest', 'basedpyright'], required_paths: ['x.py'],
	require_patch: true, allow_writes: true, allowed_commands: null
} as Contract;
const checkRows = checksOf(
	manifest('c1', ['x.py', 'y.py'], {
		checks: [
			{ name: 'pytest', passed: true, summary: 'ok' },
			{ name: 'ruff', passed: false, summary: 'E501' }
		]
	}),
	contract
);
ok('required checks come first, in the contract\'s own order',
	checkRows[0].name === 'pytest' && checkRows[1].name === 'basedpyright');
ok('a required check that never ran has no result rather than a failed one',
	checkRows[1].result === null && checkRows[1].required);
ok('a check the contract never named still appears, marked unrequired',
	checkRows[2].name === 'ruff' && checkRows[2].required === false);

const artifactRows = artifactsOf(manifest('c1', ['y.py'], {}), contract);
ok('a required artifact the version never touched is present in the list, absent in fact',
	artifactRows[0].path === 'x.py' && artifactRows[0].required && !artifactRows[0].present);

// The verdicts on offer are the fold's rules, not a UI convention.
ok('a pending version offers all three verdicts', allowed('pending').length === 3);
ok('an approved version can only be withdrawn by rejecting it',
	allowed('approved').join() === 'reject');
ok('a rejected version is final and offers nothing', allowed('rejected').length === 0);
ok('changes cannot be requested twice', !allowed('changes_requested').includes('changes'));

// Downstream. Readiness is a conjunction, and the sentence must not forget it.
const gated = plan(
	[
		node('C1', [], 'running'),
		node('C2', [], 'pending'),
		node('C3', ['C1'], 'pending'),
		node('C4', ['C1', 'C2'], 'pending'),
		node('C5', ['C3'], 'pending')
	],
	['C1', 'C3', 'C5'],
	2
);
const taint: Taint = { initiative_id: 'C3', producer_id: 'C1', checkpoint_id: 'c1', reason: 'was rejected' };
const chainConsumers = consumersOf(gated, 'C1', [taint]);
ok('consumers reach past the direct dependents', chainConsumers.map((c) => c.id).join() === 'C3,C4,C5');
ok('a dependent reached through another is not called direct',
	chainConsumers.find((c) => c.id === 'C5')?.direct === false);
ok('a consumer with another unsettled dependency says so',
	chainConsumers.find((c) => c.id === 'C4')?.alsoWaitingOn.join() === 'C2');
ok('a taint is attached to the consumer it is about',
	chainConsumers.find((c) => c.id === 'C3')?.tainted.length === 1);
ok('an initiative nothing depends on has no consumers',
	consumersOf(gated, 'C5', []).length === 0);

const approving = impactOf('approve', {
	initiativeId: 'C1', version: 2, state: 'running', current: true,
	consumers: chainConsumers, approvedVersion: 1
}).join(' ');
ok('approving names the members that actually become ready', approving.includes('C3'));
ok('approving refuses to promise readiness for a member still waiting on another',
	approving.includes('C4 depends on C1 but also waits on C2'));
ok('approving a superseding version says the old approval stops standing',
	approving.includes('Version 1 stops being the standing evidence'));
ok('approving a version that is not current does not claim it settles anything',
	impactOf('approve', {
		initiativeId: 'C1', version: 1, state: 'running', current: false,
		consumers: chainConsumers, approvedVersion: 1
	}).join(' ').includes('does not settle'));

const rejecting = impactOf('reject', {
	initiativeId: 'C1', version: 2, state: 'running', current: true,
	consumers: chainConsumers, approvedVersion: 1
}).join(' ');
ok('rejecting says the run ends', rejecting.includes('marks C1 failed'));
ok('rejecting names the members it holds', rejecting.includes('cannot start'));
ok('rejecting says the already-tainted consumer by name', rejecting.includes('C3 already rest'));
ok('rejecting promises nothing is deleted', rejecting.includes('Nothing is deleted'));

// The section headline.
ok('an automatic member with undecided evidence is not a review queue of one',
	summarize(
		versionsOf(withVersions([manifest('c1', [])]), reviewView([versionView(1, 'c1')], { policy: 'automatic' })),
		reviewView([versionView(1, 'c1')], { policy: 'automatic' }),
		'automatic'
	).awaiting === false);
ok('a required member with a pending version is awaiting review',
	summarize(joined, reviewView([versionView(2, 'c2')], { awaiting_review: true }), 'required').awaiting);
ok('no version at all reads as none recorded, in slack',
	summarize([], null, 'required').state === 'slack');
ok('an unread review lifecycle never reports a decision',
	summarize(versionsOf(withVersions([manifest('c1', [])]), null), null, 'required').word.includes('unread'));

/* --- R9: recovery ----------------------------------------------------------
   The read-only report is a list of daemon-owned attempts that have gone
   stale. Reconcile is intentionally a write with four distinct outcomes. */
const recovery = {
	stale: [
		{ initiative_id: 'V1', attempt_id: 'a1', pane_ref: 'herdsman:1', worktree_ref: 'wt/v1', outcome: 'unknown' },
		{ initiative_id: 'V2', attempt_id: 'a2', pane_ref: null, worktree_ref: null, outcome: 'unknown' }
	],
	outcomes: { V1: 'reattached', V2: 'failed', V3: 'settled', V4: 'skipped' },
	orphaned_panes: ['herdsman:9'],
	orphaned_worktrees: ['wt/orphan']
};
const recoveryRows = staleRows(recovery);
ok('recovery rows preserve pane and worktree absence as explicit nulls', recoveryRows.length === 2 && recoveryRows[1].pane_ref === null && recoveryRows[1].worktree_ref === null);
ok('recovery outcome sentences cover every probed outcome', outcomeSentences(recovery.outcomes).length === 4 && outcomeSentences(recovery.outcomes).every(([, sentence]) => sentence.length > 0));
ok('skipped recovery outcomes remain explicit', outcomeSentences(recovery.outcomes).some(([word]) => word === 'already closed'));
ok('orphaned panes and worktrees remain visible', recovery.orphaned_panes.length === 1 && recovery.orphaned_worktrees.length === 1);
ok('reconcile summary counts outcomes without calling it retry', summarizeRecovery(recovery.outcomes).includes('1 reattached') && !summarizeRecovery(recovery.outcomes).toLowerCase().includes('retry'));
ok('recovery teaches the four-way distinction', FOUR_WAY.length === 4 && ['RETRY', 'RESTART', 'REPLANNING'].every((label) => FOUR_WAY.some(([name]) => name === label)));

/* --- R6: the interventions ------------------------------------------------
   Every claim here is a rule the daemon or fold already enforces. A drift
   between this file and `herdsman/daemon.py` is the surface offering a control
   the fold will refuse, or naming a refusal the daemon never gives -- both of
   which teach an operator a rule that does not exist. */

const attempt = (id: string, extra: Partial<Attempt> = {}): Attempt =>
	({
		id,
		initiative_id: 'V1',
		assignment: { harness: 'claude-code', model: 'claude-opus-5' },
		brief_version: 1,
		by: 'daemon',
		origin: 'run',
		worktree_ref: null,
		pane_ref: 'herdsman:1',
		started_at: '2026-09-11T00:00:00Z',
		ended_at: null,
		checkpoint: null,
		packet_tokens: 0,
		...extra
	}) as Attempt;

const member = (extra: Partial<Initiative> = {}, spec: Record<string, unknown> = {}): Initiative =>
	({
		spec: {
			id: 'V1',
			name: 'V1',
			brief: 'b',
			assignment: { harness: 'claude-code', model: 'claude-opus-5' },
			routes: { reads: [], writes: [] },
			depends_on: [],
			approval: 'automatic',
			contract: null,
			policy: { max_attempts: 3 },
			...spec
		},
		subtasks: [],
		attempts: [],
		state: 'pending',
		checkpoint_versions: [],
		failures: [],
		brief_versions: [],
		assignment_override: null,
		...extra
	}) as unknown as Initiative;

const refusalOf = (initiative: Initiative, approved: boolean, action: string) =>
	availability(initiative, approved).find((offer) => offer.action === action)?.refused ?? '';
const offeredBy = (initiative: Initiative, approved = true) =>
	availability(initiative, approved)
		.filter((offer) => offer.available)
		.map((offer) => offer.action);

// The three pane actions share `Daemon._live_attempt`'s three checks, in order.
const running = member({ state: 'running', attempts: [attempt('a1')] });
ok('a running member with a live pane offers the three pane actions',
	['restart', 'nudge', 'answer'].every((action) => offeredBy(running).includes(action)));
ok('a pending member is refused a nudge because it has no live pane',
	refusalOf(member(), true, 'nudge').includes('Only a running task has a live pane'));
ok('a running member with no attempt at all says so, not "no pane"',
	refusalOf(member({ state: 'running' }), true, 'restart').includes('No attempt has started here'));
ok('a running attempt that recorded no pane is refused by attempt id',
	refusalOf(member({ state: 'running', attempts: [attempt('a9', { pane_ref: null })] }), true, 'answer')
		.includes('Attempt a9 recorded no pane'));
ok('the three pane actions share one sentence, so it is printed once and not thrice',
	heldGroups(availability(member({ state: 'failed', attempts: [attempt('a1')] }), true))
		.some((group) => group.actions.join(',') === 'restart,nudge,answer,answer-memory'));
ok('refusals with different causes are never merged',
	heldGroups(availability(member({ state: 'settled' }), true)).length > 1);
ok('held groups keep the shared pane refusal together',
	heldGroups(availability(member({ state: 'failed', attempts: [attempt('a1')] }), true))
		.some((group) => group.actions.length === 3 && group.actions.join(',') === 'restart,nudge,answer'));
ok('cancel explains that idle descendants remain pending',
	impactLines('cancel', {
		initiative: member({ state: 'running', attempts: [attempt('a1')] }),
		impact: { initiative_id: 'V1', descendants: [{ initiative_id: 'V2', state: 'pending', attempts: 0 }], started: [] }
	}).some((line) => line.includes('pending') && line.includes('not released') && line.includes('never settles')));
ok('unpause distinguishes failed work from never-started work',
	impactLines('unpause', { initiative: member({ state: 'paused', attempts: [attempt('a1')] }), impact: null })[0].includes('failed') &&
		impactLines('unpause', { initiative: member({ state: 'paused', attempts: [] }), impact: null })[0].includes('pending'));
ok('hold says a running attempt is not interrupted',
	impactLines('pause', { initiative: member({ state: 'running', attempts: [attempt('a1')] }), impact: null })[0].includes('not interrupted'));
ok('the live attempt is the latest one, never an earlier one that had a pane',
	liveAttempt(
		member({ state: 'running', attempts: [attempt('a1'), attempt('a2', { pane_ref: null })] })
	) === null);

// Retry's three refusals are three different rules and must read as three.
ok('retry is refused on work that has not failed',
	refusalOf(running, true, 'retry').includes('this member is running'));
ok('retry is refused at the fold-enforced ceiling, naming it',
	refusalOf(
		member({ state: 'failed', attempts: [attempt('a1'), attempt('a2'), attempt('a3')] }),
		true, 'retry'
	).includes('all 3 of its attempts'));
ok('retry on an unapproved revision is refused for approval, not for state',
	refusalOf(member({ state: 'failed', attempts: [attempt('a1')] }), false, 'retry')
		.includes('not approved'));
ok('a failed member under the ceiling on an approved plan can be retried',
	offeredBy(member({ state: 'failed', attempts: [attempt('a1')] })).includes('retry'));

// Reassign and redirect are refused by state alone, and only by two states.
// What a reassignment may be *set to* is the Kitchen's catalog, read when the
// action is armed — not a second refusal here.
ok('a settled member can be neither reassigned nor redirected',
	refusalOf(member({ state: 'settled' }), true, 'reassign').includes('this member is settled') &&
		refusalOf(member({ state: 'settled' }), true, 'redirect').includes('this member is settled'));
ok('a failed member can still be redirected and reassigned',
	['redirect', 'reassign'].every((action) =>
		offeredBy(member({ state: 'failed', attempts: [attempt('a1')] })).includes(action)));

// Versions. 1 is the planner's and is never stored, so counting the list is wrong.
ok('a member that was never redirected runs on brief version 1',
	currentBriefVersion(member()) === 1 && nextBriefVersion(member()) === 2);
ok('one redirect makes the current version 2 and the next 3',
	currentBriefVersion(member({ brief_versions: [{ version: 2 } as never] })) === 2 &&
		nextBriefVersion(member({ brief_versions: [{ version: 2 } as never] })) === 3);

// The override is what a new attempt runs on, so it is what a duplicate is read against.
ok('a reassignment onto the override in force is seen as the duplicate the fold refuses',
	sameAssignment(
		member({ assignment_override: { harness: 'pi', model: 'pi-default' } }),
		'pi', 'pi-default'
	));
ok("an override does not hide behind the planner's pair",
	!sameAssignment(
		member({ assignment_override: { harness: 'pi', model: 'pi-default' } }),
		'claude-code', 'claude-opus-5'
	));

// What confirming does. The downstream half is the daemon's, and only for the
// three that can strand work.
const impact = (started: string[], idle: string[]): DownstreamImpact => ({
	initiative_id: 'V1',
	descendants: [
		...started.map((id) => ({ initiative_id: id, state: 'running', attempts: 1 })),
		...idle.map((id) => ({ initiative_id: id, state: 'pending', attempts: 0 }))
	],
	started
});
const failedOnce = member({ state: 'failed', attempts: [attempt('a1')] });

ok('a retry preview counts the attempt it would be, out of the ceiling',
	impactLines('retry', { initiative: failedOnce, impact: impact([], ['V5']) })[0]
		.includes('attempt 2 of 3'));
ok('the last admissible retry says it is the last',
	impactLines('retry', {
		initiative: member({ state: 'failed', attempts: [attempt('a1'), attempt('a2')] }),
		impact: impact([], [])
	}).join(' ').includes('last attempt the fold will admit'));
ok('a preview names started descendants as work that may have to be redone',
	impactLines('retry', { initiative: failedOnce, impact: impact(['V6'], ['V5']) })
		.join(' ').includes('V6 already ran'));
ok('a preview keeps the idle descendants separate from the started ones',
	impactLines('retry', { initiative: failedOnce, impact: impact(['V6'], ['V5']) })
		.join(' ').includes('V5 is downstream and has not started'));
ok('nothing downstream is said as nothing, not as silence',
	impactLines('retry', { initiative: failedOnce, impact: impact([], []) })
		.join(' ').includes('Nothing depends on this member'));
ok('an unread impact is unread, never reported as no impact',
	impactLines('retry', { initiative: failedOnce, impact: null })
		.join(' ').includes('unread'));
ok('a restart claims no downstream consequence, because it has none',
	!disruptive('restart') &&
		!impactLines('restart', { initiative: running, impact: impact(['V6'], []) })
			.join(' ').includes('V6'));
ok('a restart says the attempt count does not move',
	impactLines('restart', { initiative: running, impact: null })
		.join(' ').includes('stays at 1 of 3'));
ok('a redirect names the version it would create, not the one in force',
	impactLines('redirect', { initiative: failedOnce, impact: impact([], []) })[0]
		.includes('brief version 2'));
ok('a redirect on a running member promises the live attempt is undisturbed',
	impactLines('redirect', { initiative: running, impact: impact([], []) })
		.join(' ').includes('nothing live is disturbed'));
ok('a reassignment promises it starts nothing',
	impactLines('reassign', {
		initiative: failedOnce, impact: impact([], []),
		assignment: { harness: 'pi', model: 'pi-default' }
	}).join(' ').includes('Nothing starts because of this'));

// Redirect targets are plan-wide, because the fold resolves them plan-wide.
const targets = checkpointChoices(
	{
		V4: member({ checkpoint_versions: [manifest('c-V4', [])] }),
		V1: member({ checkpoint_versions: [manifest('c-V1a', []), manifest('c-V1b', [])] })
	},
	'V1'
);
ok('every recorded version in the plan is a legal redirect target',
	targets.length === 3);
ok("the member's own versions are offered before another member's",
	targets.slice(0, 2).every((choice) => choice.own) && !targets[2].own);
ok('a version is numbered within its own producer, not across the plan',
	targets[0].version === 1 && targets[1].version === 2 && targets[2].version === 1);


// --- H1: the load bank -------------------------------------------------------
//
// The claims the drawing rests on. If any of these breaks, the bank starts
// lying about how much of the fleet is carrying load, or turns an unknown into
// a zero — which on a spend readout is the difference between "nothing has been
// measured" and "this run is free".

const rollup = (over: Partial<RunRollup> = {}): RunRollup => ({
	plan_id: 'plan_1',
	brief: 'ship it',
	version: 1,
	approval: 'approved',
	status: 'running',
	archived: false,
	created_at: '2026-09-18T09:00:00Z',
	updated_at: '2026-09-18T09:00:00Z',
	counts: { pending: 0, running: 0, settled: 0, failed: 0, paused: 0, cancelled: 0 },
	total: 0,
	progress: 0,
	link: { path: '/run?plan=plan_1' },
	...over
});

const item = (over: Partial<AttentionItem> = {}): AttentionItem => ({
	key: 'plan_gate:plan_1:1',
	kind: 'plan_gate',
	plan_id: 'plan_1',
	initiative_id: null,
	attempt_id: null,
	checkpoint_id: null,
	summary: 'waiting',
	since: '2026-09-18T09:00:00Z',
	blocking: true,
	action: { method: 'POST', path: '/plans/plan_1/approve', label: 'Approve plan' },
	link: { path: '/run?plan=plan_1' },
	...over
});

const mixed = { settled: 4, running: 2, failed: 1, paused: 1, pending: 3, cancelled: 1 };

const segs = segmentsOf(mixed, 12);
ok('a member covers its run exactly once',
	Math.abs(segs.reduce((sum, s) => sum + s.share, 0) - 1) < 1e-9);
ok('every initiative is drawn in exactly one segment',
	segs.reduce((sum, s) => sum + s.count, 0) === 12);
ok('load is drawn at the left and slack at the right',
	segs.map((s) => s.state).join(',') ===
		'settled,running,failed,paused,pending,cancelled');
ok('a state with no initiatives is not drawn at zero width',
	segmentsOf({ settled: 3 }, 3).length === 1);
ok('a run with no initiatives draws no member at all',
	segmentsOf({}, 0).length === 0);

ok('failed work has taken up load; it is where the load stopped',
	SEGMENT_WEIGHT.failed === 'load' && SEGMENT_WEIGHT.running === 'load');
ok('work that has not started carries nothing',
	SEGMENT_WEIGHT.pending === 'none' && SEGMENT_WEIGHT.cancelled === 'none');
ok('the loaded share is settled, running and failed — not progress',
	Math.abs(loadedShare(mixed, 12) - 7 / 12) < 1e-9);
ok('an empty run is not reported as fully loaded',
	loadedShare({}, 0) === 0);

ok('an unapproved run is slack, never loaded',
	statusOf('awaiting_approval').state === 'slack');
ok('a settled run is seated in the structure',
	statusOf('settled').state === 'seated');
ok('a status this build has never seen still reads, and claims no load',
	statusOf('quiesced').word === 'quiesced' && statusOf('quiesced').state === 'balanced');

ok('only blocking attention is counted as needing the user',
	needsUser(rollup({ attention: [item(), item({ key: 'stalled:1', blocking: false })] })).count === 1);
ok('the oldest blocker carries its own deep link, not just the plan',
	needsUser(rollup({
		attention: [item({ kind: 'checkpoint_review', link: { path: '/run?plan=plan_1&initiative=V1&checkpoint=c1' } })]
	})).oldest?.link.path.includes('checkpoint=c1') === true);
ok('a daemon that projects no attention is unknown, never nothing waiting',
	needsUser(rollup()).unknown && needsUser(rollup({ attention: [] })).unknown === false);

ok('an unmeasured run reads as unknown, never as a spend of zero',
	spendReading({ accounted: 0, sources: [], cap: null, remaining: null }).value === null);
ok('a daemon with no spend projection says so rather than reading zero',
	spendReading(undefined).value === null &&
		spendReading(undefined).gloss.includes('does not project'));
ok('a measured figure names how it was measured',
	spendReading({ accounted: 41234, sources: ['actual', 'estimate'], cap: null, remaining: null })
		.gloss.startsWith('measured · estimated'));
ok('no declared cap is stated as no budget, never as none left',
	spendReading({ accounted: 900, sources: ['actual'], cap: null, remaining: null })
		.available === null);
ok('a declared cap reports what is available and that admission enforces it',
	// Corrected with Sprint 6-A's admission enforcement: the fold refuses a
	// start that would carry the run past the cap. See the R8 entry.
	spendReading({ accounted: 900, sources: ['actual'], cap: 5000, remaining: 4100 })
		.gloss.includes('enforced when an attempt starts'));
ok('a token figure stays exact until it needs abbreviating',
	tokens(9999) === '9,999' && tokens(41234) === '41.2k');

ok('equal counts draw equal member lengths, whatever their run totals',
	memberShare(7, 27) !== memberShare(27, 27) && memberShare(9, 27) === memberShare(9, 27));
ok('a member is drawn against the largest listed run, not against its own total',
	Math.abs(memberShare(27, 27) - 1) < 1e-9 && Math.abs(memberShare(14, 28) - 0.5) < 1e-9);
ok('the smallest run is still a member somebody can see and hit',
	memberShare(1, 400) === MIN_MEMBER_SHARE && MIN_MEMBER_SHARE > 0.05);
ok('a run with no initiatives does not divide by a fleet of none',
	memberShare(0, 0) === MIN_MEMBER_SHARE);
ok('the largest run is the unit, and an empty fleet has none',
	largestRun({
		runs: [rollup({ total: 6 }), rollup({ plan_id: 'p2', total: 27 })],
		archived: 0, counts: {}, running_runs: 0, total_runs: 2, unreadable: []
	}) === 27 &&
		largestRun({
			runs: [], archived: 0, counts: {}, running_runs: 0, total_runs: 0, unreadable: []
		}) === 0);
ok('the fleet member is not drawn beside a fleet of one',
	fleetMember({
		runs: [rollup()], archived: 0, counts: mixed, running_runs: 1, total_runs: 1, unreadable: []
	}) === null);
ok('blocked runs are counted by run and by item, which are different questions',
	(() => {
		const blocked = blockedRuns({
			runs: [
				rollup({ attention: [item(), item({ key: 'b' })] }),
				rollup({ plan_id: 'plan_2', attention: [item({ key: 'c', plan_id: 'plan_2' })] }),
				rollup({ plan_id: 'plan_3', attention: [] })
			],
			archived: 0, counts: {}, running_runs: 0, total_runs: 3, unreadable: []
		});
		return blocked.runs === 2 && blocked.items === 3;
	})());
ok('the fleet figure is the sum of the figures printed beside each run',
	(() => {
		const runs = [
			rollup({ attention: [item(), item({ key: 'b', blocking: false })] }),
			rollup({ plan_id: 'plan_2', attention: [item({ key: 'c', plan_id: 'plan_2' })] })
		];
		const blocked = blockedRuns({
			runs, archived: 0, counts: {}, running_runs: 0, total_runs: 2, unreadable: []
		});
		return blocked.items === runs.reduce((sum, run) => sum + needsUser(run).count, 0);
	})());
ok('a daemon that projects no attention is unknown, never all clear',
	blockedRuns({
		runs: [rollup()], archived: 0, counts: {}, running_runs: 0, total_runs: 1, unreadable: []
	}).unknown);
ok('a run that reported nothing is named, never summed in as a zero',
	(() => {
		const blocked = blockedRuns({
			runs: [rollup({ attention: [item()] }), rollup({ plan_id: 'plan_2' })],
			archived: 0, counts: {}, running_runs: 0, total_runs: 2, unreadable: []
		});
		return blocked.items === 1 && blocked.silent === 1 && !blocked.unknown;
	})());
ok('a remainder is printed against the ceiling it is a remainder of',
	spendReading({ accounted: 900, sources: ['actual'], cap: 5000, remaining: 4100 })
		.available === '4,100 of 5,000');

const CLOCK = Date.parse('2026-09-18T12:00:00Z');
ok('a change inside the poll interval is never reported to the second',
	ago('2026-09-18T11:59:58Z', CLOCK) === 'just now');
ok('elapsed time coarsens as it grows',
	ago('2026-09-18T11:30:00Z', CLOCK) === '30 min ago' &&
		ago('2026-09-18T09:00:00Z', CLOCK) === '3 h ago' &&
		ago('2026-09-14T12:00:00Z', CLOCK) === '4 d ago');
ok('an unparseable time is unknown, not the epoch',
	ago('not a time', CLOCK) === '—');

/* --- K1's rig model ------------------------------------------------------
   The one place Kitchen's surface and the daemon can silently disagree is the
   line between what a probe observed and what the project declared. Every
   claim below mirrors a rule in `herdsman/discovery.py` or `herdsman/kitchen.py`:
   a drift here is the elevation drawing a harness taller than the evidence. */
const caps = (over: Partial<KitchenCapabilities> = {}): KitchenCapabilities => ({
	structured_output: 'unknown', resume: 'unknown', usage: 'unknown',
	pty: 'unknown', memory: null, ...over
});
const adapter = (name: string, over: Partial<KitchenCapabilities> = {}): KitchenAdapter =>
	({ name, source: 'declared', capabilities: caps(over) });
const fact = (over: Partial<HarnessFacts> = {}): HarnessFacts => ({
	harness: 'claude', executable: '/usr/bin/claude', version: '2.1.0',
	health: 'healthy', detail: '', ...over
});
const verdict = (over: Partial<KitchenReadiness> = {}): KitchenReadiness => ({
	harness: 'claude', state: 'ready', reason: '', action: '', version: '2.1.0', ...over
});
const kitchen = (over: Partial<Kitchen> = {}): Kitchen => ({
	version: 1, configured: true, ready: false, revision: 'r1',
	adapters: [adapter('claude')], models: [], readiness: [verdict()],
	discovery: { facts: [fact()], models: [] }, blockers: [], notes: [], ...over
});

ok('a declared capability never raises a column: height is observed only',
	courseReached(undefined) === 1 &&
		columnsOf(kitchen({
			adapters: [adapter('claude', { structured_output: 'supported', resume: 'supported', memory: 'C' })],
			readiness: [verdict({ state: 'unknown', reason: 'no discovery facts for this adapter' })],
			discovery: { facts: [], models: [] }
		}))[0].reached === 1);

ok('never probed and probed-and-absent are different states at the same height',
	(() => {
		const unprobed = columnsOf(kitchen({
			readiness: [verdict({ state: 'unknown' })], discovery: { facts: [], models: [] }
		}))[0];
		const absent = columnsOf(kitchen({
			readiness: [verdict({ state: 'unavailable' })],
			discovery: { facts: [fact({ executable: null, version: null, health: 'unknown', detail: "executable 'claude' not found on PATH" })], models: [] }
		}))[0];
		return unprobed.observed === null && unprobed.reached === 1 &&
			absent.observed !== null && absent.reached === 1;
	})());

ok('each course is cleared by the evidence that course names, and no other',
	courseReached(fact({ executable: null, health: 'unknown', version: null })) === 1 &&
		courseReached(fact({ health: 'unhealthy', version: null })) === 2 &&
		courseReached(fact({ version: null })) === 3 &&
		courseReached(fact()) === COURSES.length);

ok('an undeclared capability is undeclared, never a no',
	(() => {
		const seats = seatsOf(adapter('claude', { pty: 'unsupported' }));
		const memory = seats.find((seat) => seat.id === 'memory');
		const pty = seats.find((seat) => seat.id === 'pty');
		return memory?.state === 'unknown' && pty?.state === 'unsupported' &&
			seats.filter((seat) => seat.state === 'unknown').length === 4;
	})());

ok('a declared memory class is carried as itself, never guessed when absent',
	seatsOf(adapter('claude', { memory: 'B' })).find((s) => s.id === 'memory')?.gloss.includes('class B') === true &&
		seatsOf(adapter('claude')).find((s) => s.id === 'memory')?.gloss === 'no class declared');

ok('red is the broken path only: nothing in Kitchen is under load',
	memberState('ready') === 'seated' && memberState('unavailable') === 'failed' &&
		memberState('degraded') === 'slack' && memberState('unknown') === 'slack');

ok('a harness the daemon reported no readiness for is unknown, never ready',
	columnsOf(kitchen({ readiness: [] }))[0].state === 'unknown');

ok('a readiness row for an undeclared harness is carried with no invented seats',
	(() => {
		const columns = columnsOf(kitchen({
			readiness: [verdict(), verdict({ harness: 'codex', state: 'unconfigured', reason: 'installed but not declared in this project' })]
		}));
		const extra = columns.find((c) => c.harness === 'codex');
		return columns.length === 2 && extra?.seats.length === 0 && extra?.state === 'unconfigured';
	})());

ok('an unmeasured harness counts as neither ready nor unavailable',
	(() => {
		const reading = rigReading(columnsOf(kitchen({
			adapters: [adapter('claude'), adapter('codex')],
			readiness: [verdict(), verdict({ harness: 'codex', state: 'unknown' })],
			discovery: { facts: [fact()], models: [] }
		})));
		return reading.declared === 2 && reading.ready === 1 &&
			reading.unavailable === 0 && reading.unprobed === 1 && reading.other === 0;
	})());

ok('the readout\'s parts always sum to what the project declared',
	(() => {
		const reading = rigReading(columnsOf(kitchen({
			adapters: [adapter('claude'), adapter('codex'), adapter('gemini'), adapter('balky')],
			readiness: [
				verdict(),
				verdict({ harness: 'codex', state: 'degraded' }),
				verdict({ harness: 'gemini', state: 'unavailable' }),
				verdict({ harness: 'balky', state: 'unknown' })
			],
			discovery: { facts: [
				fact(),
				fact({ harness: 'codex', version: null, health: 'unknown' }),
				fact({ harness: 'gemini', executable: null, version: null, health: 'unknown' })
			], models: [] }
		})));
		return reading.other === 1 &&
			reading.ready + reading.unavailable + reading.unprobed + reading.other ===
				reading.declared;
	})());

ok('the example declaration is a document the daemon would accept',
	(() => {
		const doc = JSON.parse(EXAMPLE_DECLARATION);
		const names = new Set(doc.adapters.map((a: { name: string }) => a.name));
		const catalog = new Set(doc.models.map((m: { harness: string; model: string }) => `${m.harness}/${m.model}`));
		const assignments = [doc.defaults.planner, doc.defaults.initiative];
		return doc.version === 1 && doc.adapters.length === 1 && doc.models.length === 2 &&
			doc.adapters.every((a: { argv: string[] }) => a.argv.filter((el) => el === '{prompt}').length === 1) &&
			assignments.every((a: { harness: string; model: string }) =>
				names.has(a.harness) && catalog.has(`${a.harness}/${a.model}`));
	})());



/* --- L1: the shelf, the closure walk and the Markdown subset ---------------- */

const asset = (over: Partial<AssetSummary> & { ref: string }): AssetSummary => ({
	kind: 'role',
	name: over.ref.split('/')[1] ?? 'x',
	title: '',
	origin: 'project',
	status: 'active',
	digest: 'd-' + over.ref,
	tokens: 10,
	references: [],
	shadows_bundled: false,
	...over
});

const shelfIndex = (rows: AssetSummary[]) => new Map(rows.map((row) => [row.ref, row]));

const CHAIN = [
	asset({ ref: 'role/impl', references: ['contract/gated', 'checkpoint-template/handoff'] }),
	asset({ ref: 'contract/gated', kind: 'contract', references: ['skill/checks', 'skill/gone'], tokens: 20 }),
	asset({ ref: 'skill/checks', kind: 'skill', references: ['skill/old'], tokens: 30 }),
	asset({ ref: 'skill/old', kind: 'skill', status: 'retired', tokens: 40 }),
	asset({ ref: 'checkpoint-template/handoff', kind: 'checkpoint-template', tokens: 50 })
];

ok('the closure is depth-first from the root, each ref emitted once',
	closureOf('role/impl', shelfIndex(CHAIN)).nodes.map((n) => n.ref).join(' ') ===
		'role/impl contract/gated skill/checks skill/old skill/gone checkpoint-template/handoff');

ok('the running total climbs by each resolved asset and by nothing else',
	(() => {
		const walk = closureOf('role/impl', shelfIndex(CHAIN));
		const gone = walk.nodes.find((n) => n.ref === 'skill/gone');
		return walk.tokens === 10 + 20 + 30 + 40 + 50 && gone?.running === 100;
	})());

ok('a reference that resolves to nothing is missing, and is not a retired one',
	(() => {
		const walk = closureOf('role/impl', shelfIndex(CHAIN));
		return walk.missing.join() === 'skill/gone' && walk.retired.join() === 'skill/old';
	})());

ok('an archived reference is only distinguishable from a missing one when the index holds it',
	(() => {
		// The shelf must be read with status=all: an index that dropped retired
		// assets reports a live archived reference as missing, which is a
		// different finding with a different fix.
		const active = shelfIndex(CHAIN.filter((row) => row.status === 'active'));
		return closureOf('role/impl', active).missing.includes('skill/old');
	})());

ok('a cycle is broken and reported rather than followed',
	(() => {
		const rows = [
			asset({ ref: 'agent/a', kind: 'agent', references: ['agent/b'] }),
			asset({ ref: 'agent/b', kind: 'agent', references: ['agent/a'] })
		];
		const walk = closureOf('agent/a', shelfIndex(rows));
		return walk.cycles.join() === 'agent/a' && walk.nodes.length === 3 &&
			walk.nodes[2].state === 'cycle';
	})());

ok('one broken ref declared by two assets is one broken reference, not two',
	(() => {
		const rows = [
			asset({ ref: 'role/r', references: ['contract/a', 'contract/b'] }),
			asset({ ref: 'contract/a', kind: 'contract', references: ['skill/gone'] }),
			asset({ ref: 'contract/b', kind: 'contract', references: ['skill/gone'] })
		];
		const walk = closureOf('role/r', shelfIndex(rows));
		return walk.missing.length === 1 &&
			walk.nodes.filter((n) => n.state === 'missing').length === 1;
	})());

ok('an asset that references nothing is a closure of one, not an error',
	(() => {
		const walk = closureOf('role/x', shelfIndex([asset({ ref: 'role/x', tokens: 7 })]));
		return walk.nodes.length === 1 && walk.nodes[0].state === 'root' && walk.tokens === 7;
	})());

ok('a diamond is carried once, so a shared asset is not counted twice',
	(() => {
		const rows = [
			asset({ ref: 'role/r', references: ['contract/a', 'contract/b'] }),
			asset({ ref: 'contract/a', kind: 'contract', references: ['skill/s'], tokens: 1 }),
			asset({ ref: 'contract/b', kind: 'contract', references: ['skill/s'], tokens: 1 }),
			asset({ ref: 'skill/s', kind: 'skill', tokens: 100 })
		];
		return closureOf('role/r', shelfIndex(rows)).tokens === 10 + 1 + 1 + 100;
	})());

ok("a memory leaf's references are evidence, so the walk stops at it",
	(() => {
		const rows = [
			asset({ ref: 'role/r', references: ['memory-leaf/m'] }),
			asset({ ref: 'memory-leaf/m', kind: 'memory-leaf', references: ['notes/x.md@sha'] })
		];
		return closureOf('role/r', shelfIndex(rows)).nodes.length === 2;
	})());

ok('a filter narrows the register and never the reading',
	(() => {
		const rows = filterShelf(CHAIN, { kind: 'skill', origin: 'all', status: 'active', query: '' });
		// role/impl is filtered out of the register; its closure is untouched.
		return rows.length === 1 && closureOf('role/impl', shelfIndex(CHAIN)).nodes.length === 6;
	})());

ok('the status filter defaults to active, so an archived asset is off the shelf',
	filterShelf(CHAIN, { kind: 'all', origin: 'all', status: 'active', query: '' })
		.every((row) => row.status === 'active'));

ok('find matches ref and title, case-folded, exactly as the daemon browses',
	filterShelf(
		[asset({ ref: 'role/a', title: 'The Implementer' }), asset({ ref: 'skill/b' })],
		{ kind: 'all', origin: 'all', status: 'active', query: 'IMPLEMENT' }
	).length === 1);

ok('kinds group in pipeline order and an empty kind is dropped, never drawn at zero',
	groupByKind(CHAIN).map((g) => g.kind).join() === 'role,contract,skill,checkpoint-template');

ok('referenced-by is the direction references cannot answer',
	referencedBy('skill/checks', CHAIN).join() === 'contract/gated');

ok('a non-active status is a slack or failed reading, never a seated one',
	statusState('active') === 'seated' && statusState('retired') === 'slack' &&
		statusState('conflicted') === 'failed');

const frozen = (over: Partial<AssetSnapshot> & { ref: string }): AssetSnapshot => ({
	kind: 'role', name: 'x', origin: 'project', title: '', references: [],
	body: 'b', digest: 'd1', tokens: 5, contract: null, ...over
});

ok('a frozen asset the shelf still matches, has edited, and has lost read as three states',
	(() => {
		const rows = compareFrozen(
			[
				frozen({ ref: 'role/same', digest: 'd1' }),
				frozen({ ref: 'role/moved', digest: 'd1' }),
				frozen({ ref: 'role/gone', digest: 'd1' })
			],
			shelfIndex([
				asset({ ref: 'role/same', digest: 'd1' }),
				asset({ ref: 'role/moved', digest: 'd2' })
			])
		);
		return rows.map((r) => r.drift).join() === 'same,edited,gone' &&
			rows[1].liveDigest === 'd2' && rows[2].liveDigest === null;
	})());

ok('the frozen body is the snapshot\'s, never the shelf\'s',
	compareFrozen([frozen({ ref: 'role/moved', body: 'approved bytes' })],
		shelfIndex([asset({ ref: 'role/moved', digest: 'd2' })]))[0].body === 'approved bytes');

ok('a fenced block keeps its own lines and never wraps them into prose',
	(() => {
		const blocks = parseMarkdown('text\n\n```sh\nuv run pytest\n  --quiet\n```\n\nafter');
		const code = blocks[1];
		return blocks.length === 3 && code.kind === 'code' && code.language === 'sh' &&
			code.lines.length === 2 && code.lines[1] === '  --quiet';
	})());

ok('an unclosed fence runs to the end rather than swallowing the file as prose',
	(() => {
		const blocks = parseMarkdown('# head\n\n```\nline one\nline two');
		const code = blocks[1];
		return code.kind === 'code' && code.lines.length === 2;
	})());

ok('a code span binds tighter than emphasis, so a glob in backticks survives',
	(() => {
		const spans = parseInline('use `src/*.py` and *this*');
		return spans[1].kind === 'code' && spans[1].value === 'src/*.py' &&
			spans[3].kind === 'em';
	})());

ok('an unclosed delimiter is the literal character, not an open run',
	(() => {
		const spans = parseInline('required_paths: src/* and a `tick');
		return spans.length === 1 && spans[0].kind === 'text' &&
			spans[0].value === 'required_paths: src/* and a `tick';
	})());

ok('an underscore inside a word is not emphasis',
	(() => {
		const spans = parseInline('set require_patch and allow_writes');
		return spans.length === 1 && spans[0].kind === 'text';
	})());

ok('a javascript: target is refused and renders as its own text',
	(() => {
		const spans = parseInline('[click](javascript:alert(1))');
		return spans.every((span) => span.kind !== 'link') &&
			spans.map((s) => (s.kind === 'text' ? s.value : '')).join('') === 'click';
	})());

ok('http, mailto, relative and fragment targets are kept',
	['[a](https://x.test)', '[a](mailto:x@y.test)', '[a](./rel.md)', '[a](#frag)']
		.every((source) => parseInline(source)[0].kind === 'link'));

ok('raw HTML in a body is characters, never markup',
	(() => {
		const spans = parseInline('<script>alert(1)</script>');
		return spans.every((span) => span.kind === 'text' || span.kind === 'link') &&
			spans.map((s) => (s.kind === 'text' ? s.value : '')).join('').includes('<script>');
	})());

ok('a pipe table needs its divider, or the pipes are ordinary characters',
	(() => {
		const table = parseMarkdown('| a | b |\n| --- | ---: |\n| 1 | 2 |');
		const prose = parseMarkdown('a | b is a sentence');
		return table[0].kind === 'table' && table[0].rows.length === 1 &&
			table[0].align.join() === 'left,right' && prose[0].kind === 'paragraph';
	})());

ok('a list nests by indent and stops at a new block',
	(() => {
		const blocks = parseMarkdown('- one\n  - nested\n- two\n\nafter');
		const list = blocks[0];
		return list.kind === 'list' && list.items.length === 3 &&
			list.items[1].depth === 1 && blocks[1].kind === 'paragraph';
	})());

ok('a thematic break is a rule, not a one-item list',
	parseMarkdown('---').every((block) => block.kind === 'rule'));

ok('the outline names every heading and nothing else',
	outline(parseMarkdown('# a\n\ntext\n\n## b')).map((h) => `${h.level}${h.text}`).join() === '1a,2b');

/* --- F2: the index band ----------------------------------------------------
   Every address the band offers must already resolve on a landed surface, and
   every row must come from a daemon enumeration that already exists: a run's
   path is the daemon's own `link.path` verbatim, a member's is the exact
   shape `runTarget()` parses, and an asset's is the exact shape `daemon.asset`
   encodes. A drift here is a band offering an address nothing resolves. */

const indexFleet = (runs: RunRollup[], attention?: AttentionItem[]): Fleet => ({
	runs,
	archived: 0,
	counts: {},
	running_runs: 0,
	total_runs: runs.length,
	attention,
	unreadable: []
});

const indexReport: CheckpointReport = {
	plan_id: 'check',
	initiatives: [
		reviewView([
			versionView(1, 'cp 1', { decision: 'rejected', superseded: true }),
			versionView(2, 'cp/2', { decision: 'changes_requested' })
		])
	],
	attention: []
};

// One member whose id carries a slash, the shape every deep link must survive.
const slashy = plan([node('a/b', [], 'pending', true)], ['a/b'], 1);

const band = buildIndex({
	views: VIEWS,
	fleet: indexFleet(
		[rollup({ link: { path: '/run?plan=plan_1&initiative=V1&checkpoint=c1' } })],
		[item(), item({ key: 'stalled:2', blocking: false })]
	),
	archived: indexFleet([rollup({ plan_id: 'plan_9', status: 'running' })]),
	assets: [asset({ ref: 'role/implementer', title: 'The Implementer' }), asset({ ref: 'skill/a b' })],
	graph: slashy,
	report: indexReport,
	planId: 'check'
});

ok('a run row carries the daemon\'s own link.path, byte for byte',
	band.find((row) => row.kind === 'run' && row.mark === 'plan_1')?.path ===
		'/run?plan=plan_1&initiative=V1&checkpoint=c1');

const memberRow = band.find((row) => row.kind === 'member')!;
ok('a member row\'s address round-trips through runTarget',
	runTarget(new URL('http://localhost' + memberRow.path).searchParams).initiative === 'a/b');
ok('a member row carries the graph\'s own state word, ready marked',
	memberRow.state === 'pending, ready' && memberRow.gloss === 'initiative a/b');

/* The reader opens the member's current (last) version only, so the band
   indexes exactly that: an initiative with two versions yields one checkpoint
   row, and it names the last version's id, never the prior one. */
const cpRows = band.filter((row) => row.kind === 'checkpoint');
ok('an initiative with two versions yields ONE checkpoint row, naming the CURRENT (last) version, not the prior one',
	cpRows.length === 1 && cpRows[0].mark === 'cp/2' && !cpRows.some((row) => row.mark === 'cp 1'));
const cpTarget = runTarget(
	new URL('http://localhost' + cpRows[0].path).searchParams
);
ok('a checkpoint row\'s address round-trips through runTarget with both ids',
	cpTarget.initiative === 'C1' && cpTarget.checkpoint === 'cp/2');
ok('an asset ref with a slash encodes to an address that decodes back to the ref',
	(() => {
		const row = band.find((entry) => entry.kind === 'asset' && entry.mark === 'role/implementer')!;
		return row.path.startsWith('/library?asset=') &&
			new URL('http://localhost' + row.path).searchParams.get('asset') === 'role/implementer';
	})());
ok('an asset ref with a space decodes back to itself too',
	new URL('http://localhost' + band.find((entry) => entry.mark === 'skill/a b')!.path)
		.searchParams.get('asset') === 'skill/a b');

const attentionStates = buildIndex({
	views: [],
	fleet: indexFleet([], [
		item({ key: 'cp:plan_1:1', kind: 'checkpoint_review', link: { path: '/run?plan=plan_1' } }),
		item({ key: 'failed:plan_1:1', kind: 'failed' })
	])
});
ok('an attention row waits in slack and is red only when the path broke',
	attentionStates[0].memberState === 'slack' && attentionStates[1].memberState === 'failed');

const ranked = filterRows(
	[
		{ kind: 'run', key: 'sub', mark: 'alphabet', gloss: 'x', state: '', memberState: 'balanced', path: '' },
		{ kind: 'run', key: 'pre', mark: 'beta', gloss: 'x', state: '', memberState: 'balanced', path: '' },
		{ kind: 'run', key: 'gloss', mark: 'gamma', gloss: 'the b road', state: '', memberState: 'balanced', path: '' }
	],
	'b'
);
ok('a mark prefix outranks a mark substring, which outranks a gloss match',
	ranked.map((row) => row.key).join() === 'pre,sub,gloss');
ok('an empty query is the identity', filterRows(ranked, '') === ranked);

const onlyRuns = groupRows(
	Array.from({ length: 8 }, (_, i) => ({
		kind: 'run' as const,
		key: `r${i}`,
		mark: `p${i}`,
		gloss: '',
		state: '',
		memberState: 'balanced' as const,
		path: ''
	}))
);
ok('a group caps at six rows and reports the true total',
	onlyRuns.length === 1 && onlyRuns[0].rows.length === 6 && onlyRuns[0].total === 8);
ok('empty groups are dropped, never drawn',
	groupRows([{ kind: 'view', key: 'v', mark: 'V', gloss: '', state: '', memberState: 'balanced', path: '' }])
		.map((group) => group.kind)
		.join() === 'view');

const stepList: LocateRow[] = ['a', 'b', 'c'].map((key) =>
	({ kind: 'run', key, mark: key, gloss: '', state: '', memberState: 'balanced', path: '' })
);
ok('the band\'s step wraps at both ends',
	stepRows(stepList, 'c', 1) === 'a' &&
		stepRows(stepList, 'a', -1) === 'c' &&
		stepRows(stepList, null, 1) === 'a');

const archivedRow = band.find((row) => row.kind === 'run' && row.mark === 'plan_9');
ok('an archived run is indexed and its state cell says so',
	archivedRow?.state === 'running · archived' && archivedRow.memberState === 'slack');

ok('a blocking attention item is indexed with the daemon\'s own link',
	band.some((row) => row.kind === 'attention' && row.path === item().link.path));
ok('a non-blocking attention item is not in the index',
	!band.some((row) => row.key === 'attention:stalled:2'));

ok('the chord table names the four views the shell chords into',
	CHORDS.r === 'run' && CHORDS.h === 'home' && CHORDS.l === 'library' && CHORDS.k === 'kitchen');

/* --- R5: the grouped walkthrough model -----------------------------------
   The claims this surface rests on are mostly refusals: the client classifies
   no path, re-sorts no cohort, rewrites no summary, recomputes no total, and
   never invents a cohort for a dropped path it cannot place. */

const wtView = (
	cohorts: { name: string; paths: string[]; summary: string }[],
	total: number = cohorts.reduce((n, c) => n + c.paths.length, 0)
): Walkthrough => ({ cohorts, total_files: total });
const wtVersion = (
	number: number,
	id: string,
	manifestPaths: string[],
	walkthrough: Walkthrough | null
): Version[] =>
	versionsOf(
		withVersions([manifest(id, manifestPaths)]),
		reviewView([versionView(number, id, walkthrough ? { walkthrough } : {})])
	);

const wtBase = wtVersion(1, 'w1', ['a/x.py', 'lib/g.py', 'tests/t.py'],
	wtView([
		{ name: 'daemon core', paths: ['a/x.py'], summary: '1 file changed; daemon and CLI behavior changed' },
		{ name: 'tests', paths: ['tests/t.py'], summary: '1 file changed; test coverage changed' },
		{ name: 'lib', paths: ['lib/g.py'], summary: '1 file under lib/' }
	]));
const wtHead = wtVersion(2, 'w2', ['a/y.py', 'a/x.py', 'lib/g.py', 'README.md'],
	wtView([
		{ name: 'daemon core', paths: ['a/x.py', 'a/y.py'], summary: '2 files changed; daemon and CLI behavior changed' },
		{ name: '(root)', paths: ['README.md'], summary: '1 file at repository root' },
		{ name: 'lib', paths: ['lib/g.py'], summary: '1 file under lib/' }
	]));
const wt = walkthroughOf(wtHead[0], wtBase[0], null);

ok('the walkthrough groups exactly the version\'s own paths, none twice',
	wt.cohorts.flatMap((c) => c.paths.map((p) => p.path)).sort().join() ===
		['a/x.py', 'a/y.py', 'lib/g.py', 'README.md'].sort().join());
ok('the head prints the daemon\'s total, even where it disagrees with the cohorts',
	walkthroughOf(wtHead[0], wtBase[0], null).totalFiles === 4 &&
		walkthroughOf(
			wtVersion(2, 'w3', ['z.py'], wtView([{ name: 'one', paths: ['z.py'], summary: 's' }], 7))[0],
			wtBase[0],
			null
		).totalFiles === 7);
ok('a path against a base carries exactly one mark, and never dropped',
	wt.cohorts
		.flatMap((c) => c.paths)
		.every((row) => row.mark === (row.path === 'a/y.py' || row.path === 'README.md' ? 'added' : 'carried')));
ok('dropped paths are grouped by the base\'s served cohorts, never by the client',
	wt.dropped.length === 1 &&
		wt.dropped[0].name === 'tests' &&
		wt.dropped[0].baseTotal === 1 &&
		false === wt.dropped.some((g) => g.name === 'ungrouped'));

const wtDropped = changesOf(wtHead[0], wtBase[0])?.dropped ?? [];
ok('the dropped register flattens to exactly the change list\'s dropped half',
	wtDropped.length === 1 && wt.dropped[0].paths.join() === wtDropped.join());

const wtNoBase = wtVersion(1, 'w4', ['a/y.py', 'README.md'],
	wtView([
		{ name: 'daemon core', paths: ['a/y.py'], summary: '1 file changed; daemon and CLI behavior changed' },
		{ name: '(root)', paths: ['README.md'], summary: '1 file at repository root' }
	]));
ok('with no approved base, no path is called added',
	walkthroughOf(wtNoBase[0], null, null).cohorts
		.flatMap((c) => c.paths)
		.every((p) => p.mark === null));

ok('an ungrouped version is reported ungrouped, never classified client-side',
	(() => {
		const view = walkthroughOf(
			wtVersion(2, 'w9', ['m.py', 'n.py'], null)[0], wtBase[0], null
		);
		return view.basis === 'ungrouped' &&
			view.ungrouped.join() === ['m.py', 'n.py'].sort().join() &&
			view.cohorts.length === 0;
	})());

ok('cohort names and summaries pass through byte-identical, including (root)',
	wt.cohorts.some((c) => c.name === '(root)' && c.summary === '1 file at repository root') &&
		wt.cohorts.every((c) =>
			['2 files changed; daemon and CLI behavior changed', '1 file at repository root', '1 file under lib/']
				.includes(c.summary)));

ok('cohort order is the daemon\'s, even when it is not alphabetical', (() => {
	const scrambled = wtVersion(2, 'w5', ['z.py'], wtView([
		{ name: 'zeta', paths: ['z.py'], summary: 's' },
		{ name: 'alpha', paths: ['a.py'], summary: 's' }
	]));
	return walkthroughOf(scrambled[0], wtBase[0], null).cohorts.map((c) => c.name).join() === 'zeta,alpha';
})());

const wtContract = {
	id: 'c', role: 'implementer',
	required_checks: [], required_paths: ['lib/g.py', 'never/touched.py'],
	require_patch: false, allow_writes: true, allowed_commands: null
} as Contract;
ok('a required path the version never touched is missing once and in no cohort',
	(() => {
		const view = walkthroughOf(wtHead[0], wtBase[0], wtContract);
		const grouped = view.cohorts.flatMap((c) => c.paths.map((p) => p.path));
		return view.missing.join() === 'never/touched.py' &&
			grouped.filter((p) => p === 'never/touched').length === 0 &&
			grouped.includes('lib/g.py');
	})());
ok('a rename-shaped change produces two unlinked entries',
	(() => {
		const old = wtVersion(1, 'w6', ['a/old.py'],
			wtView([{ name: 'daemon core', paths: ['a/old.py'], summary: 's' }]));
		const rev = wtVersion(2, 'w7', ['a/new.py'],
			wtView([{ name: 'daemon core', paths: ['a/new.py'], summary: 's' }]));
		const view = walkthroughOf(rev[0], old[0], null);
		const paths = view.cohorts.flatMap((c) => c.paths.map((p) => p.path));
		const droppedPaths = view.dropped.flatMap((g) => g.paths);
		return paths.join() === 'a/new.py' && droppedPaths.join() === 'a/old.py' &&
			view.cohorts.every((c) => c.paths.every((p) => !('renamedFrom' in p))) === true;
	})());
ok('a version with no walkthrough reports its path count and no cohort count',
	(() => {
		const past = versionsOf(
			withVersions([manifest('w8', ['a/x.py', 'lib/g.py'])]),
			reviewView([versionView(1, 'w8')])
		)[0];
		return past.walkthrough === null &&
			walkthroughOf(past, null, null).totalFiles === 2 &&
			walkthroughOf(past, null, null).basis === 'ungrouped';
	})());

/* --- R7: the packet inspector ---------------------------------------------
   Every claim here is either a rule the daemon already enforces (the fold
   rejects a snapshot whose total disagrees; `packet_diff` compares whole
   sections) or a rule this surface asserts about what it must never do: sort
   the record, offer an impossible comparison, or print a zero that is not a
   zero. A drift here is the inspector teaching an operator a packet that is
   not the one the daemon sent. */

const packetSection = (name: string, value: unknown, input: number): PacketSection =>
	({
		name,
		value,
		input_tokens: input,
		output_tokens: 0,
		source: 'estimate',
		phase: 'preflight',
		provenance: 'local estimate',
		category: 'repeated_context',
		semantic_work_id: null,
		gateway_used: false
	}) as PacketSection;

const packet = (
	sections: PacketSection[],
	total: number,
	extra: Partial<PacketSnapshot> = {}
): PacketSnapshot => ({ sections, total_tokens: total, provenance: 'local estimate', ...extra }) as PacketSnapshot;

const staged = (name: string, value: unknown, tokens: number): PacketSnapshot =>
	packet([packetSection(name, value, tokens)], tokens);

ok('the sections list renders the snapshot verbatim, order for order',
	sectionRows(
		packet([packetSection('a', 'x', 1), packetSection('b', 'y', 2), packetSection('c', 'z', 3)], 6)
	)
		.map((section) => section.name)
		.join() === 'a,b,c');
ok('a snapshot that arrives out of name order is not sorted back into place',
	sectionRows(
		packet([packetSection('c', 'z', 3), packetSection('a', 'x', 1), packetSection('b', 'y', 2)], 6)
	)
		.map((section) => section.name)
		.join() === 'c,a,b');
ok('the default attempt is the latest one that has a snapshot',
	defaultAttempt([
		attempt('a1', { packet_tokens: 0, packet_snapshot: null }),
		attempt('a2', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
		attempt('a3', { packet_tokens: 200, packet_snapshot: staged('brief', 'b', 200) })
	])?.id === 'a3');
ok('the default falls back past a snapshot-less latest attempt',
	defaultAttempt([
		attempt('a1', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
		attempt('a2', { packet_tokens: 0, packet_snapshot: null })
	])?.id === 'a1');
ok('with no snapshot anywhere the default is null, the missing state',
	defaultAttempt([attempt('a1', { packet_tokens: 0, packet_snapshot: null })]) === null);
ok('comparable attempts exclude the selection and every snapshot-less attempt',
	comparableAttempts(
		attempt('a2', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
		[
			attempt('a1', { packet_tokens: 50, packet_snapshot: staged('brief', 'b', 50) }),
			attempt('a2', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
			attempt('a3', { packet_tokens: 0, packet_snapshot: null })
		]
	)
		.map((a) => a.id)
		.join() === 'a1');
ok('one snapshot alone compares against nothing',
	comparableAttempts(
		attempt('a1', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
		[
			attempt('a1', { packet_tokens: 100, packet_snapshot: staged('brief', 'b', 100) }),
			attempt('a2', { packet_tokens: 0, packet_snapshot: null })
		]
	).length === 0);
const first = attempt('a1', {
	started_at: '2026-09-20T01:00:00Z',
	packet_tokens: 100,
	packet_snapshot: staged('brief', 'b', 100)
});
const second = attempt('a2', {
	started_at: '2026-09-20T02:00:00Z',
	packet_tokens: 200,
	packet_snapshot: staged('brief', 'b2', 200)
});
const pairOrder = [first, second];
ok('the pair is earlier-started first, whichever is selected',
	comparisonPair(first, second, pairOrder).before.id === 'a1' &&
		comparisonPair(second, first, pairOrder).before.id === 'a1' &&
		comparisonPair(first, second, pairOrder).after.id === 'a2');
ok('shape classification: empty, null and the four laid-out shapes',
	sectionBody('') .kind === 'empty' &&
		sectionBody([]).kind === 'empty' &&
		sectionBody(null).kind === 'null' &&
		sectionBody('text').kind === 'string' &&
		sectionBody(['a', 'b']).kind === 'list' &&
		sectionBody({ a: 1 }).kind === 'object' &&
		sectionBody([{ a: 1 }]).kind === 'objectList');
ok('a shape this build does not know falls through to raw and never throws',
	sectionBody(5).kind === 'raw' && sectionBody([1, 'a']).kind === 'raw');
ok('an output of zero prints nothing; a real output prints',
	outputTokens(packetSection('brief', 'b', 100)) === null &&
		outputTokens({ ...packetSection('brief', 'b', 1), output_tokens: 12 }) === 12);
const packetDiff = (
	changed: string[],
	added: string[],
	removed: string[]
): PacketDiff =>
	({
		changed_sections: changed,
		added_sections: added,
		removed_sections: removed,
		token_delta: 0,
		before_tokens: 0,
		after_tokens: 0,
		provenance: [],
		derivation: ''
	}) as PacketDiff;
const verdictDiff = packetDiff(['brief'], ['assets'], ['routes']);
ok('a changed section marks changed and an added one marks added',
	diffVerdict(verdictDiff, 'brief') === 'changed' && diffVerdict(verdictDiff, 'assets') === 'added');
ok('a removed section never marks a row in the selected snapshot',
	diffVerdict(verdictDiff, 'routes') === null);
ok('provenance sentences cover the daemon\'s measured vocabulary and nothing is guessed',
	sourceSentence('estimate') === 'estimated, not measured' &&
		sourceSentence('harness') === 'reported by the harness' &&
		sourceSentence('tokenizer') === 'counted by a tokenizer' &&
		sourceSentence('provider') === 'reported by the provider' &&
		sourceSentence('gateway') === 'gateway' &&
		sourceSentence('something-new') === 'something-new');
ok('a band whose sections disagree on provenance reports mixed, not a majority',
	commonProvenance(
		packet(
			[
				{ ...packetSection('a', 'x', 1), source: 'estimate' },
				{ ...packetSection('b', 'y', 2), source: 'harness' }
			],
			3
		)
	).source === null);
ok('a total that disagrees with its sections is reported, not picked',
	totalsAgree(staged('brief', 'b', 100)) === true &&
		totalsAgree(packet([packetSection('brief', 'b', 100)], 200)) === false);
ok('empty diff lists round-trip as None rather than as absent',
	diffList([]).none === true &&
		diffList([]).items.join() === 'None' &&
		diffList(['brief']).none === false);
ok('a bare token renders as a code chip and a sentence does not',
	codeLike('herdsman/graph.py') === true && codeLike('attempt a-V1-1 failed: reason') === false);

/* --- R11: run memory ------------------------------------------------------ */
const memoryPacket = packet([
	packetSection('inputs', 'x', 2),
	packetSection('memory', ['claim'], 3),
	packetSection('memory_leaf_ids', ['leaf-1'], 1),
	packetSection('memory_leaf_versions', ['3'], 1),
	packetSection('memory_mode', 'pointer', 1),
	packetSection('name', 'V1', 1)
], 9);
const memoryAttempt = attempt('memory-a1', {
	packet_snapshot: memoryPacket,
	memory_leaf_ids: ['leaf-1'],
	memory_leaf_versions: ['3'],
	memory_mode: 'pointer'
});
const memoryReceipt = (operation: MemoryReceipt['operation'], attempt_id: string | null, leaf_ids: string[] = ['leaf-1']): MemoryReceipt => ({
	type: 'memory_use_recorded', at: '2026-09-20T03:00:00Z', operation, tokens: 0,
	provenance: 'estimate', attempt_id, run_id: 'V1', leaf_ids, leaf_versions: ['3'], source_run: null
});
ok('the memory run is the consecutive memory family and sums its marginal rows',
	memoryRun(memoryPacket)?.tokens === 6 && memoryRun(memoryPacket)?.sections.map((s) => s.name).join() === 'memory,memory_leaf_ids,memory_leaf_versions,memory_mode');
ok('an interrupted memory family is not fenced or summed',
	memoryRun(packet([packetSection('memory', 'x', 1), packetSection('name', 'x', 1), packetSection('memory_mode', 'x', 1)], 3)) === null);
ok('no memory sections means no memory run', memoryRun(packet([packetSection('name', 'x', 1)], 1)) === null);
ok('carried ids and versions pair index-for-index, but mismatches are refused',
	carriedLeaves(memoryAttempt)?.[0].id === 'leaf-1' &&
	carriedLeaves({ ...memoryAttempt, memory_leaf_versions: [] } as Attempt) === null);
ok('a zero-token receipt is a value and pulls join only through carried ids',
	receiptsFor(memoryAttempt, { memory_receipts: [memoryReceipt('pointer', 'memory-a1'), memoryReceipt('pull', null), memoryReceipt('pull', null, ['other'])] }).length === 2);
const statusNow = ({ status, version = 3 }: { status: string; version?: number }): MemoryStatus => ({
	plan_id: 'p', attention: [], leaves: [{ id: 'leaf-1', version, status, subject: 's', claim: 'c' } as never]
});
ok('current active carried version is silent, while changed version is historical then current',
	currentVerdict(statusNow({ status: 'active' }), 'leaf-1', '3') === null &&
	currentVerdict(statusNow({ status: 'active', version: 5 }), 'leaf-1', '3')?.kind === 'version');
ok('stale, conflicted, retired and gone verdicts preserve the receipt history',
	(['stale', 'conflicted', 'retired'] as const).every((status) => currentVerdict(statusNow({ status }), 'leaf-1', '3')?.line.includes('What this attempt received is unchanged')) &&
	currentVerdict({ plan_id: 'p', attention: [], leaves: [] }, 'leaf-1', '3')?.kind === 'gone');
ok('unread status never produces a current verdict', currentVerdict(null, 'leaf-1', '3') === null);
ok('answered-from-memory includes the served version', answeredFromMemory('leaf-1', '3') === 'Answered from memory: leaf-1@3.');
ok('salvage availability prints both independent blockers',
	(() => {
		const plan = { initiatives: { V1: member({ failures: [{ reason: 'failed', evidence: ['check:x'] }] }) }, retired: [] } as Pick<Plan, 'initiatives' | 'retired'>;
		const result = salvageAvailability(plan, { phase: 'read', authorName: null });
		return !result.available && result.rules.length === 1;
	})());

/* --- R8: the burn instruments -------------------------------------------- */
//
// The claims the plate rests on: the daemon's facts are read and never
// recomputed, a cap that was never declared is neither zero nor unlimited, no
// member is drawn without a unit, and every absence gets its own sentence —
// never a zero wearing a figure's clothes.

const ledgerTotals = (over: Partial<{ actual: number; preflight: number; estimate: number }> = {}) => ({
	actual: 0,
	preflight: 0,
	estimate: 0,
	productive: 0,
	orchestration: 0,
	provenance: {},
	derivation: {},
	...over
});

ok('no-ratio is not zero: a null ratio reads as an absence, never 0% or 100%',
	ratioReading({
		orchestration_tokens: 23_400,
		productive_tokens: 0,
		ratio: null,
		target: 0.2,
		within_target: null,
		derivation: '',
		provenance: [],
		recalibration_tokens: 0,
		recalibration_calls: 0,
		recalibration_derivation: ''
	}).value === null &&
		ratioReading({
			orchestration_tokens: 23_400,
			productive_tokens: 0,
		ratio: null,
		target: 0.2,
		within_target: null,
		derivation: '',
		provenance: [],
		recalibration_tokens: 0,
		recalibration_calls: 0,
		recalibration_derivation: ''
		}).absence?.includes('no ratio to take') === true);

ok('within_target is the daemon\'s fact, never recomputed against the ratio here',
	// An impossible pair on purpose: the client reports the flag, not its own
	// comparison, so a 50% ratio flagged within target reads as within target.
	ratioReading({
		orchestration_tokens: 100,
		productive_tokens: 200,
		ratio: 0.5,
		target: 0.2,
		within_target: true,
		derivation: '',
		provenance: [],
		recalibration_tokens: 0,
		recalibration_calls: 0,
		recalibration_derivation: ''
	}).state === 'seated');

ok('a null cap is not an unlimited one: no figure, no member, its own sentence',
	budgetReading(null, null).value === null &&
		budgetReading(null, null).drawMember === false &&
		burnSegments(100, 200, null) === null);

ok('a null cap is not a zero cap: a declared, exhausted ceiling reads distinctly',
	budgetReading(0, 0).value === '0 of 0' && budgetReading(0, 0).drawMember === true);

ok('segments never exceed the unit: an overrun sums to one and draws no headroom',
	(() => {
		const over = burnSegments(120_000, 31_000, 100_000);
		const sum = over?.reduce((total, segment) => total + segment.share, 0) ?? 0;
		return over !== null && Math.abs(sum - 1) < 1e-9 && !over?.some((s) => s.kind === 'headroom');
	})());

ok('weight is load: productive and orchestration at 2.5px, headroom at 1',
	BURN_SEGMENT_WEIGHT.productive === 2.5 &&
		BURN_SEGMENT_WEIGHT.orchestration === 2.5 &&
		BURN_SEGMENT_WEIGHT.headroom === 1);

ok('phase words join highest-precedence-first: measured before estimated',
	joinedPhases(['estimate', 'actual', 'preflight']) === 'measured · preflight · estimated');

ok('the six absence sentences are six different strings',
	new Set(ABSENCE_SENTENCES).size === 6);

ok('the three anomaly sentences are three different strings, none shared with the absences',
	new Set([EXHAUSTED_BUDGET, PLAN_COMPLETE]).size === 2 &&
		!ABSENCE_SENTENCES.includes(EXHAUSTED_BUDGET));

ok('anomalies group by code, not by member: six missing rows are one group of six ids',
	(() => {
		const anomaly = (id: string): Parameters<typeof groupAnomalies>[0][number] => ({
			code: 'missing-usage',
			message: 'checkpoint has no usage',
			initiative_id: id,
			attempt_id: `a-${id}`
		});
		const groups = groupAnomalies([anomaly('A'), anomaly('B'), anomaly('C'), anomaly('D'), anomaly('E'), anomaly('F')]);
		return groups.length === 1 && groups[0].count === 6 && groups[0].ids.length === 6;
	})());

ok('the count is the daemon\'s, undivided, whatever the surface renders where',
	anomalyCount([
		{ code: 'overhead', message: 'm', initiative_id: null, attempt_id: null },
		{ code: 'missing-usage', message: 'm', initiative_id: 'A', attempt_id: 'a' }
	]) === 2);

ok('a plan-level exhausted budget remains named in its group',
	(() => {
		const group = groupAnomalies([
			{ code: 'exhausted-budget', message: 'plan cap exhausted', initiative_id: null, attempt_id: null },
			{ code: 'exhausted-budget', message: 'member cap exhausted', initiative_id: 'B2', attempt_id: 'a-B2' }
		])[0];
		return group.count === 2 && group.plan && group.ids.join() === 'B2';
	})());

ok('one missing checkpoint uses singular grammar',
	groupAnomalies([{
		code: 'missing-usage', message: 'checkpoint has no usage', initiative_id: 'A', attempt_id: 'a-A'
	}])[0].text.startsWith('1 checkpoint closed'));

ok('a member with no declared cap is absent from the ceilings list, counted in n of m',
	(() => {
		const ceilings = ceilingsOf({ A: 100, B: null, C: 0 });
		return (
			ceilings.rows.length === 2 &&
			ceilings.declared === 2 &&
			ceilings.total === 3 &&
			ceilings.rows.find((row) => row.id === 'C')?.state === 'failed'
		);
	})());

ok('a null ETA always carries the daemon\'s own reason, interpolated unmodified',
	(() => {
		const reason = 'duration estimate unknown for X9';
		const reading = etaReading({ eta: null, remaining_seconds: null, reason, derivation: '', provenance: [] });
		return reading.value === null && reading.absence?.includes(reason) === true;
	})());

ok('plan complete is not a zero duration',
	etaReading({
		eta: '2026-09-20T12:00:00Z',
		remaining_seconds: 0,
		reason: 'plan complete',
		derivation: '',
		provenance: []
	}).gloss === PLAN_COMPLETE);

ok('no series is derived: the model exports no bucketing of entries by time',
	// The burn-down is the point the daemon serves. A client-side series would
	// be "settled attempts that reported usage" wearing a chart's clothes,
	// because observed_at is populated on checkpoint-usage rows alone.
	!Object.keys(burnModule).some((name) => /series|bucket|sparkline/i.test(name)));

ok('coarse stays coarse and never prints a stopwatch',
	coarse(45) === 'moments' && coarse(300) === '5 min' && coarse(7200) === '2 h');

ok('the ruled label word is the highest phase present, and estimate-only is detected',
	phaseWord(ledgerTotals({ actual: 10, estimate: 5 })) === 'measured' &&
		phaseWord(ledgerTotals({ estimate: 5 })) === 'estimated' &&
		phaseWord(ledgerTotals()) === 'nothing measured' &&
		estimateOnly(ledgerTotals({ estimate: 5 })) &&
		!estimateOnly(ledgerTotals({ preflight: 5 })));

ok('category attribution drops zero-valued categories and sorts largest first',
	categoryString({ execution: 100_000, repeated_context: 18_000, monitoring: 0, protocol: 9_000 }) ===
		'execution 100.0k · repeated context 18.0k · protocol 9,000');

const revisionFixture: RecalibrationReport = {
	plan_id: 'r10-check', from_version: 1, to_version: 2, approval: 'pending',
	revision: {
		plan_id: 'r10-check', from_version: 1, to_version: 2,
		nodes: [
			{ change: 'unchanged', old_ids: ['G4'], new_ids: ['G9'], old_digest: '1234567890abcdef', new_digest: '1234567890abcdef', old_token_caps: [40000], new_token_caps: [60000], renamed: true, edge_state: 'same', old_attempts: 2, new_attempts: 2 },
			{ change: 'split', old_ids: ['G5'], new_ids: ['G5a', 'G5b', 'G5c'], old_digest: 'old', new_digest: null, old_token_caps: [null], new_token_caps: [20000, 20000, null], renamed: false, edge_state: 'changed', old_attempts: 0, new_attempts: 0 },
			{ change: 'removed', old_ids: ['G6'], new_ids: [], old_digest: 'old', new_digest: null, old_token_caps: [null], new_token_caps: [], renamed: false, edge_state: 'same', old_attempts: 1, new_attempts: 0 }
		],
		counts: { unchanged: 1, edited: 0, split: 1, merged: 0, new: 0, removed: 1 }, ambiguous: ['abcdef0123456789'], derivation: 'daemon'
	},
	impact: { plan_id: 'r10-check', from_version: 1, to_version: 2, downstream: [{ initiative_id: 'G7', state: 'pending', attempts: 0 }], stranded: [], dropped: ['G6'], plan_token_cap_from: 40000, plan_token_cap_to: 60000, allowance_resets: [{ initiative_id: 'G5a', source_ids: ['G5'], consumed_attempts: 0, source_status: 'proven', candidate_source_ids: [] }], derivation: 'daemon'
	}
};
const revisionRows = revisionFixture.revision.nodes.map((node) => rowFor(node, revisionFixture, null, null));
const revisionBands = bandsOf(revisionFixture, null, null, null);
ok('renumbering stays unchanged and carries its marker', revisionRows[0].band === 'unchanged' && revisionRows[0].markers.includes('RENUMBERED') && !revisionRows[0].markers.includes('EDITED'));
ok('cap-only changes carry allowance moved', revisionRows[0].markers.includes('ALLOWANCE MOVED') && revisionRows[0].cap === 'token cap 40,000 → 60,000');
ok('split cardinality comes from both id lists', revisionRows[1].cardinality === '1 → 3');
ok('removed rows are not selectable', revisionRows[2].selectableId === null && revisionRows[2].state === 'slack');
ok('unresolved edges are failed but changed edges are not', rowFor({ ...revisionFixture.revision.nodes[1], edge_state: 'unresolved' }, revisionFixture, null, null).state === 'failed' && revisionRows[1].state !== 'failed');
ok('all four revision bands are present', revisionBands.map((band) => band.key).join(',') === 'fixed,moved,added,unchanged');
ok('downstream order is preserved', revisionFixture.impact.downstream[0].initiative_id === 'G7');
ok('null cap is rendered as no cap', formatCap(null) === 'no cap');
ok('ambiguous digests retain an eight-character display prefix', revisionFixture.revision.ambiguous[0].slice(0, 8) === 'abcdef01');
ok('no-revision conflict is distinguished from other conflicts', refusalMessage(409, 'plan has no revision') === 'first' && refusalMessage(409, 'plan changed') === 'refusal' && refusalMessage(null, 'offline') === 'failed');

console.log(failures === 0 ? '\nfield, gate, review, intervention, bank, rig, shelf, markdown, index and revision models: all checks pass' : `\nfield, gate, review, intervention, bank, rig, shelf, markdown, index and revision models: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
