<!--
K1 — THE RIG ELEVATION

Kitchen is the local machine drawn once, as an elevation. Every declared harness
is a column standing on one base line — the project — and its height is exactly
how far one bounded `--version` probe actually carried it: declared, found,
answered, versioned. A column that stops two courses short is visibly short
against the ghost of the courses it did not reach, so "can this machine run my
agents right now" is a silhouette rather than a table to read.

The one distinction this surface exists to hold is geometric, not editorial:
height is observed, seats are declared. Nothing in the daemon checks a declared
capability, so a claim never raises a column, and a column never implies a claim.

What this unit does NOT build, deliberately: adapter forms and smoke tests (K2),
the model catalog, assignment defaults and fallbacks (K3). Discovery here is
read-only over global configuration and writes nothing anywhere.
-->
<script lang="ts">
	import AsyncField from '$lib/AsyncField.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { daemon, DaemonError, type Kitchen } from '$lib/daemon';
	import {
		COURSES,
		EXAMPLE_DECLARATION,
		columnsOf,
		memberState,
		rigReading,
		type Column
	} from '$lib/kitchen';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));

	/* The courses are drawn bottom-up and annotated top-down, on one rhythm the
	   ladder and the columns both measure from. */
	const BAND = 52;
	const BASE = 236;
	const ladder = [...COURSES].reverse();

	let selected = $state<string | null>(null);
	let probing = $state(false);
	let probedAt = $state<Date | null>(null);
	let outcome = $state<{ ok: boolean; message: string } | null>(null);
	/* `POST /kitchen/discovery` is Sprint 8's; a daemon that predates it answers
	   404 and the control becomes an unavailable action rather than a dead
	   button that fails the same way every time it is pressed. */
	let probeRoute = $state<'present' | 'absent'>('present');
	let outcomeEl = $state<HTMLParagraphElement | null>(null);
	/* The strip is measured rather than assumed: a fade that is always on lies
	   about scrollable content when three columns fit, and one that is never on
	   cuts the last harness off mid-word at 390. */
	let strip = $state<HTMLDivElement | null>(null);
	let scrollable = $state(false);

	const columns = $derived(kitchen.data ? columnsOf(kitchen.data) : []);
	const rig = $derived(rigReading(columns));
	const at = $derived(Math.max(columns.findIndex((c) => c.harness === selected), 0));
	const current = $derived(columns[at] ?? null);

	/* The columns are a tab strip over one reading: arrow keys move along the
	   elevation and carry focus with the selection, so the drawing is navigable
	   without a pointer and the panel is announced as the thing it belongs to. */
	function onKeys(event: KeyboardEvent): void {
		const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
		if (!keys.includes(event.key) || columns.length === 0) return;
		event.preventDefault();
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? columns.length - 1
					: (at + (event.key === 'ArrowRight' ? 1 : columns.length - 1)) % columns.length;
		selected = columns[next].harness;
		const strip = (event.currentTarget as HTMLElement).parentElement;
		(strip?.querySelectorAll('[role="tab"]')[next] as HTMLElement | undefined)?.focus();
	}

	/* Selection survives every re-read: it is keyed on a harness name, never on
	   a position or a state, so a probe that changes what a column says cannot
	   move the operator to a different column while they are reading it. */
	$effect(() => {
		if (columns.length === 0) return;
		if (selected !== null && columns.some((c) => c.harness === selected)) return;
		selected = columns[0].harness;
	});

	/* One probe on first open, and only when the daemon holds no facts at all:
	   discovery lives in daemon memory, so a daemon that has just started
	   reports every harness `unknown` until something looks. Not a poll — each
	   probe starts a real process per declared harness — so it happens once
	   here and afterwards only when the operator asks. */
	let opened = false;
	$effect(() => {
		const view = kitchen.data;
		if (!view || opened) return;
		opened = true;
		if (view.configured && view.discovery.facts.length === 0) void probe();
	});

	$effect(() => {
		void kitchen.load();
		return () => kitchen.dispose();
	});

	async function probe(): Promise<void> {
		if (probing) return;
		probing = true;
		outcome = null;
		try {
			const measuredView = await daemon.probeKitchen();
			probedAt = new Date();
			const rigNow = rigReading(columnsOf(measuredView));
			outcome = {
				ok: true,
				message: `${rigNow.declared} ${rigNow.declared === 1 ? 'harness' : 'harnesses'} measured: ${rigNow.ready} ready, ${rigNow.unavailable} unavailable${rigNow.other > 0 ? `, ${rigNow.other} neither` : ''}.`
			};
			/* The probe answers with the whole projection; this build re-reads it
			   through the one Resource instead of holding a second copy, so there
			   is exactly one thing on screen that can be stale. */
			await kitchen.load();
		} catch (cause) {
			if (cause instanceof DaemonError && cause.kind === 'not_found') {
				probeRoute = 'absent';
				outcome = {
					ok: false,
					message:
						'This daemon serves no /kitchen/discovery route, so nothing here can be measured from the browser. Run a newer daemon to probe.'
				};
			} else {
				outcome = {
					ok: false,
					message:
						cause instanceof Error ? cause.message : 'The probe failed before it reported.'
				};
			}
			outcomeEl?.focus();
		} finally {
			probing = false;
		}
	}

	const measured = (view: Kitchen): string => {
		if (!view.configured) return 'nothing declared to measure';
		if (view.discovery.facts.length === 0)
			return probing ? 'measuring now' : 'not measured — nothing has been probed yet';
		if (probedAt) return `measured at ${probedAt.toLocaleTimeString()}`;
		return 'measured earlier in this daemon session, not by this window';
	};

	const headline = (): string => {
		if (kitchen.phase === 'error') return 'Not answering';
		if (!kitchen.data) return 'Reading';
		if (kitchen.stale) return 'Stale';
		if (!kitchen.data.configured) return 'Nothing declared';
		return `${rig.ready} of ${rig.declared} ready`;
	};

	/* --- the one authored motion ---------------------------------------------
	   A column takes up load when a probe really raised it between two reads,
	   and at no other time. The height is the whole claim of this drawing, so
	   the moment it changes is the moment worth animating; a column that was
	   already standing does not re-enact its own probe on every read. */
	const lastHeight = new Map<string, number>();
	let rising = $state<string[]>([]);
	$effect(() => {
		const drawn = columns;
		if (drawn.length === 0) return;
		const grew: string[] = [];
		for (const column of drawn) {
			const before = lastHeight.get(column.harness);
			if (before !== undefined && column.reached > before) grew.push(column.harness);
			lastHeight.set(column.harness, column.reached);
		}
		if (grew.length === 0) return;
		rising = grew;
		const done = setTimeout(() => (rising = []), 360);
		return () => clearTimeout(done);
	});

	$effect(() => {
		const el = strip;
		if (!el) return;
		// Read on every re-render of the strip's contents, and on resize.
		void columns.length;
		const measure = () => {
			scrollable = el.scrollWidth - el.clientWidth > 1;
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		return () => observer.disconnect();
	});

	const headY = (column: Column): number => BASE - column.reached * BAND;

	const stateWord: Record<string, string> = {
		ready: 'Ready',
		degraded: 'Degraded',
		unavailable: 'Unavailable',
		unknown: 'Unknown',
		unconfigured: 'Not declared'
	};

	const seatMark: Record<string, string> = {
		supported: 'declared supported',
		unsupported: 'declared unsupported',
		unknown: 'undeclared'
	};

	const columnLabel = (column: Column): string => {
		const course = COURSES[column.reached - 1];
		const claims = column.seats.filter((s) => s.state !== 'unknown').length;
		return `${column.harness} — ${stateWord[column.state]}, observed as far as ${course.name.toLowerCase()}, ${claims} of ${column.seats.length} capabilities declared`;
	};
</script>

<section class="kitchen">
	<p class="label rule-label">
		<span>Rig</span>
		<span class="rule"></span>
		<span
			class="member"
			data-state={kitchen.phase === 'error'
				? 'failed'
				: kitchen.stale || !kitchen.data
					? 'slack'
					: kitchen.data.ready
						? 'seated'
						: 'balanced'}
		>
			{headline()}
		</span>
	</p>

	<p
		bind:this={outcomeEl}
		class="outcome member"
		data-state={outcome === null ? 'balanced' : outcome.ok ? 'seated' : 'failed'}
		role="status"
		tabindex="-1"
	>
		{#if outcome}
			<span class="label">{outcome.ok ? 'Measured' : 'Not measured'}</span>
			<span>{outcome.message}</span>
		{/if}
	</p>

	<AsyncField resource={kitchen} reading="the kitchen" onretry={() => void kitchen.load()}>
		{#snippet children(view: Kitchen)}
			<dl class="readout plate">
				<div>
					<dt class="label">Declared</dt>
					<dd class="value">{rig.declared}</dd>
					<p class="gloss">harnesses named in this project's kitchen</p>
				</div>
				<div>
					<dt class="label">Ready</dt>
					<dd class="value member" data-state={rig.ready > 0 ? 'seated' : 'slack'}>
						{view.configured ? rig.ready : '—'}
					</dd>
					<p class="gloss">
						{#if !view.configured}
							nothing is declared, so nothing can be ready
						{:else if rig.unprobed > 0 || rig.other > 0}
							observed to run and report a version{#if rig.unprobed > 0}, with {rig.unprobed}
								unmeasured{/if}{#if rig.other > 0}{rig.unprobed > 0 ? ' and' : ', with'}
								{rig.other} measured and settled as neither{/if}
						{:else}
							observed to run and report a version
						{/if}
					</p>
				</div>
				<div>
					<dt class="label">Unavailable</dt>
					<dd class="value member" data-state={rig.unavailable > 0 ? 'failed' : 'balanced'}>
						{view.configured ? rig.unavailable : '—'}
					</dd>
					<p class="gloss">
						{view.configured
							? 'looked for and not standing — missing, or refusing to run'
							: 'nothing is declared, so nothing was looked for'}
					</p>
				</div>
				<div>
					<dt class="label">Measurement</dt>
					<dd class="value member" data-state={probedAt ? 'seated' : 'slack'}>
						{view.discovery.facts.length}
						<span class="of">of {rig.declared}</span>
					</dd>
					<p class="gloss">{measured(view)}</p>
				</div>
			</dl>

			<div class="rig-body">
				<div class="elevation">
					<div class="floor">
						<ol class="ladder" aria-hidden="true">
							{#each ladder as course (course.id)}
								<li><span class="label">{course.name}</span></li>
							{/each}
						</ol>

						{#if columns.length === 0}
							<p class="bare prose">
								The base line carries nothing. This project declares no harness, so there is
								no column to stand on it and nothing to measure.
							</p>
						{:else}
							<div
								bind:this={strip}
								class="columns"
								class:scrollable
								role="tablist"
								aria-label="Declared harnesses, drawn as columns"
							>
								{#each columns as column, index (column.harness)}
									{@const state = memberState(column.state)}
									<button
										type="button"
										role="tab"
										id="column-{index}"
										class="column member"
										data-state={state}
										class:rising={rising.includes(column.harness)}
										aria-selected={index === at}
										aria-controls="rig-reading"
										tabindex={index === at ? 0 : -1}
										aria-label={columnLabel(column)}
										onclick={() => (selected = column.harness)}
										onkeydown={onKeys}
									>
										<span class="name value">{column.harness}</span>
										<span class="label state">{stateWord[column.state]}</span>
										<svg viewBox="0 0 72 {BASE}" aria-hidden="true">
											<!-- The courses observation did not clear, kept on the sheet
											     as the ghost they are: a short column is only short
											     against the height it was meant to reach. -->
											<path
												class="ghost"
												d="M36 {headY(column)} V{BASE - COURSES.length * BAND}"
												fill="none"
											/>
											{#each COURSES as course, i (course.id)}
												<path
													class="tick"
													class:cleared={column.reached > i}
													d="M28 {BASE - (i + 1) * BAND} H44"
													fill="none"
												/>
											{/each}

											<!-- The proven height. -->
											<path class="shaft" d="M36 {BASE} V{headY(column)}" fill="none" />

											{#if column.state === 'unavailable'}
												<!-- The load path is discontinuous, drawn where it stopped. -->
												<path
													class="break"
													d="M27 {headY(column) - 5} l18 -7 M27 {headY(column) - 12} l18 -7"
													fill="none"
												/>
											{:else if column.state === 'ready'}
												<!-- Seated: the load transferred and the member is capped. -->
												<path class="cap" d="M24 {headY(column)} H48" fill="none" />
											{/if}

											<!-- Declared capabilities are bolted to the head the
											     probe actually reached — under it, never up in the
											     ghost of the courses it never cleared. Filled is
											     declared supported, open is undeclared, struck is
											     declared unsupported. -->
											{#each column.seats as seat, i (seat.id)}
												{@const x = 12 + i * 10}
												{@const y = Math.min(headY(column) + 9, BASE - 9)}
												<rect
													class="seat"
													class:on={seat.state === 'supported'}
													class:off={seat.state === 'unsupported'}
													{x}
													{y}
													width="7"
													height="7"
												/>
												{#if seat.state === 'unsupported'}
													<path class="strike" d="M{x} {y + 7} l7 -7" fill="none" />
												{/if}
											{/each}
										</svg>
									</button>
								{/each}
							</div>
						{/if}
					</div>

					<dl class="legend">
						<div>
							<dt class="label">Course</dt>
							<dd>observed — how far the probe carried it</dd>
						</div>
						<div>
							<dt class="label">Seat</dt>
							<dd>declared — filled supported, open undeclared, struck unsupported</dd>
						</div>
					</dl>

					<div class="probe">
						{#if probeRoute === 'absent'}
							<p class="member prose" data-state="slack">
								<span class="label">Unavailable</span>
								Measuring is this daemon's route to serve and it does not serve it.
							</p>
						{:else}
							<button
								type="button"
								class="plate act"
								onclick={() => void probe()}
								disabled={probing || !view.configured}
							>
								{probing ? 'Measuring' : 'Measure the rig'}
							</button>
							<p class="prose gloss-line">
								A measurement resolves each declared executable and runs one bounded
								<code>--version</code> on it. It writes nothing — not this project's kitchen,
								and nothing any harness owns — and it starts a real process per harness, which
								is why it is asked for rather than polled.
							</p>
						{/if}
					</div>
				</div>

				{#if current}
					<div
						class="reading plate"
						id="rig-reading"
						role="tabpanel"
						aria-labelledby="column-{at}"
						tabindex="0"
					>
						<h2 class="name-head">
							<span class="value">{current.harness}</span>
							<span class="member state-chip" data-state={memberState(current.state)}>
								{stateWord[current.state]}
							</span>
						</h2>

						<p class="label section">Observed</p>
						{#if current.observed === null}
							<p class="prose">
								Nothing observed. No probe has touched this harness since the daemon started,
								so every reading below is absent rather than negative.
							</p>
						{:else}
							{@const seen = current.observed}
							<dl class="facts">
								<div>
									<dt class="label">Executable</dt>
									<dd class="path">{seen.executable ?? 'not found'}</dd>
								</div>
								<div>
									<dt class="label">Version</dt>
									<dd>{seen.version ?? 'none read'}</dd>
								</div>
								<div>
									<dt class="label">Health</dt>
									<dd>{seen.health}</dd>
								</div>
								{#if seen.detail}
									<div class="wide">
										<dt class="label">The probe's words</dt>
										<dd class="quote">{seen.detail}</dd>
									</div>
								{/if}
							</dl>
						{/if}

						<p class="label section">Declared</p>
						<ul class="seats">
							{#each current.seats as seat (seat.id)}
								<li>
									<span class="mark" data-seat={seat.state} aria-hidden="true"></span>
									<span class="seat-name">{seat.name}</span>
									<span class="seat-state">{seatMark[seat.state]}</span>
									<span class="gloss">{seat.gloss}</span>
								</li>
							{/each}
						</ul>
						<p class="prose gloss-line">
							Declarations are read from this project's kitchen document. Nothing in the daemon
							verifies one, so a claim here is a claim, not a finding.
						</p>

						<p class="label section">Not observed</p>
						<p class="prose">
							Authentication. A version probe proves the executable runs, not that it can reach
							a provider — <code>--version</code> never signs in. Herdsman reads no credential
							for any harness and shows none here; check sign-in inside {current.harness}
							itself. This view never renders a harness's declared launch template for the
							same reason — a flag can carry a secret — so the resolved executable is the
							only command-line fact it holds.
						</p>

						{#if current.reason || current.action}
							<p class="label section">Next</p>
							<div class="next member" data-state={memberState(current.state)}>
								{#if current.reason}<p class="why">{current.reason}</p>{/if}
								{#if current.action}<p class="do">{current.action}</p>{/if}
							</div>
						{/if}
					</div>
				{/if}
			</div>

			{#if view.blockers.length > 0}
				<section class="blockers" aria-labelledby="blockers-head">
					<p class="label rule-label">
						<span id="blockers-head">Blocking a run</span>
						<span class="rule"></span>
						<span class="member" data-state="failed">{view.blockers.length}</span>
					</p>
					<ul class="daemon-words">
						{#each view.blockers as blocker (blocker)}
							<li class="member" data-state="failed">{blocker}</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if !view.configured}
				<section class="first-run" aria-labelledby="first-run-head">
					<p class="label rule-label">
						<span id="first-run-head">Declaring one</span>
						<span class="rule"></span>
					</p>
					<p class="prose">
						Declarations live in <code>.herdsman/kitchen.json</code>. A minimal document is one
						harness and two model assignments — the documented first run. Writing it from here
						is K2's surface and is not built yet; this is the shape it wants, for reading and
						copying. It is written here, not read back from anywhere: no configured
						project's launch template is ever rendered by this view.
					</p>
					<pre class="plate example"><code>{EXAMPLE_DECLARATION}</code></pre>
				</section>
			{/if}

			{#if view.notes.length > 0}
				<section class="notes" aria-labelledby="notes-head">
					<p class="label rule-label">
						<span id="notes-head">Provenance</span>
						<span class="rule"></span>
					</p>
					<ul class="daemon-words">
						{#each view.notes as note (note)}
							<li class="member" data-state="slack">{note}</li>
						{/each}
					</ul>
				</section>
			{/if}
		{/snippet}
	</AsyncField>
</section>

<style>
	.kitchen {
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
	/* Present in the accessibility tree from first paint — a live region created
	   at the moment of the change is not reliably announced — and taking no
	   space until it has something to report. */
	.outcome:empty {
		height: 0;
		margin: 0;
		overflow: hidden;
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
	.of {
		color: var(--ink-2);
		font-size: 0.75rem;
	}
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.gloss-line {
		margin: 0.75rem 0 0;
		font-size: 0.75rem;
		color: var(--ink-2);
	}

	/* --- the elevation --------------------------------------------------------
	   One base line, one column per harness, one rhythm. The ladder annotates
	   the courses from the top down while the columns clear them from the base
	   up, which is how an elevation is drawn and read. */
	.rig-body {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(24rem, 1fr);
		gap: 1.5rem 2rem;
		align-items: start;
		margin-top: 2rem;
	}
	/* One unitless scale drives the drawing, its ladder and the column width
	   together, so the elevation is drafted at the size the viewport affords
	   instead of being rendered at mobile size on a 1440 sheet. The SVG keeps
	   its own coordinate system; only the box it is drawn into grows. */
	.floor {
		--scale: 1;
		--elev: calc(236px * var(--scale));
		--headroom: calc(28px * var(--scale));
		--band: calc(52px * var(--scale));
		--col-w: calc(72px * var(--scale));
		display: flex;
		align-items: flex-end;
		gap: 1rem;
		border-bottom: 1.25px solid var(--member-line);
	}
	@media (min-width: 62rem) {
		.floor {
			--scale: 1.7;
		}
	}
	.ladder {
		display: grid;
		/* The headroom the seats occupy is padding, not a row: a grid row nothing
		   is placed in gets back-filled by auto-placement and the whole ladder
		   slips one course. */
		grid-template-rows: repeat(4, var(--band));
		list-style: none;
		margin: 0;
		padding: var(--headroom) 0 0;
		width: 6.5rem;
		flex: none;
	}
	/* Every label rides its own course line, and the first one starts below the
	   headroom the seats occupy — so the ladder's rules and the columns' ticks
	   are the same four heights, measured from the same base line. */
	.ladder li {
		grid-column: 1;
		border-top: 1px dashed var(--rule);
		padding-top: 0.25rem;
		text-align: right;
	}
	.columns {
		display: flex;
		align-items: flex-end;
		gap: 0.25rem;
		overflow-x: auto;
		overflow-y: hidden;
		flex: 1;
		min-width: 0;
	}
	/* Only when columns really run off the edge: the sheet fades out rather than
	   cutting a harness mid-word with nothing to say more rig exists. */
	.columns.scrollable {
		mask-image: linear-gradient(to right, #000 calc(100% - 2.5rem), transparent);
	}
	.bare {
		margin: 0 0 1.5rem;
		align-self: flex-end;
	}

	.column {
		display: flex;
		flex-direction: column;
		align-items: center;
		flex: none;
		font: inherit;
		background: transparent;
		border: 0;
		border-bottom: 2.5px solid transparent;
		padding: 0 0.25rem;
		cursor: pointer;
		color: var(--member-ink);
	}
	.column svg {
		display: block;
		width: var(--col-w);
		height: var(--elev);
		overflow: visible;
	}
	.column .name {
		margin-bottom: 0.15rem;
		color: var(--ink);
		max-width: 6rem;
		overflow-wrap: anywhere;
	}
	.column .state {
		margin-bottom: 0.6rem;
		color: var(--member-ink);
	}
	/* Which column is being read is location, not load: a harder edge, never red. */
	.column[aria-selected='true'] {
		border-bottom-color: var(--member-line);
	}
	.column:hover .name,
	.column[aria-selected='true'] .name {
		text-decoration: underline;
		text-underline-offset: 0.3em;
	}

	.shaft {
		stroke: var(--member-ink);
		stroke-width: 2.25;
	}
	.column[data-state='slack'] .shaft {
		stroke-width: 1.25;
	}
	.ghost {
		stroke: var(--ash);
		stroke-width: 1;
		stroke-dasharray: 3 4;
	}
	.tick {
		stroke: var(--rule-strong);
		stroke-width: 1;
	}
	.tick.cleared {
		stroke: var(--member-ink);
	}
	.cap {
		stroke: var(--member-ink);
		stroke-width: 2.25;
	}
	.break {
		stroke: var(--red);
		stroke-width: 1.25;
	}
	/* Seats are declarations, so they are drawn in the world's own declaration
	   ink and never in the member's state colour: a capability this project
	   claimed does not become a tension because the probe failed to find the
	   harness. The reading panel's marks use the same ink. */
	.seat {
		fill: none;
		stroke: var(--ash);
		stroke-width: 1;
	}
	.seat.on {
		fill: var(--seat);
		stroke: var(--seat);
	}
	.seat.off {
		stroke: var(--rule-strong);
	}
	.strike {
		stroke: var(--rule-strong);
		stroke-width: 1;
	}

	/* The authored moment, on the axis this drawing uses: a column that a probe
	   really raised settles into its new height rather than appearing at it. */
	@keyframes stand-up {
		0% {
			transform: scaleY(0.94);
		}
		62% {
			transform: scaleY(1.012);
		}
		100% {
			transform: scaleY(1);
		}
	}
	.column.rising svg {
		transform-origin: bottom center;
		animation: stand-up 0.34s cubic-bezier(0.16, 1, 0.3, 1);
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1.5rem;
		margin: 0.75rem 0 0;
		font-size: 0.75rem;
		color: var(--ink-2);
	}
	.legend > div {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}
	.legend dd {
		color: var(--ink-2);
	}

	.probe {
		margin-top: 1.5rem;
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
		color: var(--ash);
		border-color: var(--rule);
		cursor: default;
	}

	/* --- the reading beside the drawing -------------------------------------- */
	.reading {
		--cut: 12px;
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 1rem 1.25rem 1.25rem;
	}
	.name-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1rem;
		font: inherit;
	}
	.name-head .value {
		font-size: 1.0625rem;
	}
	.state-chip {
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--member-ink);
	}
	.section {
		margin: 1.25rem 0 0.5rem;
		padding-top: 0.5rem;
		border-top: 1px solid var(--rule);
	}
	.facts > div {
		display: grid;
		grid-template-columns: 6rem minmax(0, 1fr);
		gap: 0.5rem;
		padding: 0.2rem 0;
	}
	.facts .wide {
		grid-template-columns: minmax(0, 1fr);
	}
	.path,
	.quote {
		overflow-wrap: anywhere;
		color: var(--ink);
	}
	.quote {
		border-left: 1px solid var(--rule-strong);
		padding-left: 0.6rem;
		color: var(--ink-2);
	}
	.seats {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.seats li {
		display: grid;
		grid-template-columns: 0.75rem minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.25rem 0.6rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--rule);
	}
	.seats li:last-child {
		border-bottom: 0;
	}
	.seats .gloss {
		grid-column: 2 / -1;
		margin: 0;
	}
	.seat-state {
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-2);
		text-align: right;
	}
	/* The same three seat marks as the drawing, at reading size. */
	.mark {
		width: 0.5rem;
		height: 0.5rem;
		border: 1px solid var(--ash);
		align-self: center;
	}
	.mark[data-seat='supported'] {
		background: var(--ink);
		border-color: var(--ink);
	}
	.mark[data-seat='unsupported'] {
		border-color: var(--rule-strong);
		background: linear-gradient(
			to top right,
			transparent calc(50% - 0.5px),
			var(--rule-strong) calc(50% - 0.5px),
			var(--rule-strong) calc(50% + 0.5px),
			transparent calc(50% + 0.5px)
		);
	}
	.next {
		color: var(--member-ink);
	}
	.next .why {
		margin: 0;
	}
	.next .do {
		margin: 0.35rem 0 0;
		color: var(--ink);
	}

	/* --- the daemon's own sentences ------------------------------------------ */
	.blockers,
	.first-run,
	.notes {
		margin-top: 2.5rem;
	}
	.daemon-words {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.daemon-words li {
		padding: 0.4rem 0 0.4rem 0.9rem;
		border-left: 1px solid var(--member-ink);
		color: var(--member-ink);
	}
	.daemon-words li + li {
		margin-top: 0.4rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}
	.example {
		--cut: 12px;
		margin: 1rem 0 0;
		padding: 1rem 1.25rem;
		background: var(--plate);
		border: 1px solid var(--rule);
		overflow-x: auto;
		color: var(--ink-2);
		font-size: 0.8125rem;
		line-height: 1.7;
	}
	.example code {
		background: none;
		border: 0;
		padding: 0;
	}

	@media (max-width: 62rem) {
		.rig-body {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 34rem) {
		.ladder {
			width: 4.5rem;
		}
		.floor {
			--scale: 0.82;
			gap: 0.5rem;
		}
	}
</style>
