<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { VIEWS, viewFor, type View } from '$lib/views';
	import { daemon, type PlanGraph } from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';
	import { CHORDS } from '$lib/locate';
	import Locator from '$lib/Locator.svelte';
	import ViewIcon from '$lib/ViewIcon.svelte';
	import { setContext, tick } from 'svelte';

	let { children } = $props();

	const view = $derived(viewFor(page.url.pathname));

	/* There is no GET /plans, so a plan is addressed by id in the URL. That is
	   also the deep link F2 will build on, so it lives in the shell, not a view. */
	const planId = $derived(page.url.searchParams.get('plan'));

	let graph = $state<Resource<PlanGraph> | null>(null);
	let requested = $state<string | null>(null);

	$effect(() => {
		const id = planId;
		if (id === requested) return;
		requested = id;
		graph?.dispose();
		if (!id) {
			graph = null;
			return;
		}
		const resource = new Resource<PlanGraph>((signal) => daemon.graph(id, signal));
		graph = resource;
		void resource.load();
	});

	setContext('plan', {
		get resource() {
			return graph;
		},
		get id() {
			return planId;
		},
		reload: () => void graph?.load()
	});

	/* Theme: system unless the operator has said otherwise. */
	type Theme = 'system' | 'light' | 'dark';
	let theme = $state<Theme>('system');
	$effect(() => {
		try {
			const stored = localStorage.getItem('herdsman-theme');
			if (stored === 'light' || stored === 'dark') theme = stored;
		} catch {
			/* blocked storage: system preference stands */
		}
	});
	function cycleTheme() {
		theme = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system';
		const root = document.documentElement;
		if (theme === 'system') {
			delete root.dataset.theme;
			try {
				localStorage.removeItem('herdsman-theme');
			} catch {
				/* nothing to preserve */
			}
		} else {
			root.dataset.theme = theme;
			try {
				localStorage.setItem('herdsman-theme', theme);
			} catch {
				/* the choice still applies to this session */
			}
		}
	}

	/* A gated view is slack whether or not you are standing on it: it carries no
	   load. Red marks load, never location -- `aria-current` and the ring's
	   locator halo say where you are. */
	const nodeState = (v: View) =>
		v.gate ? 'slack' : view?.id === v.id ? 'loaded' : 'balanced';

	/* The strut narrows to its member: the seats stay, the names step off.
	   Remembered, because a rail the operator closed should not reopen on every
	   navigation, and read before first paint is not required — the width is
	   chrome, not content, so a settle on mount costs nothing readable. */
	let railShut = $state(false);
	/* Motion is armed a beat after the remembered width has been applied, so a
	   reload of a shut rail arrives shut instead of playing itself closed. */
	let railArmed = $state(false);
	$effect(() => {
		try {
			railShut = localStorage.getItem('herdsman-rail') === 'shut';
		} catch {
			/* blocked storage: the rail stays open */
		}
		const armed = setTimeout(() => (railArmed = true), 50);
		return () => clearTimeout(armed);
	});
	function toggleRail() {
		railShut = !railShut;
		try {
			localStorage.setItem('herdsman-rail', railShut ? 'shut' : 'open');
		} catch {
			/* the choice still applies to this session */
		}
	}

	/* --- the band, and the shell's own keys (F2) ----------------------------- */
	let locateOpen = $state(false);

	/* Apple platforms print the platform's own modifier; everything else spells
	   Ctrl. Guarded, because there is no navigator before the browser exists. */
	const locateChord = (() => {
		try {
			return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent)
				? '⌘K'
				: 'Ctrl K';
		} catch {
			return 'Ctrl K';
		}
	})();

	/* The `g` chord is two keys on purpose: a bare letter that navigates will
	   eventually fire against a surface that should have swallowed it, and Run
	   has armed approval controls on screen. The window closes after 1.2s. */
	let chordPending = false;
	let chordTimer: ReturnType<typeof setTimeout> | undefined;

	const isTyping = (target: EventTarget | null): boolean =>
		target instanceof HTMLElement &&
		(target instanceof HTMLInputElement ||
			target instanceof HTMLTextAreaElement ||
			target instanceof HTMLSelectElement ||
			target.isContentEditable);

	/* Arrival focus is claimed, not imposed, and claiming it means putting the
	   caret inside `main#field` — which is where every view renders, so a
	   surface that owns its own arrival has already done it. Containment, not
	   identity: after a chord the caret is often still on the strut link or the
	   control it was pressed from, which is not the body and is not arrival. */
	async function arrive(): Promise<void> {
		await tick();
		const field = document.getElementById('field');
		if (field && !field.contains(document.activeElement)) field.focus();
	}

	function onkeydown(event: KeyboardEvent) {
		if (locateOpen || isTyping(event.target)) return;
		if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey
			&& event.key.toLowerCase() === 'k') {
			/* The browser's own ⌘K (a search in the chrome) is the one thing
			   suppressed; everything else is left to the browser's own keys. */
			event.preventDefault();
			locateOpen = true;
			return;
		}
		// A modifier this binding does not name means a browser chord, not ours.
		if (event.metaKey || event.ctrlKey || event.altKey) return;
		if (event.key === 'g') {
			chordPending = true;
			clearTimeout(chordTimer);
			chordTimer = setTimeout(() => (chordPending = false), 1200);
			return;
		}
		if (chordPending && event.key in CHORDS) {
			clearTimeout(chordTimer);
			chordPending = false;
			const chordView = VIEWS.find((v) => v.id === CHORDS[event.key as keyof typeof CHORDS]);
			if (chordView) void goto(chordView.href).then(arrive);
		}
	}
