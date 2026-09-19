<script lang="ts">
	/*
	  Unit F2 — the Index Band. Its direction contract lives in
	  `.impeccable/surfaces/ui-src-lib-locator-svelte.md`, not here.

	  A native modal `<dialog>` unrolled from the title block, transparent
	  behind, holding every read this band needs: the three fleet-and-shelf
	  enumerations started lazily on first open and held for the session, and —
	  only when a plan is addressed — the checkpoint report. The addressed
	  plan's graph is the shell's own read, taken from its `plan` context and
	  never read twice here.

	  What this surface is deliberately not, each named in the contract: a
	  search route, a text field that can carry an identity out, a recents
	  store, or anything that writes to the daemon.
	*/
	import { getContext, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { VIEWS } from '$lib/views';
	import {
		daemon,
		type AssetSummary,
		type CheckpointReport,
		type Fleet,
		type PlanGraph
	} from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';
	import { buildIndex, filterRows, groupRows, step } from '$lib/locate';

	let { open, onclose }: { open: boolean; onclose: () => void } = $props();

	/* The shell owns the addressed plan and has already read its graph; the
	   band borrows both rather than reading either again. */
	const plan = getContext<{
		readonly resource: Resource<PlanGraph> | null;
		readonly id: string | null;
		reload: () => void;
	}>('plan');

	/* --- the reads -----------------------------------------------------------
	   Started when the band first opens — a keystroke is the first use, and a
	   band nobody opened owes the daemon nothing — then held for the session,
	   because a locator that re-reads on every keystroke is a poll. */
	let opened = $state(false);
	let fleet = $state<Resource<Fleet> | null>(null);
	let archived = $state<Resource<Fleet> | null>(null);
	let shelfRes = $state<Resource<AssetSummary[]> | null>(null);
	$effect(() => {
		if (!open || opened) return;
		opened = true;
		fleet = new Resource<Fleet>((signal) => daemon.fleet(signal));
		void fleet.load();
		archived = new Resource<Fleet>((signal) => daemon.fleetArchived(signal));
		void archived.load();
		shelfRes = new Resource<AssetSummary[]>((signal) => daemon.library(signal));
		void shelfRes.load();
	});

	/* The checkpoint report is the one further read, started only when a plan
	   is addressed and the band is open; it is dropped when the address moves,
	   the way the Run page drops its own per-plan reads. */
	let reviews = $state<Resource<CheckpointReport> | null>(null);
	let requestedPlan = $state<string | null>(null);
	$effect(() => {
		const id = plan.id;
		if (!open || id === requestedPlan) return;
		requestedPlan = id;
		reviews?.dispose();
		if (!id) {
			reviews = null;
			return;
		}
		reviews = new Resource<CheckpointReport>((signal) => daemon.checkpoints(id, signal));
		void reviews.load();
	});

	/* --- the index, and the query over it ------------------------------------ */
	const index = $derived(
		buildIndex({
			views: VIEWS,
			fleet: fleet?.data ?? null,
			archived: archived?.data ?? null,
			assets: shelfRes?.data ?? null,
			graph: plan.resource?.data ?? null,
			report: reviews?.data ?? null,
			planId: plan.id
		})
	);

	let query = $state('');
	const filtered = $derived(filterRows(index, query));
	const groups = $derived(groupRows(filtered));
	/* What is actually on screen, in the order it is on screen. `filtered` is
	   ranked — a mark-prefix match outranks a gloss match — while the listing is
	   grouped by kind, so the two orders genuinely differ, and the cap drops
	   rows from the tail of a group that `filtered` still holds. Movement,
	   activation and the active descendant all run over this: an active row the
	   operator cannot see, or an arrow key that jumps up the sheet because the
	   next row by rank is in an earlier group, is a keyboard model that
	   contradicts the drawing. Ranking still decides which rows survive each
	   group's cap, which is the whole of what it is for. */
	const visible = $derived(groups.flatMap((group) => group.rows));
	let activeKey = $state<string | null>(null);

	/* The active row is re-seated on the first row when the QUERY changes —
	   typing is the operator re-aiming — and when the held key is no longer on
	   screen. It is never re-seated when only the ROWS change: a live read
	   landing (the fleet, the archived runs, the shelf, the checkpoint report)
	   rewrites `visible` without touching `query`, and this build's oldest rule
	   is that a live read never moves the reading position. Both cases are
	   stated here directly: the effect reads `query` and `visible`, and holds
	   the last query it saw in a plain `let`, so re-seating depends on nothing
	   firing first.

	   `open` is a dependency too, so closing clears the seen query and every
	   opening seats row one again — the query itself is held for the session.

	   The held key is read through `untrack`: this effect writes `activeKey`, so
	   reading it reactively is the self-retriggering effect H1 and L1 each
	   shipped once. */
	let seenQuery: string | null = null;
	$effect(() => {
		if (!open) {
			seenQuery = null;
			return;
		}
		const rows = visible;
		const q = query;
		const held = untrack(() => activeKey);
		const keep = q === seenQuery && held !== null && rows.some((row) => row.key === held);
		seenQuery = q;
		if (keep) return;
		activeKey = rows[0]?.key ?? null;
	});

	/* The active option is scrolled into view, never assumed visible. */
	$effect(() => {
		const key = activeKey;
		if (!key || !open) return;
		document.getElementById(`locate-opt-${key}`)?.scrollIntoView({ block: 'nearest' });
	});

	/* --- the dialog ---------------------------------------------------------- */
	let el = $state<HTMLDialogElement | null>(null);
	let filterEl = $state<HTMLInputElement | null>(null);

	/* Openness is the parent's state; the element's is the browser's. They are
	   reconciled in one direction here, and the native `close` event — Escape
	   included — reports back, so Escape and the shell take exactly the same
	   path. The platform's own focus restoration is why it is native. */
	$effect(() => {
		const dialog = el;
		if (!dialog) return;
		if (open && !dialog.open) {
			dialog.showModal();
			filterEl?.focus();
		} else if (!open && dialog.open) {
			dialog.close();
		}
	});

	/* --- keys and movement --------------------------------------------------- */
	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			activeKey = step(visible, activeKey, event.key === 'ArrowDown' ? 1 : -1);
		} else if (event.key === 'Home') {
			event.preventDefault();
			activeKey = visible[0]?.key ?? null;
		} else if (event.key === 'End') {
			event.preventDefault();
			activeKey = visible[visible.length - 1]?.key ?? null;
		} else if (event.key === 'Enter') {
			void go();
		}
		/* Escape belongs to the dialog itself. */
	}

	/* An option can take focus programmatically (tabindex=-1, not tabbable);
	   its own keys do what a click does, so a row is real from wherever it is
	   reached. Focus still never leaves the input in ordinary use. */
	function onOptionKey(event: KeyboardEvent) {
		const key = (event.currentTarget as HTMLElement).getAttribute('data-key');
		if (!key) return;
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			activeKey = key;
			void go();
		}
	}

	/* Pointer movement sets the active option, so there is no second "current"
	   signal competing with the keyboard's — and no hover colour at all. The
	   options carry their own pointer and click handlers, keyed by data-key. */

	/* Enter on nothing does nothing: the band can never navigate to typed
	   text, only to a row the daemon enumerated. */
	async function go(): Promise<void> {
		const row = visible.find((candidate) => candidate.key === activeKey);
		if (!row) return;
		onclose();
		await goto(row.path);
		await tick();
		/* Arrival focus is claimed, not imposed. Every surface that owns its own
		   arrival — the Run drawer opened by an address — puts the caret inside
		   `main#field`, because that is where every view renders. So the test is
		   containment, not identity: testing `activeElement === body` would fail
		   for every jump made from the Locate cell, since closing a modal dialog
		   hands focus back to whatever opened it and the caret is on that cell,
		   not the body. */
		const field = document.getElementById('field');
		if (field && !field.contains(document.activeElement)) field.focus();
	}

	function retry(notice: { resource: Resource<unknown> | null }): void {
		void notice.resource?.load();
	}

	/* One notice per read, in the band's own slot above the footer: a read in
	   flight is said, a failed read is an unread section with the daemon's own
	   message — never a silently absent group. */
	const notices = $derived<{ subject: string; reading: string; resource: Resource<unknown> | null }[]>([
		{ subject: 'Runs', reading: 'the fleet', resource: fleet },
		{ subject: 'Archived runs', reading: 'the archived runs', resource: archived },
		{ subject: 'The shelf', reading: 'the shelf', resource: shelfRes },
		{ subject: 'Checkpoints', reading: 'the checkpoint report', resource: reviews }
	]);
