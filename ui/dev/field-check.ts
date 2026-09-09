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

import { buildField, phaseOf, step } from '../src/lib/field.ts';
import { because, calloutsOf, downstream, lead, registerOf, shapeOf } from '../src/lib/gate.ts';
import {
	allowed,
	artifactsOf,
	changesOf,
	checksOf,
	consumersOf,
	impactOf,
	summarize,
	versionsOf
} from '../src/lib/review.ts';
import type {
	Checkpoint,
	CheckpointVersionView,
	Contract,
	Initiative,
	InitiativeReviewView,
	NodeStatus,
	Plan,
	PlanGraph,
	RiskReport,
	Taint
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

console.log(failures === 0 ? '\nfield, gate and review models: all checks pass' : `\nfield, gate and review models: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
