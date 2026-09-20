<!--
  Unit R1 — the Run spine, drawn as The Contention Field. The direction
  contract for this surface is in `src/app.html`, where the production build
  keeps it (seed c5eeafdc). Siblings deliberately absent: the approval gate is
  R3, token instruments R8, replay R12.

  R2 joined here: the selected-member readout gained one control, and the
  drawer it opens is `$lib/InitiativeDrawer.svelte`. Its own direction contract
  is in `.impeccable/surfaces/ui-src-lib-initiativedrawer-svelte.md` -- it ran
  no concept round, so it owns no seed in `app.html`.

  R3 joined here too: a proposed revision opens `$lib/PlanGate.svelte` in the
  same right-edge slot, and R1's note that approval "is unit R3 and is not
  built here" is retired with it. The gate sits one layer under the drawer, so
  selecting a member from the register covers the gate and closing it returns.

  R4 added the fourth read -- `GET /plans/{id}/checkpoints`, the review
  lifecycle -- and nothing else here. Checkpoint review lives inside the
  drawer, and the sheet that widens for it is the drawer's own.

  R6 added nothing to this page at all. Its six writes are addressed to one
  initiative and are made from inside the drawer; they re-read through the
  same callback a checkpoint verdict already used, because a retry moves the
  field, the risk report and the fold exactly as a verdict does.
