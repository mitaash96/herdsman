<script lang="ts">
	import { onMount } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import {
		daemon,
		type NavEdge,
		type NavIndex,
		type NavSymbol,
		type NavText
	} from '$lib/daemon';
	import {
		derivedRoutes,
		filterRoutes,
		filterSymbols,
		parseFlow,
		parseTour,
		summarizeEdges,
		symbolForCitation,
		symbolRef,
		walkDerivedRoute,
		type Route,
		type RouteStop
	} from '$lib/nav';
	import { Resource } from '$lib/resource.svelte';

	const map = new Resource<NavIndex>((signal) => daemon.codemap(signal));
	const tourRead = new Resource<NavText>((signal) => daemon.tour(signal));
	const flowRead = new Resource<NavText>((signal) => daemon.flow('create-approve-run-settle', signal));
	let query = $state('');
	let selectedRouteId = $state<string | null>(null);
	let selectedSymbol = $state<NavSymbol | null>(null);
	let registerOpen = $state(false);

	const tour = $derived(parseTour(tourRead.data?.text));
	const flow = $derived(parseFlow(flowRead.data?.text, 'create-approve-run-settle'));
	const derivedRouteList = $derived(map.data ? derivedRoutes(map.data) : []);
	const routes = $derived([...(tour ? [tour] : []), ...(flow ? [flow] : []), ...derivedRouteList]);
	const filtered = $derived(filterRoutes(routes, query));
	const symbols = $derived(map.data ? filterSymbols(map.data.symbols, query) : []);
	const initialReadsSettled = $derived(
		tourRead.phase !== 'idle' && tourRead.phase !== 'loading' && flowRead.phase !== 'idle' && flowRead.phase !== 'loading'
	);
	const preferredRoute = $derived(tour ?? flow ?? (initialReadsSettled ? derivedRouteList[0] ?? null : null));
	const selectedRouteHead = $derived(
		routes.find((route) => route.id === selectedRouteId) ?? (selectedRouteId === null ? preferredRoute : null)
	);
	const selectedRoute = $derived(
		map.data && selectedRouteHead?.kind === 'derived'
			? walkDerivedRoute(map.data, selectedRouteHead)
			: selectedRouteHead
	);
	const resolutions = $derived(map.data ? summarizeEdges(map.data.edges) : { static: 0, dynamic: 0, external: 0 });
	const selectedRef = $derived(selectedSymbol ? symbolRef(selectedSymbol) : null);
	const selectedEdges = $derived(
		selectedRef && map.data
			? map.data.edges.filter((edge) => edge.src === selectedRef || edge.dst === selectedRef)
			: []
	);
	const selectedSummary = $derived(summarizeEdges(selectedEdges));
	const unresolved = $derived(
		selectedRef && map.data ? map.data.unresolved.filter((edge) => edge.src === selectedRef) : []
	);

	$effect(() => {
		if (selectedRouteId === null && selectedRouteHead) selectedRouteId = selectedRouteHead.id;
	});

	function reload(): void {
		void map.load();
		void tourRead.load();
		void flowRead.load();
	}

	function selectRoute(route: Route): void {
		selectedRouteId = route.id;
		selectedSymbol = null;
	}

	function selectCitation(ref: string | null): void {
		if (!map.data) return;
		const symbol = symbolForCitation(map.data, ref);
		if (symbol) selectedSymbol = symbol;
	}

	function selectStop(stop: RouteStop): void {
		if (stop.symbol) selectedSymbol = stop.symbol;
	}

	function selectSymbol(symbol: NavSymbol): void {
		selectedSymbol = symbol;
	}

	function source(file: string, line: number): string {
		return `${file}:${line}`;
	}

	onMount(() => {
		reload();
		return () => {
			map.dispose();
			tourRead.dispose();
			flowRead.dispose();
		};
	});
</script>

