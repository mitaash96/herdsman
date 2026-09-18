<!--
H1 — THE LOAD BANK

Home is the fleet drawn as a rack of members. Every run is one member spanning
the sheet, carrying exactly as much load as it has actually taken up: heavy
carbon where work settled, red where it is live, red and gapped where the load
path broke, a graphite run where it is held, and a dashed ash hairline for what
has not started. Nothing here is a card and nothing is a chart.

The composition is the same at two runs and at forty, which is the whole point:
a quiet fleet is two long members mostly bare, a busy one is a bank of them, and
in both cases the answer to "what is carrying load right now" is a silhouette
rather than a count you assemble by reading.

What this unit does NOT build, deliberately: the attention feed and its per-kind
actions (H2), the while-away digest (H3) and Dispatch (H4). Home counts what
needs the user and carries the daemon's own deep link to the oldest one; it
never lists them. A second attention surface here would be a second thing to
keep in step with the first.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { daemon, type Fleet, type RunRollup } from '$lib/daemon';
	import {
		SEGMENT_NAME,
		SEGMENT_WEIGHT,
		ago,
		blockedRuns,
		fleetMember,
		kindName,
		largestRun,
		memberShare,
		loadedShare,
		needsUser,
		segmentsOf,
		spendReading,
		statusOf
	} from '$lib/bank';

	/* Two reads, never one. Every aggregate on a Fleet — counts, running runs,
	   attention, spend — is summed over the runs it lists, so asking for both
	   halves at once would hand the active view archived totals. The archived
	   list is fetched the first time it is opened and not before. */
	const active = new Resource<Fleet>((signal) => daemon.fleet(signal));
	const archived = new Resource<Fleet>((signal) => daemon.fleetArchived(signal));

	let shown = $state<'active' | 'archived'>('active');
	const current = $derived(shown === 'archived' ? archived : active);

	/* The clock the relative times are read against. Held as state so a member
	   that last changed "4 min ago" becomes "5 min ago" on the next poll rather
	   than at the next navigation. */
	let now = $state(Date.now());

	/* There is no fleet-wide event stream: `GET /plans/{id}/events` is one
	   plan's. So the fleet is polled, and only while it is being looked at —
	   a supervision window left on a second screen should not keep a laptop
	   awake. A failed poll preserves the last values and marks them stale
	   through the Resource; it never blanks the bank. */
	const INTERVAL = 6000;
	$effect(() => {
		const resource = current;
		void resource.load();
		const read = () => {
			now = Date.now();
			if (!document.hidden) void resource.load();
		};
		const wake = () => {
			if (!document.hidden) read();
		};
		const timer = setInterval(read, INTERVAL);
		document.addEventListener('visibilitychange', wake);
		window.addEventListener('focus', wake);
		return () => {
			clearInterval(timer);
			document.removeEventListener('visibilitychange', wake);
			window.removeEventListener('focus', wake);
		};
	});

	/* --- the one authored motion --------------------------------------------
	   A member plays `take-up-load` when its load actually grows between two
	   reads, and at no other time. Playing it on every poll would make the
	   whole bank twitch six seconds apart while nothing had happened, which is
	   decoration; playing it when settled work arrives is the moment the motion
	   was authored for. Keyed by plan id, so a re-ordered list cannot fire it. */
	/* `lastLoad` is deliberately not `$state`: nothing renders it, and an effect
	   that both reads and writes one reactive value re-triggers itself on its
	   own write. This one did, and a saturated effect queue makes the page look
	   alive while no click ever lands. */
	const lastLoad = new Map<string, number>();
	let taking = $state<string[]>([]);
	$effect(() => {
		const view = current.data;
		if (!view) return;
		const grew: string[] = [];
		for (const run of view.runs) {
			const share = loadedShare(run.counts, run.total);
			const before = lastLoad.get(run.plan_id);
			if (before !== undefined && share > before) grew.push(run.plan_id);
			lastLoad.set(run.plan_id, share);
		}
		if (grew.length === 0) return;
		taking = grew;
		const done = setTimeout(() => {
			taking = [];
		}, 340);
		return () => clearTimeout(done);
	});

	/* --- archiving -----------------------------------------------------------
	   Arm, read the consequence, then confirm — R3's pattern, and the reason
	   this build keeps using it: a list of runs is exactly where a stray click
	   lands. Both the arm and its outcome are keyed on a plan id and reset on
	   nothing else. Keying either on the run's state would wipe the operator's
	   own confirmation on the re-read their write triggered, which is the bug
	   R3, R4 and R6 each found from a different side. */
	let armed = $state<string | null>(null);
	let reason = $state('');
	let sending = $state(false);
	let outcome = $state<{ planId: string; ok: boolean; message: string } | null>(null);
	/* A successful archive removes the row the Confirm button lived in, so focus
	   would fall to the document and the operator would land at the top of the
	   page. The outcome sentence takes it instead: it is what they pressed for. */
	let outcomeEl = $state<HTMLParagraphElement | null>(null);

	function arm(planId: string) {
		armed = planId;
		reason = '';
		outcome = null;
	}

	async function commit(run: RunRollup) {
		if (sending) return;
		sending = true;
		const wasArchived = run.archived;
		try {
			const call = wasArchived ? daemon.unarchive : daemon.archive;
			await call(run.plan_id, reason, `${wasArchived ? 'unarchive' : 'archive'}:${run.plan_id}`);
			outcome = {
				planId: run.plan_id,
				ok: true,
				message: wasArchived
					? `${run.plan_id} is back in the active fleet.`
					: `${run.plan_id} is archived and is out of active navigation.`
			};
			armed = null;
			await tick();
			outcomeEl?.focus();
			/* Both lists moved: the run left one and joined the other. Re-read
			   whichever has already been read, so neither can go on showing a
			   run that is no longer in it. */
			void active.load();
			if (archived.hasData) void archived.load();
		} catch (cause) {
			outcome = {
				planId: run.plan_id,
				ok: false,
				message: cause instanceof Error ? cause.message : 'The write failed.'
			};
		} finally {
			sending = false;
		}
	}

	const headline = (view: Fleet | null, phase: string, stale: boolean): string => {
		if (phase === 'error') return 'Not answering';
		if (!view) return 'Reading';
		if (stale) return 'Stale';
		if (view.runs.length === 0) return shown === 'archived' ? 'None archived' : 'No runs';
		return `${view.runs.length} ${view.runs.length === 1 ? 'run' : 'runs'}`;
	};
