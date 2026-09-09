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
-->
<script lang="ts">
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import AsyncField from '$lib/AsyncField.svelte';
	import ContentionField from '$lib/ContentionField.svelte';
	import InitiativeDrawer from '$lib/InitiativeDrawer.svelte';
	import PlanGate from '$lib/PlanGate.svelte';
	import { Resource } from '$lib/resource.svelte';
	import {
		daemon,
		type CheckpointReport,
		type InitiativeFailedFrame,
		type Plan,
		type PlanGraph,
		type RiskReport,
		type RuntimeObservedFrame
	} from '$lib/daemon';
	import { buildField, contentionIndex, phaseOf, step, type Member } from '$lib/field';

	const plan = getContext<{
		readonly resource: Resource<PlanGraph> | null;
		readonly id: string | null;
		reload: () => void;
	}>('plan');

	let entered = $state('');
	const ready = $derived(entered.trim().length > 0);

	function address(event: SubmitEvent) {
		event.preventDefault();
		const id = entered.trim();
		if (id) void goto(`/run?plan=${encodeURIComponent(id)}`);
	}

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
	let requested = $state<string | null>(null);
	$effect(() => {
		const id = plan.id;
		if (id === requested) return;
		requested = id;
		risk?.dispose();
		folded?.dispose();
		reviews?.dispose();
		activity = [];
		failures = {};
		drawerId = null;
		if (!id) {
			risk = null;
			folded = null;
			reviews = null;
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
	});

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
		if (!id) return;
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
				}, 120);
			},
			(connected) => {
				live = connected;
				if (!connected) {
					plan.resource?.markStale();
					risk?.markStale();
					folded?.markStale();
					reviews?.markStale();
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
	const select = (id: string) => {
		selectedId = id;
		drawerId = id;
	};

	/* The drawer expands on selection but holds its own id rather than reading
	   the selection, so a live re-read that drops the initiative leaves it open
	   and says so, and closing it does not clear what you have selected.
	   Re-selecting the same member expands it again. */
	let drawerId = $state<string | null>(null);
	const closeDrawer = () => (drawerId = null);

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
</script>

{#if !plan.id}
	<!-- Unavailable action, stated as such: there is no plan picker because the
	     daemon exposes no collection route to build one from. -->
	<section class="addressing">
		<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>Not addressed</span></p>
		<p class="prose">
			A plan is addressed by id. The daemon has no <code>GET /plans</code> route, so this
			build cannot list the plans on disk and offer you a picker; it can only open the one
			you name. Home (H1) is where a real plan index belongs, once that route exists.
		</p>
		<form onsubmit={address}>
			<label class="label" for="plan-id">Plan id</label>
			<div class="row">
				<input class="plate" id="plan-id" bind:value={entered} spellcheck="false"
					autocomplete="off" aria-describedby="plan-id-help" />
				<button class="plate act" type="submit" disabled={!ready}>Open</button>
			</div>
		</form>
		<p id="plan-id-help" class="req">
			A plan id is required; there is nothing to open without one.
		</p>
		<p class="prose quiet">
			No plan on disk yet? <code>uv run python ui/dev/seed_plan.py</code> writes a real
			Sprint 2 plan into the project's event store and prints its id.
		</p>
	</section>
{:else if plan.resource}
	<AsyncField resource={plan.resource} reading="the plan projection" onretry={plan.reload}>
		{#snippet children(graph: PlanGraph)}
			{#if graph.nodes.length === 0}
				<section>
					<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>No initiatives</span></p>
					<p class="prose">
						Plan <strong>{graph.plan_id}</strong> exists at revision {graph.version}, and
						its planner proposed no initiatives. There is no structure to draw.
					</p>
				</section>
			{:else}
				{@const field = buildField(graph)}
				{@const contention = contentionIndex(risk?.data ?? null)}
				{@const phase = phaseOf(graph)}
				{@const conflicts = risk?.data?.conflicts.length ?? null}
				{@const readyNow = graph.nodes.filter((n) => n.ready).length}
				{@const selected = selectedId ? (field.byId.get(selectedId) ?? null) : null}
				{@const order = field.members.map((m) => m.node.initiative_id)}
				{@const anchor = selected ? selected.node.initiative_id : order[0]}

				<p class="label rule-label">
					<span>Plan {graph.plan_id}</span><span class="rule"></span><span>{PHASE[phase]}</span>
				</p>

				<dl class="readout plate">
					<div>
						<dt class="label">Lanes</dt>
						<dd class="value">{field.lanes.length}</dd>
						<p class="gloss">the most agents this plan can ever keep busy</p>
					</div>
					<div>
						<dt class="label">Critical path</dt>
						<dd class="value">{graph.critical_path.length || '—'}</dd>
						<p class="gloss">longest chain; its floor on wall-clock time</p>
					</div>
					<div>
						<dt class="label">Ready now</dt>
						<dd class="value member" data-state={readyNow > 0 ? 'balanced' : 'slack'}>{readyNow}</dd>
						<p class="gloss">
							{#if phase === 'proposed'}nothing may start until the plan is approved{:else}pending, with every dependency settled{/if}
						</p>
					</div>
					<div>
						<dt class="label">Write conflicts</dt>
						<dd
							class="value member"
							data-state={conflicts === null ? 'slack' : conflicts > 0 ? 'failed' : 'seated'}
						>
							{conflicts ?? '—'}
						</dd>
						<p class="gloss">
							{#if conflicts === null}unread — the risk report did not answer{:else}pairs that may not run at the same time, though the lanes allow it{/if}
						</p>
					</div>
					<div>
						<dt class="label">Stream</dt>
						<dd class="value member" data-state={live === true ? 'seated' : live === false ? 'failed' : 'slack'}>
							{live === true ? 'Live' : live === false ? 'Dropped' : 'Connecting'}
						</dd>
						<p class="gloss">
							{#if live === true}the daemon is pushing this plan’s events{:else if live === false}the stream closed; these values change only when re-read{:else}opening the event stream{/if}
						</p>
					</div>
				</dl>

				{#if phase === 'proposed'}
					<p class="note prose">
						This revision is proposed, not approved: every member is drawn as the planner
						laid it out and none of it has run.
						{#if !gateOpen}
							<button class="act" type="button" bind:this={gateTrigger} onclick={openGate}>
								Review and approve
							</button>
						{/if}
					</p>
				{/if}
				{#if !field.agrees}
					<p class="note prose member" data-state="failed" role="alert">
						This build drew {field.lanes.length} lanes where the daemon computes a maximum
						concurrency of {graph.max_concurrency}. The lane count is supposed to be that
						number; treat the lanes as unreliable until they agree.
					</p>
				{/if}
				{#if risk && risk.phase === 'error' && risk.error}
					<p class="note prose member" data-state="slack" role="status">
						Contention is unread: {risk.error.message} The field below is drawn without its
						conflict and missing-edge cords — that is unknown, not none.
						<button class="act" type="button" onclick={() => void risk?.load()}>Read again</button>
					</p>
				{/if}

				<ContentionField
					{field}
					{contention}
					contentionRead={risk?.data != null}
					selected={selectedId}
					onselect={select}
				/>

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

				<PlanGate
					open={gateOpen}
					planId={graph.plan_id}
					{graph}
					{field}
					{risk}
					plan={folded}
					covered={drawerId !== null}
					selected={selectedId}
					onselect={select}
					onclose={closeGate}
					onapproved={() => {
						plan.reload();
						void folded?.load();
					}}
				/>

				<InitiativeDrawer
					open={drawerId !== null}
					planId={graph.plan_id}
					id={drawerId}
					member={drawerId ? (field.byId.get(drawerId) ?? null) : null}
					plan={folded}
					{graph}
					report={reviews}
					approved={graph.approval === 'approved'}
					activity={drawerId ? activityFor(drawerId) : []}
					failure={drawerId ? (failures[drawerId] ?? null) : null}
					ondecided={() => {
						/* A verdict can settle an initiative and release its
						   dependents, so it moves the field, the risk report and
						   the fold — not just the review it was sent to. */
						plan.reload();
						void risk?.load();
						void folded?.load();
						void reviews?.load();
					}}
					onclose={closeDrawer}
				/>
			{/if}
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

	/* --- the addressing form ------------------------------------------------ */
	.addressing {
		max-width: 46rem;
	}
	form {
		margin: 2rem 0 2.25rem;
	}
	.row {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.4rem;
		max-width: 28rem;
	}
	input {
		--cut: 10px;
		font: inherit;
		flex: 1;
		min-width: 0;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	input:focus-visible {
		border-color: var(--red);
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
	.req {
		margin: 0.4rem 0 0;
		max-width: 28rem;
		font-size: 0.625rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--ink-2);
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
