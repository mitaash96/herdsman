<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { VIEWS, viewFor, type View } from '$lib/views';
	import { daemon, type PlanGraph } from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';
	import { setContext } from 'svelte';

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
</script>

<svelte:head>
	<title>{view ? `${view.name} — Herdsman` : 'Herdsman'}</title>
</svelte:head>

<a class="skip" href="#field">Skip to content</a>

<div class="shell">
	<nav class="strut" aria-label="Views">
		<p class="mark">Herdsman</p>
		<ul>
			{#each VIEWS as v (v.id)}
				<li>
					<a
						class="node member"
						data-state={nodeState(v)}
						href={v.href}
						aria-current={view?.id === v.id ? 'page' : undefined}
					>
						<span class="ring" aria-hidden="true"></span>
						<span class="leader" aria-hidden="true"></span>
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
			<button class="cell theme" type="button" onclick={cycleTheme}
				aria-label="Theme: {theme}. Activate to change.">
				<span class="label">Light</span>
				<span class="value">{theme}</span>
			</button>
		</header>

		<main id="field" class="sheet">
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
		grid-template-columns: 17rem minmax(0, 1fr);
		min-height: 100vh;
	}

	/* --- the strut: a carbon member with the four views seated on it -------- */
	.strut {
		border-right: 1px solid var(--rule);
		padding: 1.5rem 0 2rem;
		position: relative;
		min-width: 0;
	}
	.mark {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 76, 'wght' 700;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.02em;
		font-size: 0.9375rem;
		margin: 0 0 2.5rem 1.5rem;
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
		top: 3.6rem;
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
	}
	.node {
		display: grid;
		grid-template-columns: 3rem 1.5rem minmax(0, 1fr) auto;
		align-items: start;
		column-gap: 0;
		padding: 0.5rem 1.25rem 0.5rem 0;
		text-decoration: none;
		color: var(--member-ink);
		position: relative;
	}
	/* Rings and leaders sit on the first text line, not the block's centre. */
	.ring {
		grid-column: 1;
		justify-self: center;
		position: relative;
		z-index: 2;
		margin-top: 0.42rem;
		width: 9px;
		height: 9px;
		border: 1.25px solid currentColor;
		border-radius: 50%;
		background: var(--ground);
	}
	.node[data-state='loaded'] .ring {
		background: var(--red);
	}
	/* Location, in carbon. Never red: red is load. */
	.node[aria-current='page'] .ring {
		box-shadow:
			0 0 0 3px var(--ground),
			0 0 0 4px var(--member-line);
	}
	.node[data-state='slack'] .ring {
		border-style: dashed;
	}
	.leader {
		grid-column: 2;
		margin-top: 0.62rem;
		height: 1px;
		background: currentColor;
		opacity: 0.5;
	}
	.node[data-state='loaded'] .leader {
		background: var(--red);
		opacity: 1;
		animation: take-up-load 0.32s cubic-bezier(0.16, 1, 0.3, 1);
		transform-origin: left center;
	}
	.node[aria-current='page'] .leader {
		opacity: 1;
	}
	.node-text {
		grid-column: 3;
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
		grid-column: 4;
		align-self: start;
		margin-top: 0.3rem;
		font-size: 0.5625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
		border: 1px dashed var(--ash);
		padding: 0.05rem 0.3rem;
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
		.strut {
			border-right: 0;
			border-bottom: 1px solid var(--rule);
			padding: 1.25rem 0 0;
		}
		.mark {
			margin: 0 0 1.5rem 1.25rem;
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
			top: 0.55rem;
			height: 1px;
			background: var(--member-line);
			z-index: 0;
		}
		.node[data-state='loaded']::before {
			left: 0;
			right: 0;
			top: 0.55rem;
			bottom: auto;
			width: auto;
			height: 1px;
			z-index: 1;
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
		.ring {
			grid-column: 1;
			grid-row: 1;
			margin-top: 0;
		}
		.leader {
			display: none;
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
		.theme {
			border-right: 0;
		}
	}
</style>