-->
<script lang="ts">
	import { getContext } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import AsyncField from '$lib/AsyncField.svelte';
	import BurnPlate from '$lib/BurnPlate.svelte';
	import BurnLists from '$lib/BurnLists.svelte';
	import BurnAttribution from '$lib/BurnAttribution.svelte';
	import Salvage from '$lib/Salvage.svelte';
	import ReplayBar from '$lib/ReplayBar.svelte';
	import ReplayRegister from '$lib/ReplayRegister.svelte';
	import ContentionField from '$lib/ContentionField.svelte';
	import InitiativeDrawer from '$lib/InitiativeDrawer.svelte';
	import PlanGate from '$lib/PlanGate.svelte';
	import Recovery from '$lib/Recovery.svelte';
	import { Resource } from '$lib/resource.svelte';
	import {
		daemon,
		type CheckpointReport,
		type Fleet,
		type InitiativeFailedFrame,
		type Kitchen,
		type MemoryStatus,
		type Plan,
		type PlanGraph,
		type RecoveryReport,
		type RecalibrationReport,
		type RiskReport,
		type RunRollup,
		type RuntimeObservedFrame,
		type StatusBundle,
		type TokenLedger
	} from '$lib/daemon';
	import { buildField, contentionIndex, phaseOf, runTarget, step, type Field, type Member } from '$lib/field';
	import {
		boundOf,
		nearestStop,
		qualifies,
		qualificationSentence,
		runPhase,
		setHistorical as setHistoricalLatch,
		stopsOf,
		stopReading,
		type ReplayStop
	} from '$lib/replay';

	const plan = getContext<{
		readonly resource: Resource<PlanGraph> | null;
		readonly id: string | null;
		reload: () => void;
	}>('plan');

	/* --- choosing a plan -----------------------------------------------------
	   A plan is chosen from the plans that exist, never typed: `GET /fleet` is
	   the enumeration (Sprint 10), and its row already carries the brief,
	   revision, approval and progress a choice is actually made on. The read is
	   started only when no plan is addressed, because an addressed Run has no
	   use for it. */
	let runs = $state<Resource<Fleet> | null>(null);
	let chosen = $state('');
	$effect(() => {
		if (plan.id || runs) return;
		const resource = new Resource<Fleet>((signal) => daemon.fleet(signal));
		runs = resource;
		void resource.load();
	});

	/** The row for what is selected, so the sheet can describe it before opening. */
	function rowOf(fleet: Fleet, planId: string): RunRollup | null {
		return fleet.runs.find((run) => run.plan_id === planId) ?? null;
	}

	/** Newest-first is the fleet's own order; opening follows its own deep link. */
	function open(event: SubmitEvent, fleet: Fleet): void {
		event.preventDefault();
		const run = rowOf(fleet, chosen);
		if (run) void goto(run.link.path);
	}

	const APPROVAL: Record<string, string> = {
		approved: 'approved',
		pending: 'not approved'
	};

	/* Contention is a second read: the graph draws without it, so a risk report
	   that fails leaves the field standing with its cords explicitly unread.
	   R2 adds a third — the folded plan, which carries the planner-authored
	   content the graph deliberately omits. R4 adds a fourth — the checkpoint
	   report, which carries the review lifecycle the fold projects nowhere
	   else. Each stands alone: a failed plan read leaves the field and the
	   schedule drawn, and a failed review read leaves the manifests readable
	   with their verdicts explicitly unread. */
	let risk = $state<Resource<RiskReport> | null>(null);
	let folded = $state<Resource<Plan> | null>(null);
	let reviews = $state<Resource<CheckpointReport> | null>(null);
	/* R8's fifth and sixth reads. `/status` bundles the graph, overhead,
	   burn-down, ETA and anomalies into one request; `/tokens` is the separate
	   ledger the category attribution reads from, because the bundle carries no
	   `by_category` and a failed read there leaves the category string
	   explicitly unread rather than absent. Each stands alone like the others. */
	let status = $state<Resource<StatusBundle> | null>(null);
	let ledger = $state<Resource<TokenLedger> | null>(null);
	let recovery = $state<Resource<RecoveryReport> | null>(null);
	const recoveryHasProbe = $derived(!!recovery?.data && Object.keys(recovery.data.outcomes).length > 0);
	let revision = $state<Resource<RecalibrationReport> | null>(null);
	let memoryStatus = $state<Resource<MemoryStatus> | null>(null);
	let kitchen = $state<Resource<Kitchen> | null>(null);
	let requested = $state<string | null>(null);
	let previousPlanId = $state<string | null>(null);
	let replayed = $state<Resource<Plan> | null>(null);
	let stops = $state<ReplayStop[]>([]);
	let replayIndex = $state(0);
	let replayKey = $state('');
	let replayNotice = $state('');
	let replayEntry = $state<HTMLButtonElement | null>(null);
	let historical = $state(false);
	const setReplay = (value: boolean) => {
		historical = value;
		setHistoricalLatch(value);
	};
	$effect(() => {
		const id = plan.id;
		if (id === requested) return;
		requested = id;
		if (previousPlanId !== null && previousPlanId !== id) {
			const url = new URL(page.url);
			url.searchParams.delete('at');
			replaceState(url, {});
		}
		previousPlanId = id;
		risk?.dispose();
		folded?.dispose();
		reviews?.dispose();
		status?.dispose();
		ledger?.dispose();
		recovery?.dispose();
		revision?.dispose();
		memoryStatus?.dispose();
		kitchen?.dispose();
		replayed?.dispose();
		replayed = null;
		stops = [];
		replayKey = '';
		setReplay(false);
		activity = [];
		failures = {};
		drawerId = null;
		if (!id) {
			risk = null;
			folded = null;
			reviews = null;
			status = null;
			ledger = null;
			recovery = null;
			revision = null;
			memoryStatus = null;
			kitchen = null;
			return;
		}
		const report = new Resource<RiskReport>((signal) => daemon.risk(id, signal));
		risk = report;
		void report.load();
		const document = new Resource<Plan>((signal) => daemon.plan(id, signal));
		folded = document;
		void document.load();
		const checkpoints = new Resource<CheckpointReport>((signal) =>
			daemon.checkpoints(id, signal)
		);
		reviews = checkpoints;
		void checkpoints.load();
		const bundle = new Resource<StatusBundle>((signal) => daemon.status(id, signal));
		status = bundle;
		void bundle.load();
		const ledgerRead = new Resource<TokenLedger>((signal) => daemon.tokens(id, signal));
		ledger = ledgerRead;
		void ledgerRead.load();
		const recoveryReport = new Resource<RecoveryReport>((signal) => daemon.recovery(id, signal));
		recovery = recoveryReport;
		void recoveryReport.load();
		const comparison = new Resource<RecalibrationReport>((signal) => daemon.revision(id, signal));
		revision = comparison;
		void comparison.load();
		const memoryRead = new Resource<MemoryStatus>((signal) => daemon.memoryStatus(id, signal));
		memoryStatus = memoryRead;
		void memoryRead.load();
		const kitchenRead = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
		kitchen = kitchenRead;
		void kitchenRead.load();
	});

	/* Replay is a second Plan read. The live graph remains the structural source;
	   this resource supplies only historical member state and fold-backed readers. */
	$effect(() => {
		const id = plan.id;
		const livePlan = folded?.data;
		const requestedAt = page.url.searchParams.get('at');
		if (!id || !livePlan || !requestedAt) return;
		const phase = runPhase(livePlan);
		if (!qualifies(phase)) {
			setReplay(false);
			replayNotice = 'This address asks for a past state of a run that has not stopped. It is showing the run as it stands.';
			return;
		}
		const nextStops = stops.length > 0 ? stops : stopsOf(livePlan);
		if (stops.length === 0) stops = nextStops;
		const nextIndex = nearestStop(nextStops, requestedAt);
		const resolvedAt = boundOf(nextStops, nextIndex);
		replayNotice = requestedAt === resolvedAt ? '' : 'No record was written at the moment this address names. Reading the last one before it.';
		if (replayIndex !== nextIndex) replayIndex = nextIndex;
		if (!historical) {
			setReplay(true);
			activity = [];
			failures = {};
			gateOpen = false;
		}
	});

	$effect(() => {
		const id = plan.id;
		const at = historical && stops.length > 0 ? boundOf(stops, replayIndex) : null;
		const key = id && at ? `${id}@${at}` : '';
		if (!key || key === replayKey) return;
		replayKey = key;
		const previous = replayed;
		previous?.dispose();
		const resource = new Resource<Plan>((signal) => daemon.replay(id!, at!, signal));
		if (previous?.data) {
			resource.data = previous.data;
			resource.phase = 'ready';
			resource.stale = true;
		}
		replayed = resource;
		const timer = setTimeout(() => void resource.load(), 120);
		return () => clearTimeout(timer);
	});

	function moveReplay(index: number): void {
		if (stops.length === 0) return;
		const next = Math.max(0, Math.min(stops.length - 1, index));
		replayIndex = next;
		const url = new URL(page.url);
		url.searchParams.set('at', boundOf(stops, next));
		replaceState(url, {});
	}

	function enterReplay(): void {
		const document = folded?.data;
		if (!document || !qualifies(runPhase(document))) return;
		stops = stopsOf(document);
		replayIndex = stops.length - 1;
		setReplay(true);
		activity = [];
		failures = {};
		const url = new URL(page.url);
		url.searchParams.set('at', boundOf(stops, replayIndex));
		replaceState(url, {});
	}

	function returnToLive(): void {
		setReplay(false);
		replayed?.dispose();
		replayed = null;
		stops = [];
		replayKey = '';
		replayNotice = '';
		activity = [];
		failures = {};
		const url = new URL(page.url);
		url.searchParams.delete('at');
		replaceState(url, {});
		plan.reload();
		void risk?.load();
		void folded?.load();
		void reviews?.load();
		void status?.load();
		void ledger?.load();
		queueMicrotask(() => replayEntry?.focus());
	}

	/* What the fold does not keep, this page keeps for as long as it is open —
	   and says plainly that it starts empty. `RuntimeObserved` carries no
	   projected state, and `InitiativeFailed.reason` is dropped by the fold, so
	   the live stream is the only place either exists. */
	let activity = $state<RuntimeObservedFrame[]>([]);
	let failures = $state<Record<string, string>>({});

	function remember(type: string, data: unknown) {
		if (type === 'runtime_observed') {
			const frame = data as RuntimeObservedFrame;
			if (typeof frame?.attempt_id === 'string') activity = [...activity, frame];
		} else if (type === 'initiative_failed') {
			const frame = data as InitiativeFailedFrame;
			if (typeof frame?.initiative_id === 'string' && typeof frame.reason === 'string') {
				failures = { ...failures, [frame.initiative_id]: frame.reason };
			}
		}
	}

	/** This initiative's observations, in arrival order. Attempts hold the link. */
	function activityFor(id: string): { at: string; kind: string }[] {
		const attempts = new Set(
			(folded?.data?.initiatives[id]?.attempts ?? []).map((attempt) => attempt.id)
		);
		return activity.filter((frame) => attempts.has(frame.attempt_id));
	}

	/* The shell wired SSE and left it unconsumed; this is its first consumer.
	   A burst of events costs one re-read, and a dropped stream marks what is on
	   screen stale rather than freezing a projection that looks live. */
	/* null until the stream answers: connecting is not the same as dropped. */
	let live = $state<boolean | null>(null);
	$effect(() => {
		const id = plan.id;
		if (!id || historical) return;
		let timer: ReturnType<typeof setTimeout> | undefined;
		const stop = daemon.events(
			id,
			(type, data) => {
				remember(type, data);
				clearTimeout(timer);
				timer = setTimeout(() => {
					plan.reload();
					void risk?.load();
					void folded?.load();
					void reviews?.load();
					void status?.load();
					void ledger?.load();
					void recovery?.load();
					void revision?.load();
					void memoryStatus?.load();
				}, 120);
			},
			(connected) => {
				live = connected;
				if (!connected) {
					plan.resource?.markStale();
					risk?.markStale();
					folded?.markStale();
					reviews?.markStale();
					status?.markStale();
					ledger?.markStale();
					recovery?.markStale();
					revision?.markStale();
					memoryStatus?.markStale();
					kitchen?.markStale();
				}
			}
		);
		return () => {
			clearTimeout(timer);
			live = null;
			stop();
		};
	});

	/* Selection is an initiative id and nothing positional, so a live update
	   that reorders or re-ranks the field cannot move what you were reading. */
	let selectedId = $state<string | null>(null);
	let drawerId = $state<string | null>(null);
	let targetCheckpointId = $state<string | null>(null);
	/* True only when the *address* opened the drawer, never a click: arrival
	   focus is claimed by the drawer's own heading in that case, and a click
	   must not steal the caret from a field the operator is reading. */
	let focusOnOpen = $state(false);
	/* Which address `select` itself wrote, so the effect that address triggers
	   can tell a click from an arrival. A plain value: no render reads it, and
	   an effect that reads what it writes re-triggers itself. */
	let clickWrote: string | null = null;
	const select = (id: string) => {
		selectedId = id;
		drawerId = id;
		targetCheckpointId = null;
		focusOnOpen = false;
		/* The drawer becomes addressable, with L1's precedent for the write:
		   replaceState, so a locator jump never fills the back stack with drawer
		   states. Dropping the checkpoint is the same rule — the address names
		   what you are reading, and a selection does not name one. */
		const url = new URL(page.url);
		url.searchParams.set('initiative', id);
		url.searchParams.delete('checkpoint');
		/* Claim this write, so the address effect it triggers knows a click made
		   it and does not take the caret. */
		clickWrote = `${plan.id ?? ''}\u0000${id}\u0000`;
		replaceState(url, {});
	};

	/* Fleet links address the existing Run drawer rather than inventing an
	   attention surface. A checkpoint link opens the same member and asks its
	   existing review section to take the reading position. */
	let addressedLink = $state('');
	$effect(() => {
		const { initiative, checkpoint } = runTarget(page.url.searchParams);
		const address = `${plan.id ?? ''}\u0000${initiative ?? ''}\u0000${checkpoint ?? ''}`;
		/* Consumed before any early return: a stamp left armed would suppress the
		   caret on a later arrival at the same member. */
		const wrote = clickWrote;
		clickWrote = null;
		if (address === addressedLink) return;
		addressedLink = address;
		if (!plan.id || !initiative) return;
		/* What separates an address from a click is which one wrote the address,
		   not whether the drawer happened to be shut: a locator jump from one
		   open member to another is still an arrival and still owes the caret.
		   `select` stamps its own write here and this consumes the stamp, so the
		   effect stays idempotent — the composed address equals the held state,
		   and it cannot re-trigger itself into a loop. */
		const byAddress = address !== wrote;
		selectedId = initiative;
		drawerId = initiative;
		targetCheckpointId = checkpoint;
		focusOnOpen = byAddress;
	});

	/* The drawer expands on selection but holds its own id rather than reading
	   the selection, so a live re-read that drops the initiative leaves it open
	   and says so, and closing it does not clear what you have selected.
	   Re-selecting the same member expands it again. Closing also removes the
	   address: what you are reading stops being the page's own. */
	const closeDrawer = () => {
		drawerId = null;
		targetCheckpointId = null;
		focusOnOpen = false;
		const url = new URL(page.url);
		url.searchParams.delete('initiative');
		url.searchParams.delete('checkpoint');
		replaceState(url, {});
	};

	/* --- the approval gate (R3) ---------------------------------------------
	   A proposed revision has exactly one available action, so the gate opens
	   itself once per plan-and-revision rather than hiding the only thing that
	   can be done with what is on screen. Closing it is then respected until
	   the plan or its revision actually changes. */
	let gateOpen = $state(false);
	let gateSeen = $state<string | null>(null);
	let gateTrigger = $state<HTMLButtonElement | null>(null);
	$effect(() => {
		const graph = plan.resource?.data;
		if (!graph) return;
		const key = `${graph.plan_id}@${graph.version}`;
		if (gateSeen === key) return;
		gateSeen = key;
		gateOpen = graph.approval !== 'approved';
	});

	/* Focus moves into the sheet only when the operator asked for it. An
	   auto-opened gate does not steal the caret from a page that just loaded. */
	function openGate() {
		gateOpen = true;
		queueMicrotask(() => document.getElementById('gate-title')?.focus());
	}
	function focusRecovery() {
		const target = document.getElementById('recovery-label');
		target?.scrollIntoView({ block: 'start', behavior: 'auto' });
		queueMicrotask(() => target?.focus());
	}
	function closeGate() {
		gateOpen = false;
		gateTrigger?.focus();
	}

	function onScheduleKey(event: KeyboardEvent, order: string[]) {
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		const next = step(order, selectedId, event.key === 'ArrowDown' ? 1 : -1);
		if (!next) return;
		event.preventDefault();
		select(next);
		document.getElementById(`row-${next}`)?.focus();
	}

	const PHASE: Record<string, string> = {
		proposed: 'Proposed',
		running: 'Running',
		settled: 'Settled'
	};

	function waiting(m: Member): string {
		if (m.node.state !== 'pending') return '—';
		return m.node.ready ? 'nothing — ready' : m.blockedBy.join(', ');
	}

	function historicalField(field: Field, document: Plan): Field {
		const settled = new Set(
			Object.values(document.initiatives)
				.filter((initiative) => initiative.state === 'settled' || initiative.state === 'cancelled')
				.map((initiative) => initiative.spec.id)
		);
		const members = field.members.map((member) => {
			const initiative = document.initiatives[member.node.initiative_id];
			const state = initiative?.state ?? 'pending';
			const mapped: Member['state'] = state === 'settled' ? 'seated' : state === 'running' ? 'loaded' : state === 'failed' ? 'failed' : 'slack';
			return {
				...member,
				node: { ...member.node, state, ready: false },
				state: mapped,
				cancelled: state === 'cancelled',
				blockedBy: member.node.depends_on.filter((dependency) => !settled.has(dependency))
			};
		});
		const byId = new Map(members.map((member) => [member.node.initiative_id, member]));
		const lanes = field.lanes.map((lane) => lane.map((member) => byId.get(member.node.initiative_id)!));
		return {
			...field,
			members,
			byId,
			lanes,
			crossings: field.crossings.map(({ from, to }) => ({ from: byId.get(from.node.initiative_id)!, to: byId.get(to.node.initiative_id)! })),
			criticalPath: field.criticalPath.map((member) => byId.get(member.node.initiative_id)!).filter(Boolean)
		};
	}