</script>

<svelte:window onkeydown={onkeydown} />

<svelte:head>
	<title>{view ? `${view.name} — Herdsman` : 'Herdsman'}</title>
</svelte:head>

<a class="skip" href="#field">Skip to content</a>

<Locator open={locateOpen} onclose={() => (locateOpen = false)} />

<div class="shell" class:shut={railShut} class:armed={railArmed}>
	<nav class="strut" aria-label="Views">
		<!-- The mark is the head of the member: the same drawing the favicon
		     carries (`static/favicon.svg`), minus its plate, seated on the
		     strut's own line so the structure runs out of it. Carbon only —
		     red means load, and a wordmark carries none.

		     It is also the rail's own control. Nothing else in the column is
		     always visible at both widths, and a drawing is closed from its
		     head. -->
		<button
			class="mark"
			type="button"
			onclick={toggleRail}
			aria-expanded={!railShut}
			aria-label={railShut ? 'Herdsman: open the view rail' : 'Herdsman: narrow the view rail'}
			title={railShut ? 'Open the view rail' : 'Narrow the view rail'}
		>
			<svg class="glyph" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
				<path d="M3 14H29V18H3V14ZM8 5H12V27H8V5ZM20 5H24V27H20V5Z" />
			</svg>
			<span class="mark-word">Herdsman</span>
		</button>
		<ul>
			{#each VIEWS as v (v.id)}
				<li>
					<a
						class="node member"
						data-state={nodeState(v)}
						href={v.href}
						aria-current={view?.id === v.id ? 'page' : undefined}
						title={railShut ? v.name : undefined}
					>
						<span class="seat"><ViewIcon id={v.id} /></span>
						<span class="node-text">
							<span class="node-name">{v.name}</span>
							<span class="node-purpose">{v.purpose}</span>
						</span>
						{#if v.gate}<span class="slack-mark">slack</span>{/if}
					</a>
				</li>
			{/each}
		</ul>

	</nav>

	<div class="field">
		<header class="titleblock">
			<div class="cell">
				<span class="label">Daemon</span>
				<span class="value member" data-state={!graph ? 'slack' : graph.phase === 'error' ? 'failed' : graph.stale ? 'slack' : 'seated'}>
					{#if !graph}Not addressed{:else if graph.phase === 'loading'}Reading{:else if graph.phase === 'error'}Not answering{:else if graph.stale}Stale{:else}Answering{/if}
				</span>
			</div>
			<div class="cell">
				<span class="label">Plan</span>
				<span class="value">{planId ?? '—'}</span>
			</div>
			<div class="cell">
				<span class="label">Revision</span>
				<span class="value">{graph?.data ? graph.data.version : '—'}</span>
			</div>
			<div class="cell">
				<span class="label">Approval</span>
				<span class="value member" data-state={graph?.data?.approval === 'approved' ? 'seated' : 'slack'}>
					{graph?.data ? graph.data.approval : '—'}
				</span>
			</div>
			<button class="cell theme" type="button" onclick={() => (locateOpen = true)}
				aria-label="Open the locate band">
				<span class="label">Locate</span>
				<span class="value">{locateChord}</span>
			</button>
			<button class="cell theme" type="button" onclick={cycleTheme}
				aria-label="Theme: {theme}. Activate to change.">
				<span class="label">Light</span>
				<span class="value">{theme}</span>
			</button>
		</header>

		<!-- tabindex=-1: the skip link's own target, and the arrival focus a
		     plain view or plan jump claims when nothing else took it. -->
		<main id="field" class="sheet" tabindex="-1">
			<div class="sheet-inner plate">
				{#if view}
					<h1 class="display">{view.name}</h1>
				{/if}
				{@render children()}
			</div>
		</main>
	</div>
</div>

<style>
	.skip {
		position: absolute;
		left: -9999px;
	}
	.skip:focus {
		left: 0.5rem;
		top: 0.5rem;
		z-index: 10;
		background: var(--plate);
		border: 1px solid var(--red);
		padding: 0.5rem 0.875rem;
	}

	.shell {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		min-height: 100vh;
	}

	/* --- the strut: a carbon member with the four views seated on it --------
	   Narrowing does not rebuild the column: the member line, the seats and
	   their gutter hold their exact positions, and only the text column is
	   withdrawn. The structure is the same drawing at either width. */
	.strut {
		border-right: 1px solid var(--rule);
		padding: 1.5rem 0 2rem;
		position: relative;
		display: flex;
		flex-direction: column;
		width: 17rem;
		min-width: 0;
		overflow: hidden;
	}
	.armed .strut {
		transition: width 0.42s cubic-bezier(0.16, 1, 0.3, 1);
	}
	.shut .strut {
		width: 3.25rem;
	}
	.mark {
		display: flex;
		background: transparent;
		border: 0;
		padding: 0;
		cursor: pointer;
		color: inherit;
		text-align: left;
		align-items: center;
		gap: 0.5rem;
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 76, 'wght' 700;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.02em;
		font-size: 0.9375rem;
		/* Pulled left by half the glyph so the glyph — not the text — sits on the
		   member line the nodes below are seated on. */
		margin: 0 0 2.5rem calc(1.5rem - 9px);
		white-space: nowrap;
	}
	.mark:hover {
		color: var(--red);
	}
	/* The column is drawn once, at one width, and the strut clips it. Nothing
	   inside relays out when the rail narrows, so no seat moves by a pixel. */
	.mark,
	.strut ul {
		width: 17rem;
		flex: none;
	}
	/* The name steps off before the column has finished closing, and waits for
	   it to open before stepping back on. */
	.armed .mark-word,
	.armed .node-text,
	.armed .slack-mark {
		transition:
			opacity 0.2s ease-out 0.18s,
			transform 0.32s cubic-bezier(0.16, 1, 0.3, 1) 0.14s;
	}
	.shut .mark-word,
	.shut .node-text,
	.shut .slack-mark {
		opacity: 0;
		transform: translateX(-0.5rem);
		pointer-events: none;
		transition-delay: 0s, 0s;
		transition-duration: 0.14s, 0.2s;
	}
	.glyph {
		flex: none;
		width: 18px;
		height: 18px;
		fill: currentColor;
	}
	.strut ul {
		list-style: none;
		margin: 0;
		padding: 0;
		position: relative;
	}
	/* The member itself: carbon, and running the whole column rather than
	   stopping under the last node. Ash here would say the structure is slack. */
	.strut::after {
		content: '';
		position: absolute;
		left: calc(1.5rem - 0.5px);
		/* The mark's bottom edge: the member drops out of the glyph rather than
		   starting in mid-air below it. 1.5rem of strut padding + the 18px glyph. */
		top: calc(1.5rem + 18px);
		bottom: 0;
		width: 1px;
		background: var(--member-line);
	}
	/* The length of member the current view loads. This is the run of tension the
	   operator is meant to find at a glance. */
	.node[data-state='loaded']::before {
		content: '';
		position: absolute;
		left: calc(1.5rem - 0.5px);
		top: 0;
		bottom: 0;
		width: 1px;
		background: var(--red);
		z-index: 1;
		/* The one authored moment, now on the member itself: the loaded run takes
		   up its length instead of appearing at it. */
		animation: take-up-run 0.32s cubic-bezier(0.16, 1, 0.3, 1);
		transform-origin: top center;
	}
	@keyframes take-up-run {
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
	.node {
		display: grid;
		grid-template-columns: 3rem minmax(0, 1fr) auto;
		align-items: start;
		column-gap: 0;
		padding: 0.5rem 1.25rem 0.5rem 0;
		text-decoration: none;
		color: var(--member-ink, var(--ink-2));
		position: relative;
	}
	/* The seat sits on the first text line, not the block's centre, and carries
	   the ground under it so the member passes behind the glyph rather than
	   through it. */
	.seat {
		grid-column: 1;
		justify-self: center;
		display: grid;
		place-items: center;
		position: relative;
		z-index: 2;
		margin-top: 0.32rem;
		width: 18px;
		height: 18px;
		background: var(--ground);
	}
	/* Location, in carbon. Never red: red is load. */
	.node[aria-current='page'] .seat {
		box-shadow:
			0 0 0 3px var(--ground),
			0 0 0 4px var(--member-line);
	}
	/* Slack changes form as well as colour: an undrawn member reads broken. */
	.node[data-state='slack'] .seat :global(svg) {
		stroke-dasharray: 2 2;
	}
	.node-text {
		grid-column: 2;
		display: flex;
		flex-direction: column;
		line-height: 1.3;
	}
	.node-name {
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--ink);
	}
	.node[data-state='slack'] .node-name {
		color: var(--ink-2);
	}
	.node-purpose {
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-2);
		margin-top: 0.2rem;
	}
	.slack-mark {
		grid-column: 3;
		align-self: start;
		margin-top: 0.3rem;
		font-size: 0.5625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
		border: 1px dashed var(--ash);
		padding: 0.05rem 0.3rem;
	}
	.node:hover {
		color: var(--red);
	}
	.node:hover .node-name {
		color: var(--red);
	}
	.node[aria-current='page'] .node-name {
		color: var(--ink);
	}


	/* --- title block -------------------------------------------------------- */
	.field {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.titleblock {
		display: flex;
		flex-wrap: wrap;
		background: var(--ground);
		border-bottom: 1px solid var(--rule);
	}
	.cell {
		padding: 0.625rem 1.25rem;
		min-width: 8.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		border-right: 1px solid var(--rule);
	}
	.cell .value {
		color: var(--member-ink, var(--ink));
	}
	.cell .label {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.cell .label::before {
		content: '';
		flex: none;
		width: 4px;
		height: 4px;
		border: 1px solid var(--member-line);
		border-radius: 50%;
	}
	/* The leader runs from the node out to the value pinned beneath it. */
	.cell .label::after {
		content: '';
		flex: 1;
		height: 1px;
		min-width: 0.75rem;
		background: var(--rule);
	}
	.theme {
		font: inherit;
		text-align: left;
		background: transparent;
		border: 0;
		border-right: 1px solid var(--rule);
		cursor: pointer;
		color: var(--ink);
	}
	.theme:hover .value {
		color: var(--red);
	}
	/* Plain focus (a programmatic arrival) takes no outline; the keyboard's
	   :focus-visible outline stays the global red one. */
	.sheet:focus {
		outline: none;
	}
	.sheet:focus-visible {
		outline: 2px solid var(--red);
		outline-offset: 2px;
	}

	/* --- the sheet ----------------------------------------------------------
	   A drawing sheet has an edge and corner ticks. That is what makes an
	   undrawn area read as a sheet awaiting work rather than a page that failed
	   to render. */
	.sheet {
		flex: 1;
		min-width: 0;
		padding: 3.25rem 2.5rem 4rem;
	}
	.sheet-inner {
		position: relative;
		max-width: 74rem;
		min-height: 60vh;
		padding: 2.5rem 2.75rem 3rem;
		border: 1px solid var(--rule);
	}
	.sheet-inner::before,
	.sheet-inner::after {
		content: '';
		position: absolute;
		width: 14px;
		height: 14px;
		border: 1px solid var(--ash);
		pointer-events: none;
	}
	.sheet-inner::before {
		top: -1px;
		left: -1px;
		border-right: 0;
		border-bottom: 0;
	}
	.sheet-inner::after {
		bottom: -1px;
		right: -1px;
		border-left: 0;
		border-top: 0;
	}
	.sheet-inner :global(h1.display) {
		font-size: clamp(3rem, 10vw, 8.5rem);
		margin-bottom: 2.75rem;
	}

	/* --- flatten the column on compact screens ----------------------------- */
	@media (max-width: 60rem) {
		.shell {
			grid-template-columns: minmax(0, 1fr);
		}
		/* The rail is already at its shortest here; narrowing it further has
		   nothing to give back, so the control goes and the names stay. */
		.strut,
		.shut .strut {
			border-right: 0;
			border-bottom: 1px solid var(--rule);
			padding: 1.25rem 0 0;
			width: auto;
		}
		.mark,
		.strut ul {
			width: auto;
		}
		.shut .mark-word,
		.shut .node-text,
		.shut .slack-mark {
			opacity: 1;
			transform: none;
			pointer-events: auto;
		}
		.mark {
			margin: 0 0 1.5rem calc(1.25rem - 9px);
		}
		.strut ul {
			display: flex;
			flex-wrap: wrap;
			padding: 0 1.25rem;
			column-gap: 0;
			row-gap: 0.5rem;
		}
		/* The member runs horizontally now, through the same nodes. */
		/* One absolute line cannot serve a wrapped rail, so each node carries its
		   own length of member and the segments join into a continuous run. */
		.strut::after {
			display: none;
		}
		.node::after {
			content: '';
			position: absolute;
			left: 0;
			right: 0;
			top: 0.625rem;
			height: 1px;
			background: var(--member-line);
			z-index: 0;
		}
		.node[data-state='loaded']::before {
			left: 0;
			right: 0;
			top: 0.625rem;
			bottom: auto;
			width: auto;
			height: 1px;
			z-index: 1;
			/* The run is horizontal here, so the load is taken up along it. */
			animation-name: take-up-load;
			transform-origin: left center;
		}
		.node {
			grid-template-columns: auto auto;
			grid-template-rows: auto auto;
			justify-items: start;
			align-items: center;
			padding: 0 1.5rem 0.75rem 0;
			row-gap: 0.6rem;
			column-gap: 0.5rem;
			white-space: nowrap;
		}
		.seat {
			grid-column: 1;
			grid-row: 1;
			margin-top: 0;
		}
		.node-text {
			grid-column: 1;
			grid-row: 2;
		}
		/* Purpose lines cost three wrapped rows here and say least; the name and
		   the slack mark carry the rail. */
		.node-purpose {
			display: none;
		}
		.slack-mark {
			grid-column: 2;
			grid-row: 2;
			align-self: center;
			margin-top: 0;
		}
		.sheet {
			padding: 1.75rem 1rem 3rem;
		}
		.sheet-inner {
			padding: 1.5rem 1.25rem 2rem;
			min-height: 0;
		}
		.cell {
			min-width: 6.5rem;
			padding: 0.5rem 0.875rem;
		}
		/* Only the cell at the end of the row gives up its divider. This was
		   `.theme` while the theme cell was the only button in the block; with
		   Locate beside it, that rule deleted a divider from the middle of the
		   title block at every narrow width. */
		.cell:last-child {
			border-right: 0;
		}
	}
</style>