</script>

<!--
  The band covers the title block and that is the composition: the title
  block unrolls into the index. The field below stays fully legible — the
  backdrop is transparent and the hairline seam carries the mode, so nothing
  here dims what it covers.
-->
<dialog bind:this={el} class="band plate" aria-label="Locate" onclose={() => onclose()}>
	<div class="inner">
		<p class="label rule-label">
			<span>Locate</span><span class="rule"></span>
			<!-- Rendered from first paint and collapsed by :empty, so the count is
			     announced as it changes rather than created at the change. -->
			<span class="found" role="status">{#if filtered.length > 0}{filtered.length} found{/if}</span>
		</p>

		<!-- The filterable listbox the shared contract names, not a text field:
		     it narrows a daemon-enumerated list and can never carry an identity
		     out. Focus never leaves this input while the band is open. -->
		<input
			bind:this={filterEl}
			bind:value={query}
			type="text"
			class="filter"
			role="combobox"
			aria-expanded={filtered.length > 0}
			aria-controls={filtered.length > 0 ? 'locate-list' : undefined}
			aria-activedescendant={activeKey ? `locate-opt-${activeKey}` : undefined}
			aria-autocomplete="list"
			placeholder="Filter runs, members, checkpoints and assets"
			aria-label="Filter the index"
			autocomplete="off"
			spellcheck="false"
			onkeydown={onkeydown}
		/>

		{#if filtered.length === 0}
			<p class="prose empty" role="status">
				{#if query !== ''}Nothing matches <code>{query}</code>.&nbsp;{/if}This index carries
				the four views, every run on disk, the assets on the shelf, and the members
				and checkpoints of the run you have open — members of other runs are not
				indexed.{#if !plan.id} No run is addressed, so the member and checkpoint groups
				are absent, not empty.{/if}
			</p>
		{:else}
			<div class="results" id="locate-list" role="listbox" aria-label="Matching rows">
				{#each groups as group (group.kind)}
					<!-- A listbox owns options and groups and nothing else, so the
					     section is the group and the list between it and the rows is
					     removed from the tree. The heading is the group's own label
					     read twice otherwise, so it is hidden rather than repeated. -->
					<section class="group" role="group" aria-label={group.label}>
						<p class="label rule-label" aria-hidden="true">
							<span>{group.label}</span><span class="rule"></span>
							{#if group.total > group.rows.length}
								<span>{group.rows.length} of {group.total}</span>
							{/if}
						</p>
						<ul role="none">
							{#each group.rows as row (row.key)}
								<li
									class="row"
									role="option"
									id="locate-opt-{row.key}"
									data-key={row.key}
									aria-selected={row.key === activeKey}
									tabindex="-1"
									onpointermove={() => (activeKey = row.key)}
									onclick={() => { activeKey = row.key; void go(); }}
									onkeydown={onOptionKey}
								>
									<span class="ring" aria-hidden="true"></span>
									<span class="leader" aria-hidden="true"></span>
									<span class="row-mark">{row.mark}</span>
									<span class="row-gloss">{row.gloss}</span>
									<span class="row-state member" data-state={row.memberState}>{row.state}</span>
								</li>
							{/each}
						</ul>
					</section>
				{/each}
			</div>
		{/if}

		{#each notices as notice (notice.subject)}
			{#if notice.resource?.phase === 'loading'}
				<p class="notice label">Reading {notice.reading}…</p>
			{:else if notice.resource?.error && (notice.resource.phase === 'error' || notice.resource.stale)}
				<p class="notice member" data-state="slack" role="status">
					<span>
						{notice.subject} unread — the daemon did not answer: {notice.resource.error.message}
					</span>
					<button class="act plate" type="button" onclick={() => retry(notice)}>Read again</button>
				</p>
			{/if}
		{/each}

		<footer>
			<p class="label">↑↓ move · ⏎ go · esc close</p>
		</footer>
	</div>
</dialog>

<style>
	/* --- the band ------------------------------------------------------------
	   A plate held flush to the top and right viewport edges, cut at the
	   bottom-left only — the Edge-Cut Exception, exactly as the drawer takes
	   it, because a top-right chamfer landing on the browser frame reads as a
	   notch. Placement is the strut's own width, a constant, never a
	   measurement: the band starts exactly at the field's edge and nothing
	   here is measured. No scrim, no shadow, no motion — the band sets. */
	/* `display` is set on `[open]` only. A bare `.band { display: flex }` beats
	   the UA's own `dialog:not([open]) { display: none }` on specificity, and the
	   band then renders over the title block and the strut rail on every view,
	   all the time, whether or not anyone opened it. */
	.band {
		--cut: 12px;
		position: fixed;
		top: 0;
		bottom: auto;
		left: 17rem;
		right: 0;
		margin: 0;
		width: auto;
		max-width: none;
		max-height: 100dvh;
		border: 0;
		border-bottom: 1px solid var(--rule);
		padding: 1.5rem 2.5rem 1.25rem;
		background: var(--plate);
		color: var(--ink);
		flex-direction: column;
		border-radius: 0 0 0 var(--cut);
	}
	.band[open] {
		display: flex;
	}
	/* Overriding the shared geometry means overriding its fallback in the same
	   breath, or the shared fallback still cuts both corners. */
	@supports not (corner-shape: bevel) {
		.band {
			border-radius: 0;
			clip-path: polygon(
				0 0,
				100% 0,
				100% 100%,
				var(--cut) 100%,
				0 calc(100% - var(--cut))
			);
		}
	}
	.band::backdrop {
		background: transparent;
	}

	/* The sheet's own measure; the band's content holds to it, not to the
	   viewport's full width. */
	.inner {
		flex: 1;
		min-height: 0;
		width: 100%;
		max-width: 74rem;
		display: flex;
		flex-direction: column;
	}

	/* --- the ruled label, as everywhere else in this world ------------------- */
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	/* A live region rendered from first paint, taking no space until it speaks. */
	.found:empty {
		display: none;
	}

	/* --- the filter, in the system's input style ----------------------------- */
	.filter {
		--cut: 10px;
		font: inherit;
		flex: none;
		width: 100%;
		color: var(--ink);
		background: var(--plate);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	.filter:focus {
		border-color: var(--red);
	}
	/* A browser surface the design still owns: the default placeholder is a
	   UA-chosen dim of the text colour and clears no contrast bar. Graphite is
	   the system's own secondary reading — 4.86:1 on plate in light, 6.31:1 in
	   dark. */
	.filter::placeholder {
		color: var(--ink-2);
		opacity: 1;
	}

	/* --- the results: the Leader-Line Rule at list scale --------------------- */
	.results {
		flex: 1;
		min-height: 0;
		margin-top: 1.25rem;
		overflow-y: auto;
		overscroll-behavior: contain;
		/* A scroll box clips at its padding edge, and the active option's locator
		   halo is drawn 4px outside its own ring. Without this the one mark that
		   says which row you are on is sliced in half at the left edge. The
		   negative margin keeps the rows optically under the label above. */
		padding-left: 6px;
		margin-left: -6px;
	}
	.group + .group {
		margin-top: 1.5rem;
	}
	/* The group is the grid, and each row borrows its columns through subgrid,
	   so every mark and every gloss in a group lands on one line. A grid per row
	   re-decides the column width for each row, and a drawing office does not
	   set four ragged left edges down one list. */
	.group ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: auto 1.5rem minmax(6rem, max-content) minmax(0, 1fr) auto;
	}
	.row {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: baseline;
		gap: 0.2rem 0.65rem;
		padding: 0.45rem 0;
		border-bottom: 1px solid var(--rule);
		cursor: pointer;
	}
	.ring {
		align-self: center;
		width: 4px;
		height: 4px;
		border: 1px solid var(--member-line);
		border-radius: 50%;
	}
	.leader {
		align-self: center;
		height: 1px;
		background: var(--rule);
	}
	.row-mark {
		color: var(--ink-2);
		font-weight: 500;
		font-size: 0.9375rem;
		letter-spacing: -0.01em;
		overflow-wrap: anywhere;
	}
	.row-gloss {
		min-width: 0;
		color: var(--ink-2);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.row-state {
		flex: none;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--member-ink);
	}
	/* Ash draws slack and never sets text: a slack reading is graphite carrying
	   a dashed ash rule, as every other slack reading in this build is. */
	.row-state[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}

	/* The active option is location, and therefore carbon: the locator halo on
	   its ring, a full-opacity leader, and the mark in carbon. Never red,
	   never a filled background, never red-quiet — red means load, and the
	   state cell's loaded word is this surface's only red. */
	.row[aria-selected='true'] .ring {
		border-color: var(--member-line);
		box-shadow:
			0 0 0 3px var(--plate),
			0 0 0 4px var(--member-line);
	}
	.row[aria-selected='true'] .leader {
		background: var(--member-line);
	}
	.row[aria-selected='true'] .row-mark {
		color: var(--ink);
	}

	/* --- the notices and the footer ------------------------------------------ */
	.notice {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem 0.75rem;
		margin: 0.75rem 0 0;
	}
	.notice.member[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: none;
	}
	footer {
		flex: none;
		margin-top: 1rem;
		border-top: 1px solid var(--rule);
		padding-top: 0.6rem;
	}
	footer .label {
		margin: 0;
	}

	/* --- stated absences ------------------------------------------------------ */
	.empty {
		margin: 1.25rem 0 0;
		color: var(--ink-2);
	}
	.empty code {
		background: var(--ground);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}

	/* --- the ghost button, unchanged ------------------------------------------ */
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
		white-space: nowrap;
		cursor: pointer;
	}
	.act:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}

	/* --- the one breakpoint ---------------------------------------------------- */
	@media (max-width: 60rem) {
		.band {
			left: 0;
			padding: 1.25rem 1rem 1rem;
		}
		/* The gloss drops to its own second row rather than being truncated to
		   nothing: a brief that cannot be read is worse than none. */
		.group ul {
			grid-template-columns: auto 1.5rem minmax(0, 1fr) auto;
		}
		.row {
			grid-template-columns: subgrid;
		}
		.row-mark {
			grid-column: 3;
		}
		.row-state {
			grid-column: 4;
		}
		.row-gloss {
			grid-column: 2 / 5;
			grid-row: 2;
			white-space: normal;
			overflow-wrap: anywhere;
		}
	}
</style>