</script>


{#if !plan.id}
	<!-- A plan is chosen from the plans that exist. `GET /fleet` is that list,
	     and the row carries what the choice is actually made on — the brief,
	     the revision, whether it is approved, how far it got. No id is typed
	     here; an id belongs in the address, not in a form. -->
	<section class="addressing">
		<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>Not addressed</span></p>
		{#if runs}
			<AsyncField resource={runs} reading="the fleet" onretry={() => void runs?.load()}>
				{#snippet children(fleet: Fleet)}
					{#if fleet.runs.length === 0}
						<p class="prose">
							No run exists yet. The daemon answered with an empty fleet, which is a
							project nothing has been planned in — not a failed read.
						</p>
						<p class="prose quiet">
							<code>uv run python ui/dev/seed_plan.py</code> writes a real Sprint 2 plan
							into the project's event store and prints its id; Dispatch (H4) is where a
							brief becomes a plan once that flow is built.
						</p>
					{:else}
						{@const selected = rowOf(fleet, chosen)}
						<p class="prose">
							{fleet.total_runs}
							{fleet.total_runs === 1 ? 'run is' : 'runs are'} on disk, newest first.
							Choose the one to supervise.
						</p>
						<form onsubmit={(event) => open(event, fleet)}>
							<label class="label" for="plan-choice">Plan</label>
							<div class="row">
								<select class="plate" id="plan-choice" bind:value={chosen}
									aria-describedby="plan-choice-help">
									<option value="">Choose a run…</option>
									{#each fleet.runs as run (run.plan_id)}
										<option value={run.plan_id}>
											{run.plan_id} · {run.brief.length > 64
												? run.brief.slice(0, 63) + '…'
												: run.brief}
										</option>
									{/each}
								</select>
								<button class="plate act" type="submit" disabled={!selected}>Open</button>
							</div>
						</form>
						{#if selected}
							<dl class="chosen plate">
								<div><dt class="label">Revision</dt><dd class="value">v{selected.version}</dd></div>
								<div>
									<dt class="label">Approval</dt>
									<dd class="value member"
										data-state={selected.approval === 'approved' ? 'seated' : 'slack'}>
										{APPROVAL[selected.approval] ?? selected.approval}
									</dd>
								</div>
								<div>
									<dt class="label">State</dt>
									<dd class="value member"
										data-state={selected.status === 'running' ? 'loaded' : 'balanced'}>
										{selected.status.replace('_', ' ')}
									</dd>
								</div>
								<div>
									<dt class="label">Settled</dt>
									<dd class="value">
										{selected.total === 0
											? '—'
											: `${Math.round(selected.progress * 100)}% of ${selected.total}`}
									</dd>
								</div>
							</dl>
						{:else}
							<p id="plan-choice-help" class="req">
								A run is required; there is nothing to open until one is chosen.
							</p>
						{/if}
					{/if}
					<!-- A daemon older than this build sends no `unreadable` at all; that is
					     absent, not empty, and it is the one field here worth guarding. -->
					{@const broken = fleet.unreadable ?? []}
					{#if broken.length > 0}
						<p class="prose quiet member" data-state="failed" role="status">
							{broken.join(', ')}
							{broken.length === 1 ? 'is' : 'are'} on disk and could not be folded, so
							{broken.length === 1 ? 'it is' : 'they are'} in none of the counts above and
							cannot be opened. That is a broken record, not an empty one.
						</p>
					{/if}
				{/snippet}
			</AsyncField>
		{/if}
	</section>
{:else if plan.resource}
	<AsyncField resource={plan.resource} reading="the plan projection" onretry={plan.reload}>
		{#snippet children(graph: PlanGraph)}
			<div class:historical-sheet={historical}>
			{#if graph.nodes.length === 0}
				<section>
					<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>No initiatives</span></p>
					<p class="prose">
						Plan <strong>{graph.plan_id}</strong> exists at revision {graph.version}, and
						its planner proposed no initiatives. There is no structure to draw.
					</p>
				</section>
			{:else}
				{@const baseField = buildField(graph)}
				{@const replayPlan = replayed?.data}
				{@const structureMatches = !historical || !replayPlan ||
					(replayPlan.version === graph.version && Object.keys(replayPlan.initiatives).length === graph.nodes.length &&
						graph.nodes.every((node) => replayPlan.initiatives[node.initiative_id] !== undefined))}
				{@const field = historical && replayPlan ? historicalField(baseField, replayPlan) : baseField}
				{@const contention = historical ? new Map() : contentionIndex(risk?.data ?? null)}
				{@const phase = phaseOf(graph)}
				{@const liveFoldPhase = runPhase(folded?.data)}
				{@const replayFoldPhase = runPhase(replayPlan)}
				{@const conflicts = risk?.data?.conflicts.length ?? null}
				{@const readyNow = graph.nodes.filter((n) => n.ready).length}
				{@const selected = selectedId ? (field.byId.get(selectedId) ?? null) : null}
				{@const order = field.members.map((m) => m.node.initiative_id)}
				{@const anchor = selected ? selected.node.initiative_id : order[0]}

				<p class="label rule-label">
					<span>Plan {graph.plan_id}</span><span class="rule"></span><span>{historical ? 'Settled' : PHASE[phase]}</span>
					{#if historical}
						<span class="member" data-state="slack">Historical</span>
					{:else if folded?.data && qualifies(liveFoldPhase)}
						<button class="act replay-entry" type="button" bind:this={replayEntry} onclick={enterReplay}>Replay this run</button>
					{:else}
						<span class="qualification">{qualificationSentence(liveFoldPhase)}</span>
					{/if}
				</p>
				{#if replayNotice}<p class="note prose" role="status">{replayNotice}</p>{/if}
				{#if historical && replayed?.data}
					{@const lastStopNotice = stopReading(stops, replayIndex, replayFoldPhase, liveFoldPhase)}
					{#if lastStopNotice}<p class="note prose" role="status">{lastStopNotice}</p>{/if}
				{/if}
				{#if historical && stops.length > 0}
					<ReplayBar stops={stops} index={replayIndex} historical={historical} onindex={moveReplay} onreturn={returnToLive} stale={replayed?.stale ?? false} />
				{/if}
				{#if historical && !replayed?.data}
					<p class="prose" aria-busy="true">Reading the historical plan projection. The live plan is not substituted for a missing record.</p>
				{/if}
				{#if !historical || replayed?.data}
				<dl class="readout plate">
					<div>
						<dt class="label">Lanes</dt>
						<dd class="value">{field.lanes.length}</dd>
						<p class="gloss">the most agents this plan can ever keep busy</p>
					</div>
					<div>
						<dt class="label">Critical path</dt>
						<dd class="value">{graph.critical_path.length || '—'}</dd>
						<p class="gloss">longest chain; structure, not a duration</p>
					</div>
					<div>
						<dt class="label">Ready now</dt>
						<dd class="value member" data-state="slack">{historical ? '—' : readyNow}</dd>
						<p class="gloss">
							{#if historical}readiness is a live computation and is not replayed{:else if phase === 'proposed'}nothing may start until the plan is approved{:else}pending, with every dependency settled{/if}
						</p>
					</div>
					<div>
						<dt class="label">Write conflicts</dt>
						<dd
							class="value member"
							data-state={conflicts === null ? 'slack' : conflicts > 0 ? 'failed' : 'seated'}
						>
							{historical ? '—' : conflicts ?? '—'}
						</dd>
						<p class="gloss">
							{#if historical}structure is available; live contention is not replayed{:else if conflicts === null}unread — the risk report did not answer{:else}pairs that may not run at the same time, though the lanes allow it{/if}
						</p>
					</div>
					<div>
						<dt class="label">Stream</dt>
						<dd class="value member" data-state="slack">
							{historical ? 'Held' : live === true ? 'Live' : live === false ? 'Dropped' : 'Connecting'}
						</dd>
						<p class="gloss">
							{#if historical}the event stream is closed while you are reading history; returning to live reopens it{:else if live === true}the daemon is pushing this plan’s events{:else if live === false}the stream closed; these values change only when re-read{:else}opening the event stream{/if}
						</p>
					</div>
					<div class="wide">
						<dt class="label">Recovery</dt>
						<dd class="value member" data-state={recovery?.data ? (recoveryHasProbe || !recovery.data.stale.length ? 'seated' : 'failed') : 'slack'}>
							{recovery?.data ? (recovery.data.stale.length || 'None') : '—'}
						</dd>
						<p class="gloss">{recovery?.data ? (recoveryHasProbe ? 'attempts this daemon started and no longer tracks; probe complete' : recovery.data.stale.length ? 'attempts this daemon started and no longer tracks; nothing has been probed yet' : 'every attempt on this plan is one this daemon is tracking') : 'unread — the recovery report did not answer. That is unknown, not none.'}</p>
					</div>
				</dl>

				<!-- R8's burn instruments: one plate under the structural readout, two
				     bare-button lists under it, nothing drawn on the field. The cap the
				     member is drawn against comes from the fold the page already holds. -->
				{#if !historical && status && ledger}
					<section class="burn">
						<BurnPlate {status} {ledger} planCap={folded?.data?.token_cap ?? null} />
					</section>
				{:else if historical}
					<p class="prose quiet replay-unavailable">Token and timing instruments are not replayed. They are served for the run as it stands, and reading them beside a past state would date them wrongly.</p>
				{/if}

				{#if !historical && phase === 'proposed'}
					<p class="note prose">
						This revision is proposed, not approved: every member is drawn as the planner
						laid it out and none of it has run.
						{#if !gateOpen}
							<button class="act" type="button" bind:this={gateTrigger} onclick={openGate}>Review and approve</button>
						{/if}
					</p>
				{:else if graph.approval === 'approved' && !gateOpen}
					<p class="note prose">Revision {graph.version} is approved. {graph.nodes.filter((node) => node.state === 'settled').length} of {graph.nodes.length} members have settled. <button class="act" type="button" bind:this={gateTrigger} onclick={openGate}>Review plan</button></p>
				{/if}
				{#if !historical && !field.agrees}
					<p class="note prose member" data-state="failed" role="alert">
						This build drew {field.lanes.length} lanes where the daemon computes a maximum
						concurrency of {graph.max_concurrency}. The lane count is supposed to be that
						number; treat the lanes as unreliable until they agree.
					</p>
				{/if}
				{#if !historical && risk && risk.phase === 'error' && risk.error}
					<p class="note prose member" data-state="slack" role="status">
						Contention is unread: {risk.error.message} The field below is drawn without its
						conflict and missing-edge cords — that is unknown, not none.
						<button class="act" type="button" onclick={() => void risk?.load()}>Read again</button>
					</p>
				{/if}

				{#if !historical && phase !== 'proposed' && recovery}
					<Recovery planId={graph.plan_id} resource={recovery} onretry={() => void recovery?.load()} onselect={select} />
				{/if}

				{#if !historical || structureMatches}
					<ContentionField
						{field}
						{contention}
						contentionRead={!historical && risk?.data != null}
						selected={selectedId}
						onselect={select}
					/>
				{:else}
					<p class="note prose">The plan's structure changed after this moment. The drawing shows the structure this run finished with, which is not the one that existed here, so it is not drawn against this bound. The members below are the record.</p>
				{/if}

				{#if !historical && ledger}
					<BurnAttribution {ledger} />
				{/if}
				{#if !historical && status?.data}
					<BurnLists bundle={status.data} selected={selectedId} onselect={select} />
				{/if}

				<Salvage plan={folded?.data ?? null} onchanged={() => {
					void folded?.load();
					void memoryStatus?.load();
				}} />

				<section class="reading">
					<p class="label rule-label">
						<span>Member</span><span class="rule"></span>
						<span>{selected ? selected.node.initiative_id : 'None selected'}</span>
					</p>
					{#if selected}
						{@const touches = contention.get(selected.node.initiative_id) ?? []}
						{@const node = risk?.data?.nodes.find((n) => n.initiative_id === selected.node.initiative_id)}
						<h2 class="member-name">{selected.node.name}</h2>
						<dl class="readout plate">
							<div>
								<dt class="label">State</dt>
								<dd class="value member" data-state={selected.state}>
									{selected.cancelled ? 'cancelled' : selected.node.state}
								</dd>
							</div>
							<div><dt class="label">Lane · rank</dt><dd class="value">{selected.lane + 1} · {selected.depth}</dd></div>
							<div>
								<dt class="label">Critical path</dt>
								<dd class="value">{selected.onCriticalPath ? 'On it' : 'Off it'}</dd>
							</div>
							<div>
								<dt class="label">Blocks downstream</dt>
								<dd class="value">{node ? node.blast_radius : '—'}</dd>
							</div>
							<div>
								<dt class="label">Assignment</dt>
								<dd class="value">{selected.node.harness} · {selected.node.model}</dd>
							</div>
							<div><dt class="label">Attempts</dt><dd class="value">{selected.node.attempts}</dd></div>
							<div class="wide">
								<dt class="label">Waiting on</dt>
								<dd>
									{#if selected.node.state !== 'pending'}
										Not waiting; this member is {selected.cancelled ? 'cancelled' : selected.node.state}.
									{:else if selected.node.ready}
										Nothing. Every dependency has settled, so this member is ready to run.
									{:else}
										{selected.blockedBy.join(', ')} — unsettled.
									{/if}
								</dd>
							</div>
							<div class="wide">
								<dt class="label">Contends with</dt>
								<dd>
									{#if !risk?.data}
										Unread. The risk report did not answer, so overlap is unknown.
									{:else if touches.length === 0}
										Nothing. No other initiative the plan lets run beside this one touches its paths.
									{:else}
										<ul class="touches">
											{#each touches as touch (touch.peer + touch.kind)}
												<li class="member" data-state={touch.kind === 'write_write' ? 'failed' : 'slack'}>
													<span class="peer">{touch.peer}</span>
													{#if touch.kind === 'write_write'}
														both write {touch.paths.join(', ')} — they may not run at the same time
													{:else}
														{touch.writes ? 'reads' : 'writes'} {touch.paths.join(', ')} that this one
														{touch.writes ? 'writes' : 'reads'}, with no dependency between them
													{/if}
												</li>
											{/each}
										</ul>
									{/if}
								</dd>
							</div>
						</dl>
					{:else if selectedId}
						<p class="prose">
							<strong>{selectedId}</strong> is not in revision {graph.version} of this plan.
							Select a member below to read one that is.
						</p>
					{:else}
						<p class="prose">
							Select a member — in the field above or the schedule below — to read its
							lane, what holds it, and what it contends with.
						</p>
					{/if}
				</section>

				<section class="schedule">
					<p class="label rule-label">
						<span>Load schedule</span><span class="rule"></span><span>{field.members.length} members</span>
					</p>
					<p class="prose quiet">
						Every member in the field, in the field's own order. Arrow keys move between rows.
					</p>
					<p class="prose quiet phone-note">
						This width drops the lane, rank and contention columns. Select a member to read
						all three.
					</p>
					<div class="tablewrap">
						<table>
							<caption class="sr">
								Initiatives in lane and rank order, with state, what each waits on, and what it contends with.
							</caption>
							<thead>
								<tr>
									<th scope="col">Member</th>
									<th scope="col" class="col-place">Lane</th>
									<th scope="col" class="col-place">Rank</th>
									<th scope="col">State</th>
									<th scope="col">Waits on</th>
									<th scope="col" class="col-contend">Contends with</th>
								</tr>
							</thead>
							<tbody>
								{#each field.members as m (m.node.initiative_id)}
									{@const touches = contention.get(m.node.initiative_id) ?? []}
									<tr aria-current={selectedId === m.node.initiative_id ? 'true' : undefined}>
										<th scope="row">
											<button
												id="row-{m.node.initiative_id}"
												type="button"
												class="pick"
												tabindex={m.node.initiative_id === anchor ? 0 : -1}
												onclick={() => select(m.node.initiative_id)}
												onkeydown={(event) => onScheduleKey(event, order)}
											>
												<span class="mark">{m.node.initiative_id}</span>
												<span class="who">{m.node.name}</span>
												{#if m.onCriticalPath}<span class="cp">critical path</span>{/if}
											</button>
										</th>
										<td class="col-place">{m.lane + 1}</td>
										<td class="col-place">{m.depth}</td>
										<td>
											<span class="member state" data-state={m.state}>
												{m.cancelled ? 'cancelled' : m.node.state}{#if m.node.state === 'pending' && m.node.ready}, ready{/if}
											</span>
										</td>
										<td>{waiting(m)}</td>
										<td class="col-contend">
											{#if !risk?.data}
												<span class="unread">unread</span>
											{:else if touches.length === 0}
												—
											{:else}
												{#each touches as touch (touch.peer + touch.kind)}
													<span class="member touch" data-state={touch.kind === 'write_write' ? 'failed' : 'slack'}>
														<span class="touch-peer">{touch.peer}</span>
														<span class="sr">
															{touch.kind === 'write_write' ? 'write conflict' : 'missing edge'} on
														</span>
														<span class="touch-path">{touch.paths.join(', ')}</span>
													</span>
												{/each}
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>

				{#if historical && replayed?.data}
					<ReplayRegister stops={stops} index={replayIndex} plan={replayed.data} onstop={moveReplay} onselect={select} />
				{/if}

				{#if !historical}<PlanGate
					open={gateOpen}
					planId={graph.plan_id}
					{graph}
					{field}
					{risk}
					plan={folded}
					revision={revision}
					reviews={reviews}
					covered={drawerId !== null}
					selected={selectedId}
					onselect={select}
					onclose={closeGate}
					onapproved={() => {
						plan.reload();
						void folded?.load();
						void revision?.load();
					}}
					onrevised={() => {
						plan.reload();
						void risk?.load();
						void folded?.load();
						void reviews?.load();
						void revision?.load();
					}}
				/>
				{/if}

				<InitiativeDrawer
					open={drawerId !== null}
					planId={graph.plan_id}
					id={drawerId}
					member={drawerId ? (field.byId.get(drawerId) ?? null) : null}
					plan={historical ? replayed : folded}
					historical={historical}
					{graph}
					report={reviews}
					approved={graph.approval === 'approved'}
					{memoryStatus}
					{kitchen}
					activity={drawerId ? activityFor(drawerId) : []}
					failure={drawerId ? (failures[drawerId] ?? null) : null}
					staleAttempt={drawerId ? (recovery?.data?.stale.find((attempt) => attempt.initiative_id === drawerId) ?? null) : null}
					{targetCheckpointId}
					{focusOnOpen}
					onrecovery={focusRecovery}
					ondecided={() => {
						/* A verdict can settle an initiative and release its
						   dependents, so it moves the field, the risk report and
						   the fold — not just the review it was sent to. */
						plan.reload();
						void risk?.load();
						void folded?.load();
						void reviews?.load();
						void recovery?.load();
					}}
					onclose={closeDrawer}
				/>
				{/if}
			{/if}
			</div>
		{/snippet}
	</AsyncField>
{/if}

<style>
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}

	/* --- readouts ----------------------------------------------------------- */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		flex: 1 1 11rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.75rem 1rem;
	}
	.readout .wide {
		flex-basis: 100%;
	}
	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		color: var(--member-ink, var(--ink));
	}
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.touches {
		margin: 0;
		padding: 0;
		list-style: none;
		color: var(--ink-2);
	}
	.touches li + li {
		margin-top: 0.35rem;
	}
	.peer {
		color: var(--member-ink);
		font-weight: 500;
	}

	.note {
		margin: 1.5rem 0 0;
		color: var(--member-ink, var(--ink-2));
	}
	.note[role='alert'],
	.note[role='status'] {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
	}

	.reading,
	.schedule {
		margin-top: 3rem;
	}
	.burn {
		margin-top: 2.75rem;
	}
	.member-name {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 70, 'wght' 620;
		font-weight: 620;
		text-transform: uppercase;
		letter-spacing: -0.01em;
		font-size: 2rem;
		line-height: 1;
		margin: 0 0 1.25rem;
		text-wrap: balance;
	}

	/* --- the schedule ------------------------------------------------------- */
	.tablewrap {
		margin-top: 1.25rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		table-layout: fixed;
		text-align: left;
	}
	thead th:nth-child(1) {
		width: 34%;
	}
	thead th:nth-child(2),
	thead th:nth-child(3) {
		width: 6%;
	}
	thead th:nth-child(4),
	thead th:nth-child(5) {
		width: 12%;
	}
	thead th:nth-child(6) {
		width: 30%;
	}
	th,
	td {
		padding: 0.55rem 0.75rem 0.55rem 0;
		border-bottom: 1px solid var(--rule);
		vertical-align: top;
		color: var(--ink-2);
	}
	thead th {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
		border-bottom: 1px solid var(--rule-strong);
	}
	tbody th {
		font-weight: 400;
		padding-left: 0;
	}
	tr[aria-current='true'] th,
	tr[aria-current='true'] td {
		background: var(--red-quiet);
	}
	.pick {
		font: inherit;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		text-align: left;
		background: none;
		border: 0;
		padding: 0;
		color: inherit;
		cursor: pointer;
	}
	.mark {
		font-weight: 500;
		color: var(--ink);
	}
	.who {
		color: var(--ink-2);
	}
	.pick:hover .mark,
	.pick:hover .who {
		color: var(--red);
	}
	.cp {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink);
		border-bottom: 2.5px solid var(--ink);
		align-self: flex-start;
		padding-bottom: 0.05rem;
		margin-top: 0.15rem;
	}
	.state {
		color: var(--member-ink);
	}
	/* Ash is a graphics value: it draws slack and never sets text (3.86:1 on
	   plate). A slack reading falls back to graphite and the dashed rule carries
	   the state; only seated (carbon) and failed (red) borrow the member ink. */
	dd.member[data-state='slack'],
	.state[data-state='slack'],
	.touch[data-state='slack'],
	.touches li[data-state='slack'],
	.touches li[data-state='slack'] .peer {
		color: var(--ink-2);
	}
	.touch[data-state='slack'],
	.touches li[data-state='slack'] .peer {
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}
	.touch {
		display: block;
		color: var(--member-ink);
	}
	.touch-peer {
		font-weight: 500;
		white-space: nowrap;
	}
	.touch-path {
		overflow-wrap: anywhere;
	}
	.touch + .touch {
		margin-top: 0.3rem;
	}
	.unread {
		color: var(--ink-2);
		border-bottom: 1px dashed var(--ash);
	}

	/* --- choosing a plan ----------------------------------------------------- */
	.addressing {
		max-width: 46rem;
	}
	form {
		margin: 1.5rem 0 0;
	}
	.row {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.4rem;
		max-width: 34rem;
	}
	/* The field geometry of every other control here; `.plate` carries the cut
	   and its fallback, so neither is restated. */
	select {
		--cut: 10px;
		font: inherit;
		flex: 1;
		min-width: 0;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	select:focus-visible {
		border-color: var(--red);
	}
	/* What the choice is made on, in the readout grid's own geometry: cells on
	   plate separated by a 1px gap that is the divider. */
	.chosen {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		margin: 1.25rem 0 0;
		max-width: 34rem;
		background: var(--rule);
		border: 1px solid var(--rule);
		overflow: hidden;
	}
	.chosen div {
		flex: 1 1 8rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.625rem 1rem;
	}
	.chosen dt {
		margin: 0;
	}
	.chosen dd {
		margin: 0.2rem 0 0;
	}
	.req {
		margin: 0.4rem 0 0;
		max-width: 34rem;
		font-size: 0.625rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.act {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink);
		background: transparent;
		border: 1px solid var(--rule-strong);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}
	.act:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}
	.act:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	.quiet {
		font-size: 0.8125rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}
	strong {
		color: var(--ink);
		font-weight: 500;
	}

	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	@media (max-width: 60rem) {
		.reading,
		.schedule {
			margin-top: 2.25rem;
		}
		th,
		td {
			padding-right: 0.5rem;
		}
		.gloss {
			display: none;
		}
		/* Six columns do not fit a narrow desktop. Lane and rank are drawn in the
		   field and named in the member readout, so they go first. A declared path
		   must also be allowed to break, or one long route pushes the whole table
		   past the sheet edge. */
		.col-place {
			display: none;
		}
		th,
		td {
			overflow-wrap: anywhere;
		}
	}

	/* A phone is a readable fallback, not a supervision surface. Four columns
	   still clip here, so contention moves to the member readout and the
	   schedule says so rather than dropping a real blocker in silence. */
	.phone-note {
		display: none;
	}
	.historical-sheet { border: 1px solid var(--rule-strong); padding: 0.75rem; }
	.qualification { color: var(--ink-2); font-size: 0.75rem; }
	.replay-entry { white-space: nowrap; }
	.replay-unavailable { margin-top: 1.25rem; }

	@media (max-width: 48rem) {
		.phone-note {
			display: block;
			margin-top: 0.5rem;
		}
		.col-contend {
			display: none;
		}
		thead th:nth-child(1) {
			width: 46%;
		}
	}
</style>