<section class="map" data-r13="map" data-r14="tour-flow" aria-labelledby="map-route-label">
	<p id="map-route-label" class="rule-label label"><span>Route</span><span class="rule"></span><span>repository evidence</span></p>
	<p class="prose intro">Walk an entry point to the evidence it reaches. Authored facts and walked edges remain separate, so this sheet names where a claim stops being provable.</p>

	<AsyncField resource={map} reading="the repository map" onretry={reload}>
		{#snippet children(index: NavIndex)}
			<div class="readout" aria-label="Repository measurements">
				<div><span class="label">Structure</span><strong>{index.symbols.length.toLocaleString()} symbols · {index.edges.length.toLocaleString()} edges</strong></div>
				<div><span class="label">Extent</span><strong>{index.files.length.toLocaleString()} files · {new Set(index.symbols.map((symbol) => symbol.module)).size.toLocaleString()} modules</strong></div>
				<div><span class="label">Resolution</span><strong>{resolutions.static.toLocaleString()} static · {resolutions.dynamic.toLocaleString()} dynamic · {resolutions.external.toLocaleString()} external</strong></div>
				<div><span class="label">Stated absence</span><strong class="quiet-reading">Repository ref, fingerprint, deep probe, and deep conflicts are unstamped or unavailable in this read.</strong></div>
			</div>

			<label class="find">
				<span class="label">Filter routes and symbols</span>
				<input class="plate" bind:value={query} type="search" placeholder="Filter the route rail…" aria-label="Filter map routes and symbols" />
			</label>

			<div class="route-layout">
				<aside class="route-apparatus">
					<nav class="route-rail" aria-label="Repository routes">
				<section>
					<p class="rail-label label"><span>Curated route</span><span class="rail-rule"></span><span>{tour ? (filtered.some((route) => route.id === tour.id) ? 'authored' : 'filtered') : 'absent'}</span></p>
					{#if tour && filtered.some((route) => route.id === tour.id)}
						<button class:current={selectedRoute?.id === tour.id} class="route-choice" type="button" onclick={() => selectRoute(tour)}><span>{tour.label}</span><small>{tour.stops.length} stops · authored facts</small></button>
					{:else if !tour}
						<p class="rail-notice">No curated tour is available for this repository; derived routes remain readable from structural evidence.</p>
					{/if}
				</section>
				<section>
					<p class="rail-label label"><span>Curated flow</span><span class="rail-rule"></span><span>{flow ? (filtered.some((route) => route.id === flow.id) ? 'authored' : 'filtered') : 'absent'}</span></p>
					{#if flow && filtered.some((route) => route.id === flow.id)}
						<button class:current={selectedRoute?.id === flow.id} class="route-choice" type="button" onclick={() => selectRoute(flow)}><span>{flow.label}</span><small>{flow.source} · authored facts</small></button>
					{:else if !flow}
						<p class="rail-notice">No curated flow answered. The structural route heads are still available below.</p>
					{/if}
				</section>
				<section class="derived-rail">
					<p class="rail-label label"><span>Derived routes</span><span class="rail-rule"></span><span>{filtered.filter((route) => route.kind === 'derived').length} of {routes.filter((route) => route.kind === 'derived').length}</span></p>
					<div class="route-wrap">
						{#each filtered.filter((route) => route.kind === 'derived') as route (route.id)}
							<button class:current={selectedRoute?.id === route.id} class="route-choice" type="button" onclick={() => selectRoute(route)}><span>{route.label}</span><small>{route.source} · structural</small></button>
						{/each}
					</div>
					{#if filtered.length === 0 && symbols.length === 0}<p class="rail-notice">No route or indexed symbol matches this filter. The map still holds {routes.filter((route) => route.kind === 'derived').length} derived route heads.</p>{/if}
				</section>
			</nav>

			<details class="symbol-register" bind:open={registerOpen}>
				<summary>Symbol register · {symbols.length.toLocaleString()} of {index.symbols.length.toLocaleString()}</summary>
				{#if registerOpen && symbols.length === 0}
					<p class="rail-notice">No indexed symbol matches this filter. The route rail remains available above.</p>
				{:else if registerOpen}
					<ul>
						{#each symbols as symbol (`${symbol.file}:${symbol.line}:${symbol.name}`)}
							<li><button type="button" onclick={() => selectSymbol(symbol)}><span>{symbol.name}</span><small>{source(symbol.file, symbol.line)} · {symbol.kind}</small></button></li>
						{/each}
					</ul>
				{/if}
			</details>
				</aside>

				<div class="route-sheet">
			<p class="route-state" aria-live="polite">
				{#if selectedRoute}{selectedRoute.kind === 'derived' ? 'Walked structural route. Each hop carries its own resolution.' : 'Authored route. Facts and checkpoints are cited from the daemon response.'}{:else}No route is selected because this filter has no match.{/if}
			</p>

			{#if selectedRoute}
				<section class="reading" aria-label="Selected route">
					<p class="rule-label label"><span>{selectedRoute.kind === 'derived' ? 'Derived route' : 'Curated route'}</span><span class="rule"></span><span>{selectedRoute.source}</span></p>
					<h2>{selectedRoute.label}</h2>
					<p class="prose route-proof">{selectedRoute.kind === 'derived' ? 'This is a structural walk from one entry-point head. It follows calls and constructions only; it does not claim type-inferred or complete reachability.' : 'These authored facts teach this repository’s own load path. Their citations remain separate from structural resolution.'}</p>

					<ol class="member-chain">
						{#each selectedRoute.stops as stop (stop.id)}
							<li class:terminal={stop.terminal} style:--depth={stop.depth}>
								{#if stop.symbol}<button class="seat" type="button" onclick={() => selectStop(stop)} aria-label={`Read ${stop.label}`}></button>{:else}<span class="seat" aria-hidden="true"></span>{/if}
								<div class="stop-body">
									{#if stop.symbol}<button class="stop-name" type="button" onclick={() => selectStop(stop)}>{stop.label}</button>{:else}<span class="stop-name">{stop.label}</span>{/if}
									{#if stop.resolution}<span class="resolution {stop.resolution}">{stop.resolution}</span>{/if}
									{#if stop.fact}<p class="fact"><span class="label">Fact</span>{stop.fact}</p>{/if}
									{#if stop.citations.length > 0}
										<div class="citations">
											{#each stop.citations as cite (`${cite.file}:${cite.line}:${cite.ref}`)}
												<button type="button" class="citation" onclick={() => selectCitation(cite.ref)} disabled={!symbolForCitation(index, cite.ref)}>{cite.file}:{cite.line}</button>
											{/each}
										</div>
									{/if}
									{#if stop.checkpoint}<p class="checkpoint"><span class="label">Checkpoint</span><span>{stop.checkpoint}</span></p>{/if}
									{#if stop.reason}<p class="terminal-note">{stop.reason}</p>{/if}
								</div>
							</li>
						{/each}
					</ol>
				</section>
			{/if}

			{#if selectedSymbol}
				<section class="symbol-detail" aria-labelledby="symbol-detail-title">
					<p class="rule-label label"><span>Symbol explorer</span><span class="rule"></span><span>route remains open</span></p>
					<h2 id="symbol-detail-title">{selectedSymbol.name}</h2>
					<p class="source"><code>{source(selectedSymbol.file, selectedSymbol.line)}</code> · {selectedSymbol.kind}</p>
					<dl class="symbol-readout">
						<div><dt class="label">Signature</dt><dd><code>{selectedSymbol.signature || '—'}</code></dd></div>
						<div><dt class="label">Returns</dt><dd>{selectedSymbol.returns || '—'}</dd></div>
						<div><dt class="label">Edges</dt><dd>{selectedEdges.length.toLocaleString()}</dd></div>
						<div><dt class="label">Unresolved</dt><dd>{unresolved.length.toLocaleString()}</dd></div>
					</dl>
					<p class="edge-summary">Resolution before listing: {selectedSummary.static.toLocaleString()} static · {selectedSummary.dynamic.toLocaleString()} dynamic · {selectedSummary.external.toLocaleString()} external.</p>
					<details>
						<summary>Show {selectedEdges.length.toLocaleString()} indexed edge{selectedEdges.length === 1 ? '' : 's'}</summary>
						<ul class="edge-list">
							{#each selectedEdges as edge (`${edge.src}:${edge.dst}:${edge.file}:${edge.line}:${edge.kind}:${edge.resolution}`)}
								<li><code>{edge.src === selectedRef ? '→' : '←'} {edge.src === selectedRef ? edge.dst : edge.src}</code><span class="resolution {edge.resolution}">{edge.resolution}</span><small>{edge.kind} · {source(edge.file, edge.line)}</small></li>
							{/each}
						</ul>
					</details>
					{#if unresolved.length > 0}
						<section class="unresolved">
							<p class="rule-label label"><span>Unresolved evidence</span><span class="rule"></span><span>{unresolved.length}</span></p>
							<ul class="edge-list">
								{#each unresolved as edge (`${edge.name}:${edge.file}:${edge.line}`)}
									<li><code>{edge.name}</code><span class="resolution unresolved">unresolved</span><small>{edge.kind} · {source(edge.file, edge.line)}</small></li>
								{/each}
							</ul>
						</section>
					{/if}
				</section>
			{/if}
				</div>
			</div>
		{/snippet}
	</AsyncField>
</section>

<style>
	.map { min-width: 0; }
	.intro { margin: 0 0 1.5rem; }
	.readout { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 0 0 1.5rem; background: var(--rule); border: 1px solid var(--rule); }
	.readout > div { min-width: 0; padding: 0.75rem 0.9rem; background: var(--plate); }
	.readout strong { display: block; margin-top: 0.3rem; font-size: 0.8125rem; font-weight: 500; overflow-wrap: anywhere; }
	.quiet-reading { color: var(--ink-2); font-weight: 400 !important; }
	.find { display: block; max-width: 42rem; margin-bottom: 1.75rem; }
	.find input { display: block; width: 100%; margin-top: 0.35rem; padding: 0.45rem 0.7rem; font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); }
	.find input:focus { border-color: var(--red); outline: 2px solid var(--red); outline-offset: 2px; }
	.route-layout { display: grid; grid-template-columns: minmax(15rem, 0.7fr) minmax(0, 1.7fr); gap: 2.5rem; align-items: start; }
	.route-apparatus, .route-sheet { min-width: 0; }
	.route-rail { padding: 1.25rem 0 1.5rem; border-top: 1px solid var(--rule-strong); border-bottom: 1px solid var(--rule-strong); }
	.route-rail section + section { margin-top: 1.25rem; }
	.rail-label, .rule-label { display: flex; align-items: center; gap: 0.5rem; margin: 0 0 0.55rem; }
	.rail-rule, .rule { flex: 1; min-width: 1rem; height: 1px; background: var(--rule); }
	.route-wrap { display: flex; flex-wrap: wrap; gap: 0.3rem 1.25rem; }
	.route-choice { display: inline-flex; flex-direction: column; align-items: flex-start; max-width: 100%; padding: 0.2rem 0; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--rule); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
	.route-choice span { font-size: 0.8125rem; font-weight: 500; }
	.route-choice small { color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.04em; }
	.route-choice:hover, .route-choice:focus-visible { color: var(--red); }
	.route-choice.current { background: var(--red-quiet); color: var(--ink); border-color: var(--member-line); }
	.route-choice.current small { color: var(--ink-2); }
	.rail-notice, .route-state, .route-proof, .terminal-note, .edge-summary { max-width: 68ch; color: var(--ink-2); }
	.rail-notice { margin: 0; font-size: 0.8125rem; }
	.symbol-register { margin-top: 1.25rem; }
	.symbol-register ul { display: flex; flex-wrap: wrap; gap: 0.3rem 1.25rem; margin: 0.75rem 0 0; padding: 0; list-style: none; }
	.symbol-register button { display: inline-flex; flex-direction: column; align-items: flex-start; padding: 0.2rem 0; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--rule); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
	.symbol-register button:hover, .symbol-register button:focus-visible { color: var(--red); }
	.symbol-register button span { font-size: 0.8125rem; font-weight: 500; }
	.symbol-register button small { color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.04em; }
	.route-state { margin: 1rem 0 0; font-size: 0.8125rem; text-decoration: underline dashed var(--ash); text-underline-offset: 0.3em; }
	.reading, .symbol-detail { margin-top: 2.5rem; }
	.reading h2, .symbol-detail h2 { margin: 0 0 0.65rem; font-family: 'Archivo', ui-sans-serif, sans-serif; font-size: 2rem; font-weight: 620; font-variation-settings: 'wdth' 70, 'wght' 620; line-height: 1; overflow-wrap: anywhere; }
	.route-proof { margin: 0 0 1.75rem; }
	.member-chain { position: relative; margin: 0; padding: 0; list-style: none; }
	.member-chain::before { content: ''; position: absolute; top: 0.35rem; bottom: -0.75rem; left: 0.35rem; width: 1px; background: var(--member-line); }
	.member-chain li { position: relative; display: grid; grid-template-columns: 1.5rem minmax(0, 1fr); gap: 0.75rem; min-height: 2rem; margin-left: calc(var(--depth) * 0.85rem); padding-bottom: 1.15rem; }
	.seat { z-index: 1; width: 11px; height: 11px; margin-top: 0.3rem; padding: 0; border: 1.5px solid var(--member-line); border-radius: 50%; background: var(--plate); }
	button.seat { cursor: pointer; }
	.terminal .seat { border-style: dashed; border-color: var(--ink-2); }
	button.seat:hover, button.seat:focus-visible { border-color: var(--red); }
	.stop-body { min-width: 0; }
	.stop-name { padding: 0; color: var(--ink); background: var(--plate); border: 0; font: inherit; font-size: 0.875rem; font-weight: 500; text-align: left; overflow-wrap: anywhere; }
	button.stop-name { cursor: pointer; }
	button.stop-name:hover, button.stop-name:focus-visible { color: var(--red); }
	.resolution { display: inline-block; margin-left: 0.5rem; color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.12em; text-transform: uppercase; }
	.resolution.static { color: var(--ink); }
	.resolution.dynamic, .resolution.external, .resolution.unresolved { text-decoration: underline dashed var(--ash); text-underline-offset: 0.3em; }
	.fact { max-width: 68ch; margin: 0.35rem 0 0; color: var(--seat); }
	.fact .label { display: inline-block; margin-right: 0.5rem; color: var(--ink-2); }
	.citations { display: flex; flex-wrap: wrap; gap: 0.35rem 1rem; margin-top: 0.45rem; }
	.citation { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0; color: var(--ink-2); background: transparent; border: 0; font: inherit; font-size: 0.75rem; cursor: pointer; overflow-wrap: anywhere; }
	.citation::before { content: ''; width: 1.25rem; height: 1px; background: var(--rule-strong); }
	.citation:not(:disabled):hover, .citation:not(:disabled):focus-visible { color: var(--red); }
	.citation:disabled { cursor: default; }
	.checkpoint { display: flex; align-items: baseline; gap: 0.75rem; max-width: 68ch; margin: 0.65rem 0 0; padding-top: 0.45rem; border-top: 1px solid var(--rule); color: var(--ink-2); }
	.checkpoint .label { flex: none; }

	.terminal-note { margin: 0.45rem 0 0; font-size: 0.8125rem; text-decoration: underline dashed var(--ash); text-underline-offset: 0.3em; }
	.symbol-detail { padding-top: 1.5rem; border-top: 1px solid var(--rule-strong); }
	.source { margin: 0 0 1rem; color: var(--ink-2); }
	.symbol-readout { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 0; background: var(--rule); border: 1px solid var(--rule); }
	.symbol-readout > div { min-width: 0; padding: 0.65rem 0.8rem; background: var(--plate); }
	.symbol-readout dt { margin-bottom: 0.25rem; }
	.symbol-readout dd { margin: 0; overflow-wrap: anywhere; }
	.edge-summary { margin: 1rem 0 0.6rem; }
	details { border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 0.55rem 0; }
	summary { color: var(--ink); cursor: pointer; }
	.edge-list { margin: 0.75rem 0 0; padding: 0; list-style: none; }
	.edge-list li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.25rem 0.75rem; padding: 0.45rem 0; border-top: 1px solid var(--rule); }
	.edge-list code { min-width: 0; overflow-wrap: anywhere; white-space: normal; }
	.edge-list small { grid-column: 1 / -1; color: var(--ink-2); }
	.unresolved { margin-top: 1.5rem; }
	@media (max-width: 60rem) {
		.route-layout { grid-template-columns: 1fr; gap: 1.75rem; }
		.route-sheet { order: -1; }
		.readout, .symbol-readout { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.member-chain li { margin-left: calc(var(--depth) * 0.45rem); }
	}
	@media (max-width: 38rem) {
		.readout, .symbol-readout { grid-template-columns: 1fr; }
		.route-wrap { gap: 0.3rem 0.85rem; }
		.checkpoint { display: block; }
		.checkpoint .label { display: block; margin-bottom: 0.3rem; }
	}
</style>