</script>

<section class="home">
	<p class="label rule-label">
		<span>Fleet</span>
		<span class="rule"></span>
		<span
			class="member"
			data-state={current.phase === 'error'
				? 'failed'
				: current.stale || !current.data
					? 'slack'
					: 'seated'}
		>
			{headline(current.data, current.phase, current.stale)}
		</span>
	</p>

	{#if outcome}
		<p
			bind:this={outcomeEl}
			class="outcome member"
			data-state={outcome.ok ? 'seated' : 'failed'}
			role="status"
			tabindex="-1"
		>
			<span class="label">{outcome.ok ? 'Done' : 'Not done'}</span>
			<span>{outcome.message}</span>
		</p>
	{/if}

	{#if active.data && (active.data.runs.length > 0 || active.data.archived > 0)}
		{@const counted = active.data}
		<!-- Navigation between two lists, not a filter over one: each list is its
		     own read with its own totals. -->
		<div class="switch" role="group" aria-label="Which runs to list">
			<button
				type="button"
				class="plate tab"
				aria-pressed={shown === 'active'}
				onclick={() => (shown = 'active')}
			>
				Active <span class="n">{counted.total_runs}</span>
			</button>
			<button
				type="button"
				class="plate tab"
				aria-pressed={shown === 'archived'}
				onclick={() => (shown = 'archived')}
			>
				Archived <span class="n">{counted.archived}</span>
			</button>
		</div>
	{/if}

	<AsyncField
		resource={current}
		reading={shown === 'archived' ? 'the archived runs' : 'the fleet'}
		onretry={() => void current.load()}
	>
		{#snippet children(view: Fleet)}
			{#if view.runs.length === 0}
				{#if shown === 'archived'}
					<p class="prose">
						No run has been archived. Archiving takes a finished or abandoned run out of
						this navigation without touching its record; nothing has been taken out yet.
					</p>
				{:else}
					<p class="prose">
						No run exists yet. The daemon answered with an empty fleet, which is a project
						nothing has been planned in — not a failed read.
					</p>
					<p class="prose quiet">
						<code>uv run python ui/dev/seed_plan.py</code> writes a real plan into the
						project's event store and prints its id. Turning a brief into a plan from here
						is the Dispatch flow, which is not built yet.
					</p>
				{/if}
			{:else}
				{@const spend = spendReading(view.spend)}
				{@const blocked = blockedRuns(view)}
				{@const bank = fleetMember(view)}
				{@const largest = largestRun(view)}

				<dl class="readout plate">
					<div>
						<dt class="label">Runs</dt>
						<dd class="value">{view.total_runs}</dd>
						<p class="gloss">
							{shown === 'archived' ? 'out of active navigation' : 'listed here, newest first'}
						</p>
					</div>
					<div>
						<dt class="label">Running</dt>
						<dd class="value member" data-state={view.running_runs > 0 ? 'loaded' : 'balanced'}>
							{view.running_runs}
						</dd>
						<p class="gloss">runs with an initiative under load right now</p>
					</div>
					<div>
						<dt class="label">Needs you</dt>
						<dd
							class="value member"
							data-state={blocked.unknown ? 'slack' : blocked.items > 0 ? 'loaded' : 'seated'}
						>
							{blocked.unknown ? '—' : blocked.items}
						</dd>
						<p class="gloss">
							{#if blocked.unknown}
								this daemon does not project attention
							{:else if blocked.items === 0}
								nothing is waiting on you{#if blocked.silent > 0}, in the {view.total_runs -
										blocked.silent} that reported{/if}
							{:else}
								across {blocked.runs}
								{blocked.runs === 1 ? 'run' : 'runs'} — each is cleared in Run
							{/if}
							{#if blocked.silent > 0 && !blocked.unknown}
								· {blocked.silent}
								{blocked.silent === 1 ? 'run' : 'runs'} reported no attention and {blocked.silent ===
								1
									? 'is'
									: 'are'} not in this figure
							{/if}
						</p>
					</div>
					<div>
						<dt class="label">Spend</dt>
						<dd class="value member" data-state={spend.value === null ? 'slack' : 'seated'}>
							{spend.value ?? '—'}
						</dd>
						<p class="gloss">{spend.gloss}</p>
					</div>
					{#if spend.available !== null}
						<div>
							<dt class="label">
								Available in {view.spend?.capped_runs}
								{view.spend?.capped_runs === 1 ? 'run' : 'runs'}
							</dt>
							<dd class="value">{spend.available}</dd>
							<p class="gloss">
								this covers only the runs that declared a cap, not the fleet's whole spend
							</p>
						</div>
					{/if}
				</dl>

				{#if bank}
					<!-- The fleet's own member: the same drawing, one level up. It is
					     drawn only above one run, because at one run it would be the
					     same member twice on one screen. -->
					<div class="whole">
						<p class="label rule-label">
							<span>All work</span>
							<span class="rule"></span>
							<span>{bank.total} initiatives</span>
						</p>
						<div class="run-member" aria-hidden="true">
							<span class="span">
								{#each bank.segments as segment (segment.state)}
									<span
										class="seg"
										data-state={segment.state}
										data-weight={SEGMENT_WEIGHT[segment.state]}
										style="flex-grow: {segment.share}"
									></span>
								{/each}
							</span>
							<span class="tail"></span>
						</div>
						<p class="dim">
							{#each bank.segments as segment, index (segment.state)}
								{#if index > 0}<span class="sep" aria-hidden="true"></span>{/if}<span
									class="pair"
									><span class="k">{SEGMENT_NAME[segment.state]}</span><span
										class="v member"
										data-state={segment.state === 'running'
											? 'loaded'
											: segment.state === 'failed'
												? 'failed'
												: segment.state === 'settled'
													? 'seated'
													: 'slack'}>{segment.count}</span
									></span
								>
							{/each}
						</p>
					</div>
				{/if}

				<ol class="bank">
					{#each view.runs as run (run.plan_id)}
						{@const status = statusOf(run.status)}
						{@const segments = segmentsOf(run.counts, run.total)}
						{@const attention = needsUser(run)}
						{@const runSpend = spendReading(run.spend)}
						<li class="entry">
							<p class="label rule-label entry-head">
								<a class="run-id" href={run.link.path}>{run.plan_id}</a>
								<span class="rule"></span>
								<span class="member status" data-state={status.state}>{status.word}</span>
							</p>

							<p class="brief">{run.brief}</p>

							{#if segments.length > 0}
								<div
									class="run-member"
									class:taking={taking.includes(run.plan_id)}
									style="--span: {memberShare(run.total, largest)}"
									aria-hidden="true"
								>
									<span class="span">
										{#each segments as segment (segment.state)}
											<span
												class="seg"
												data-state={segment.state}
												data-weight={SEGMENT_WEIGHT[segment.state]}
												style="flex-grow: {segment.share}"
											></span>
										{/each}
									</span>
									<span class="tail"></span>
								</div>
							{/if}

							<p class="dim">
								<span class="pair">
									<span class="k">settled</span>
									<span class="v">
										{run.total === 0 ? '—' : `${run.counts.settled ?? 0}/${run.total}`}
									</span>
								</span>
								{#each segments.filter((s) => s.state !== 'settled' && s.state !== 'pending') as segment (segment.state)}
									<span class="sep" aria-hidden="true"></span>
									<span class="pair">
										<span class="k">{SEGMENT_NAME[segment.state]}</span>
										<span
											class="v member"
											data-state={segment.state === 'running'
												? 'loaded'
												: segment.state === 'failed'
													? 'failed'
													: 'slack'}>{segment.count}</span
										>
									</span>
								{/each}

								<span class="sep" aria-hidden="true"></span>
								<span class="pair">
									<span class="k">needs you</span>
									{#if attention.unknown}
										<span class="v member" data-state="slack">—</span>
									{:else if attention.count === 0}
										<span class="v">0</span>
									{:else if attention.oldest}
										<!-- One precise link, not a feed: the daemon's own deep link to
										     the oldest blocker, which addresses the initiative or the
										     checkpoint and not just the plan. -->
										<a class="v member need" data-state="loaded" href={attention.oldest.link.path}>
											{attention.count}
											<span class="sr">
												— oldest is {kindName(attention.oldest.kind)}; open it in Run
											</span>
										</a>
									{/if}
								</span>

								<span class="sep" aria-hidden="true"></span>
								<span class="pair">
									<span class="k">spend</span>
									<span class="v member" data-state={runSpend.value === null ? 'slack' : 'seated'}>
										{runSpend.value ?? '—'}
									</span>
								</span>

								{#if runSpend.available !== null}
									<span class="sep" aria-hidden="true"></span>
									<span class="pair">
										<span class="k">available</span>
										<span class="v">{runSpend.available}</span>
									</span>
								{/if}

								<span class="sep" aria-hidden="true"></span>
								<span class="pair">
									<span class="k">revision</span>
									<span class="v">v{run.version}</span>
								</span>
								<span class="sep" aria-hidden="true"></span>
								<span class="pair">
									<span class="k">changed</span>
									<span class="v">{ago(run.updated_at, now)}</span>
								</span>
								<span class="sep" aria-hidden="true"></span>
								<button class="rowact" type="button" onclick={() => arm(run.plan_id)}>
									{run.archived ? 'Return to active' : 'Archive'}
								</button>
							</p>

							{#if armed === run.plan_id}
								<div class="arm plate">
									<p class="prose">
										{#if run.archived}
											Returning {run.plan_id} puts it back in active navigation. It changes no
											work either way.
										{:else}
											Archiving {run.plan_id} takes it out of active navigation and nothing else:
											its record is kept, any running initiative keeps running, and you can
											return it at any time.
										{/if}
									</p>
									<label class="label" for="reason-{run.plan_id}">Reason (optional)</label>
									<input
										class="plate"
										id="reason-{run.plan_id}"
										type="text"
										bind:value={reason}
										placeholder="Why this run is being set aside"
									/>
									<div class="acts">
										<button
											class="plate act"
											type="button"
											disabled={sending}
											onclick={() => void commit(run)}
										>
											{sending ? 'Writing…' : run.archived ? 'Confirm return' : 'Confirm archive'}
										</button>
										<button class="plate act" type="button" onclick={() => (armed = null)}>
											Cancel
										</button>
									</div>
								</div>
							{/if}
						</li>
					{/each}
				</ol>
			{/if}

			<!-- A daemon older than this build sends no `unreadable`; that is absent,
			     not empty. A run that cannot be read is not a run that is not there. -->
			{@const broken = view.unreadable ?? []}
			{#if broken.length > 0}
				<p class="prose quiet member" data-state="failed" role="status">
					{broken.join(', ')}
					{broken.length === 1 ? 'is' : 'are'} on disk and could not be folded, so
					{broken.length === 1 ? 'it is' : 'they are'} in none of the figures above and cannot
					be opened. That is a broken record, not an empty one.
				</p>
			{/if}
		{/snippet}
	</AsyncField>
</section>

<style>
	.home {
		max-width: 74rem;
	}

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

	/* --- the outcome of a write --------------------------------------------- */
	.outcome {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 0 0 1.5rem;
		color: var(--member-ink);
	}
	.outcome .label {
		color: var(--member-ink);
	}

	/* --- active / archived --------------------------------------------------- */
	.switch {
		display: flex;
		gap: 0.5rem;
		margin: 0 0 1.75rem;
	}
	.tab {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-2);
		background: transparent;
		border: 1px solid var(--rule);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}
	.tab:hover {
		border-color: var(--red);
		color: var(--red);
	}
	/* Which list you are reading is location, not load, so it is carbon and a
	   harder edge — never red. */
	.tab[aria-pressed='true'] {
		color: var(--ink);
		border-color: var(--rule-strong);
		box-shadow: 0 0 0 3px var(--plate), 0 0 0 4px var(--member-line);
	}
	.tab .n {
		color: var(--ink-2);
		letter-spacing: 0;
	}
	.tab[aria-pressed='true'] .n,
	.tab:hover .n {
		color: inherit;
	}

	/* --- readouts ------------------------------------------------------------ */
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

	/* --- the member ----------------------------------------------------------
	   Weight is the load: 2.5px is a member under load (the weight the critical
	   run is drawn at in the Contention Field), 1.25px is work in place but not
	   loaded, 1px is work carrying nothing. Colour agrees with the weight and
	   never carries the state alone. The tail is the Member-Runs-Through Rule:
	   the structure overshoots the last segment rather than stopping at it. */
	.run-member {
		display: flex;
		align-items: center;
		height: 0.75rem;
		margin: 0.75rem 0 0.6rem;
	}
	/* Every member is drawn on one fleet-wide unit: `--span` is this run's
	   initiative count measured against the largest listed run, so equal counts
	   draw equal lengths and the red across the bank compares in one pass.
	   Drawing every member full width made length mean proportion within its
	   own run, which is the count you have to assemble by reading. The fleet's
	   own member is that unit and sets no span, so it stays whole. The tail is
	   outside the span, which is what keeps a short member overshooting its
	   last segment rather than stopping at it. */
	.span {
		display: flex;
		align-items: center;
		flex: none;
		min-width: 0;
		width: calc(var(--span, 1) * (100% - 0.75rem));
	}
	.seg {
		flex-basis: 0;
		min-width: 2px;
		transform-origin: left center;
	}
	.seg[data-weight='load'] {
		height: 2.5px;
	}
	.seg[data-weight='held'] {
		height: 1.25px;
	}
	.seg[data-weight='none'] {
		height: 1px;
	}
	.seg[data-state='settled'] {
		background: var(--seat);
	}
	.seg[data-state='running'] {
		background: var(--red);
	}
	/* The load path is discontinuous: the 1/4 gap of the failed member state. */
	.seg[data-state='failed'] {
		background: repeating-linear-gradient(
			90deg,
			var(--red) 0 1px,
			transparent 1px 5px
		);
	}
	.seg[data-state='paused'] {
		background: var(--ink-2);
	}
	/* Slack: ash, dashed 3/3. Ash draws here and never sets type. */
	.seg[data-state='pending'] {
		background: repeating-linear-gradient(
			90deg,
			var(--ash) 0 3px,
			transparent 3px 6px
		);
	}
	/* Closed out: present, and it will never carry load. Solid, so it cannot be
	   mistaken for work that has not started. */
	.seg[data-state='cancelled'] {
		background: var(--ash);
	}
	.tail {
		flex: none;
		width: 0.75rem;
		height: 1px;
		background: var(--member-line);
	}
	/* The one authored motion, and only when the load really grew. */
	.run-member.taking .seg[data-weight='load'] {
		animation: take-up-load 320ms cubic-bezier(0.16, 1, 0.3, 1);
	}

	.whole {
		margin: 2rem 0 0.5rem;
	}
	.whole .label {
		margin: 0;
	}

	/* --- the dimension string under a member --------------------------------- */
	.dim {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.3rem 0.65rem;
		margin: 0;
	}
	.pair {
		display: inline-flex;
		align-items: baseline;
		gap: 0.4rem;
		white-space: nowrap;
	}
	.k {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.v {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--ink);
	}
	.sep {
		flex: none;
		width: 1px;
		height: 0.9rem;
		align-self: center;
		background: var(--rule);
	}

	/* --- the bank ------------------------------------------------------------ */
	.bank {
		list-style: none;
		margin: 2.25rem 0 0;
		padding: 0;
	}
	.entry {
		padding: 1.5rem 0 1.25rem;
		border-top: 1px solid var(--rule);
	}
	.entry-head {
		margin: 0;
	}
	.run-id {
		font-size: 0.8125rem;
		font-weight: 500;
		letter-spacing: 0;
		text-transform: none;
		color: var(--ink);
		overflow-wrap: anywhere;
	}
	.run-id {
		text-decoration-color: var(--rule-strong);
	}
	.run-id:hover {
		color: var(--red);
		text-decoration-color: var(--red);
	}
	.status {
		color: var(--member-ink);
	}
	.brief {
		max-width: 68ch;
		margin: 0;
		color: var(--ink-2);
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
	}

	/* Ash is a graphics value: it draws slack and never sets text. A slack
	   reading falls back to graphite and carries the state as a dashed rule. */
	.member[data-state='slack'],
	.v.member[data-state='slack'],
	.value.member[data-state='slack'],
	.status[data-state='slack'] {
		color: var(--ink-2);
	}
	.v.member[data-state='slack'],
	.value.member[data-state='slack'],
	.status[data-state='slack'] {
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}
	.rowact {
		font: inherit;
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
		background: none;
		border: 0;
		padding: 0;
		cursor: pointer;
		white-space: nowrap;
	}
	.rowact:hover {
		color: var(--red);
	}
	.need {
		color: var(--red);
		text-decoration: none;
	}
	.need:hover {
		text-decoration: underline;
		text-decoration-color: var(--red);
		text-underline-offset: 0.25em;
	}

	/* --- arming a write ------------------------------------------------------ */
	.arm {
		--cut: 12px;
		margin: 1rem 0 0;
		max-width: 46rem;
		padding: 1rem 1.25rem 1.25rem;
		background: var(--plate);
		border: 1px solid var(--rule);
	}
	.arm .prose {
		margin: 0 0 0.875rem;
	}
	.arm .label {
		display: block;
		margin-bottom: 0.3rem;
	}
	input {
		--cut: 10px;
		font: inherit;
		display: block;
		width: 100%;
		max-width: 34rem;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	input:focus-visible {
		border-color: var(--red);
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 1rem;
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
		margin-top: 1.5rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
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
		.entry {
			padding: 1.25rem 0 1rem;
		}
		.dim {
			gap: 0.3rem 0.5rem;
		}
	}
</style>
