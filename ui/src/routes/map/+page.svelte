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
		moduleGraph,
		parseFlow,
		parseTour,
		summarizeEdges,
		symbolForCitation,
		symbolRef,
		walkDerivedRoute,
		type ModuleNode,
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
	let selectedModule = $state<string | null>(null);
	let combBox: HTMLDivElement | null = $state(null);
	let comb: HTMLDivElement | null = $state(null);
	let combOverflows = $state(false);

	const ENTRY_CAP = 80;
	const SYMBOL_CAP = 60;
	const ENTRY_CLASSES = [
		{ key: 'script', label: 'Console script', noun: 'console script' },
		{ key: 'cli', label: 'CLI commands', noun: 'command head' },
		{ key: 'route', label: 'Daemon routes', noun: 'route head' },
		{ key: 'test', label: 'Tests', noun: 'test head' }
	] as const;

	const tour = $derived(parseTour(tourRead.data?.text));
	const flow = $derived(parseFlow(flowRead.data?.text, 'create-approve-run-settle'));
	const derivedRouteList = $derived(map.data ? derivedRoutes(map.data) : []);
	const curated = $derived(filterRoutes([...(tour ? [tour] : []), ...(flow ? [flow] : [])], query));
	const routes = $derived([...(tour ? [tour] : []), ...(flow ? [flow] : []), ...derivedRouteList]);
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

	/* --- Architecture: the module rank comb and its ledger ------------------- */
	const modules = $derived(map.data ? moduleGraph(map.data) : []);
	const rankList = $derived.by(() => {
		const buckets: ModuleNode[][] = [];
		for (const node of modules) (buckets[node.rank] ??= []).push(node);
		return buckets;
	});
	const rowCount = $derived(Math.max(0, ...rankList.map((rank) => rank.length)));
	const byModule = $derived(new Map(modules.map((node) => [node.module, node])));
	const importEdgeCount = $derived(
		map.data ? map.data.edges.filter((edge) => edge.kind === 'imports').length : 0
	);
	const selectedNode = $derived(selectedModule !== null ? byModule.get(selectedModule) ?? null : null);

	/* A module matches the find by its own name or by owning a matched symbol.
	   The comb never re-lays-out under a filter — matching only dims labels. */
	const moduleMatches = $derived.by(() => {
		if (!map.data) return null;
		const needle = query.trim().toLowerCase();
		if (!needle) return null;
		const matched = new Set<string>();
		for (const symbol of map.data.symbols)
			if (`${symbol.name} ${symbol.file} ${symbol.signature}`.toLowerCase().includes(needle))
				matched.add(symbol.module);
		for (const node of modules) if (node.module.toLowerCase().includes(needle)) matched.add(node.module);
		return matched;
	});

	const slotOf = (node: ModuleNode): number => (rankList[node.rank] ?? []).indexOf(node);

	const anchorModule = $derived(
		selectedModule !== null && byModule.has(selectedModule) ? selectedModule : modules[0]?.module ?? null
	);

	/* entries outrank unresolved, so a module with both draws balanced; the
	   ledger still names the unresolved count. */
	const stateOf = (node: ModuleNode): string =>
		node.entries > 0 ? 'balanced' : node.unresolved > 0 ? 'slack' : 'held';

	const ringX = (node: ModuleNode): number => node.rank + 0.5;
	const ringY = (node: ModuleNode): number => slotOf(node) + 0.5;

	/* The drawing carries shape; the ledger and the aria-label carry the full
	   name, so the mark abbreviates to the segment that fits beside its ring. */
	const markOf = (module: string): string => module.split('.').at(-1) ?? module;

	/** Horizontal out of `a`, then down into `b`'s column — the Contention Field elbow, on its side. */
	const elbowPath = (a: ModuleNode, b: ModuleNode): string =>
		a.rank === b.rank
			? `M${ringX(a)} ${ringY(a)}V${ringY(b)}`
			: `M${ringX(a)} ${ringY(a)}H${ringX(b) - 0.42}V${ringY(b)}H${ringX(b)}`;

	function describe(node: ModuleNode): string {
		return [
			node.module,
			`rank ${node.rank}`,
			`${node.symbols} symbols`,
			`imports ${node.out.length} modules`,
			`imported by ${node.in.length}`,
			node.entries ? `${node.entries} declared entry points` : '',
			`${node.unresolved} unresolved`
		]
			.filter(Boolean)
			.join(', ');
	}

	/* The one tab stop into the drawing. It must never be the selected module
	   alone: a re-read that drops it would leave the comb with no tabbable
	   ring, which is a keyboard trap. */
	function oncombkeydown(event: KeyboardEvent, node: ModuleNode): void {
		if (event.altKey || event.ctrlKey || event.metaKey) return;
		const rank = rankList[node.rank] ?? [];
		const at = rank.indexOf(node);
		let next: ModuleNode | undefined;
		if (event.key === 'ArrowUp') next = rank[at - 1];
		else if (event.key === 'ArrowDown') next = rank[at + 1];
		else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
			const target = rankList[node.rank + (event.key === 'ArrowRight' ? 1 : -1)];
			if (target)
				next = target.reduce((best, item) =>
					Math.abs(target.indexOf(item) - at) < Math.abs(target.indexOf(best) - at) ? item : best
				);
		} else if (event.key === 'Home') next = modules[0];
		else if (event.key === 'End') next = modules.at(-1);
		else return;
		event.preventDefault();
		if (!next) return;
		selectModule(next.module);
		document.getElementById(`mod-${next.module}`)?.focus();
	}

	/* The scroll edge fade is measured, never assumed: a fade that is always on
	   lies about scrollable content when everything fits. Reading modules.length
	   keeps the measurement live for the async read that fills the comb. */
	$effect(() => {
		void modules.length;
		if (!combBox) return;
		const measure = () => {
			combOverflows = combBox !== null && combBox.scrollWidth > combBox.clientWidth;
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(combBox);
		if (comb) observer.observe(comb);
		return () => observer.disconnect();
	});

	/* --- Entry points, grouped by the class they were declared in ------------ */
	const entryClasses = $derived(
		ENTRY_CLASSES.map((cls) => {
			const all = derivedRouteList.filter((route) => route.id.split(':')[1] === cls.key);
			const matched = filterRoutes(all, query);
			return { ...cls, total: all.length, matched, shown: matched.slice(0, ENTRY_CAP) };
		})
	);

	/* --- Symbols, driven by the find and the module scope --------------------- */
	const symbolsActive = $derived(query.trim() !== '' || selectedModule !== null);
	const symbolPool = $derived(
		map.data
			? query.trim() !== ''
				? filterSymbols(map.data.symbols, query)
				: map.data.symbols.filter((symbol) => symbol.module === selectedModule)
			: []
	);
	const symbolShown = $derived(symbolPool.slice(0, SYMBOL_CAP));
	const symbolGroups = $derived.by(() => {
		const groups = new Map<string, NavSymbol[]>();
		for (const symbol of symbolShown) {
			const list = groups.get(symbol.module) ?? [];
			list.push(symbol);
			groups.set(symbol.module, list);
		}
		return [...groups.entries()].map(([module, list]) => ({ module, symbols: list }));
	});

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

	function selectModule(module: string): void {
		selectedModule = module;
		document.getElementById(`mod-${module}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
	}

	function selectCitation(ref: string | null): void {
		if (!map.data) return;
		const symbol = symbolForCitation(map.data, ref);
		if (symbol) {
			selectedSymbol = symbol;
			selectedModule = symbol.module;
		}
	}

	function selectStop(stop: RouteStop): void {
		if (stop.symbol) {
			selectedSymbol = stop.symbol;
			selectedModule = stop.symbol.module;
		}
	}

	function selectSymbol(symbol: NavSymbol): void {
		selectedSymbol = symbol;
		selectedModule = symbol.module;
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
			<dl class="readout" aria-label="Repository measurements">
				<div><dt class="label">Structure</dt><dd>{index.symbols.length.toLocaleString()} symbols · {index.edges.length.toLocaleString()} edges</dd></div>
				<div><dt class="label">Extent</dt><dd>{index.files.length.toLocaleString()} files · {new Set(index.symbols.map((symbol) => symbol.module)).size.toLocaleString()} modules</dd></div>
				<div><dt class="label">Resolution</dt><dd>{resolutions.static.toLocaleString()} static · {resolutions.dynamic.toLocaleString()} dynamic · {resolutions.external.toLocaleString()} external</dd></div>
				<div><dt class="label">Entry points</dt><dd>{index.entry_points.console_script ? 1 : 0} script · {index.entry_points.cli.length.toLocaleString()} commands · {index.entry_points.routes.length.toLocaleString()} routes · {index.entry_points.tests.length.toLocaleString()} tests</dd></div>
			</dl>
			{#if !index.repo_ref || !index.fingerprint || !index.coverage.deep}
				<p class="unstamped">Repository ref, fingerprint, and deep probe are unstamped or unavailable in this read.</p>
			{/if}

			<label class="find">
				<span class="label">Find modules, entry points and symbols</span>
				<input class="plate" bind:value={query} type="search" placeholder="Find the repository…" aria-label="Find modules, entry points and symbols" />
			</label>

			<section class="architecture" aria-labelledby="architecture-label">
				<p id="architecture-label" class="rule-label label">
					<span>Architecture</span><span class="rule"></span>
					<span>{moduleMatches ? `${moduleMatches.size} of ${modules.length} modules match` : `${modules.length} modules · ${importEdgeCount.toLocaleString()} import edges`}</span>
				</p>
				{#if importEdgeCount === 0 || modules.length === 0}
					<p class="absent">No import edge is indexed, so no module dependency can be drawn. The ledger still names every module the symbols declare.</p>
				{:else}
					<div class="comb-scroll" class:scrollable={combOverflows} bind:this={combBox}>
						<div
							bind:this={comb}
							class="comb"
							style="--ranks: {rankList.length}; --rows: {rowCount}"
							role="group"
							aria-label="Module rank comb: {modules.length} modules in {rankList.length} dependency ranks. Arrow keys move between modules."
						>
							<div class="plot" aria-hidden="true">
								<svg viewBox="0 0 {rankList.length} {rowCount}" preserveAspectRatio="none">
									{#each rankList as rank, r (r)}
										{#if rank.length > 0}
											<line
												class="run"
												x1={r + 0.5}
												y1={slotOf(rank[0]) + 0.5 - 0.2}
												x2={r + 0.5}
												y2={slotOf(rank.at(-1)!) + 0.5 + 0.2}
											/>
										{/if}
									{/each}
									{#each Array(rankList.length - 1) as _, boundary (boundary)}
										<line class="ruling" x1={boundary + 1} y1="0" x2={boundary + 1} y2={rowCount} />
									{/each}
									{#if selectedNode}
										{#each selectedNode.out as target (target)}
											{#if byModule.has(target)}
												<path class="cord imports" d={elbowPath(selectedNode, byModule.get(target)!)}>
													<title>{selectedNode.module} imports {target}</title>
												</path>
											{/if}
										{/each}
										{#each selectedNode.in as importer (importer)}
											{#if byModule.has(importer)}
												<path class="cord imported-by" d={elbowPath(byModule.get(importer)!, selectedNode)}>
													<title>{importer} imports {selectedNode.module}</title>
												</path>
											{/if}
										{/each}
									{/if}
								</svg>
							</div>
							{#each rankList as rank, r (r)}
								<p class="rank" style="grid-row: 1; grid-column: {r + 1}">Rank {r}</p>
								{#each rank as node (node.module)}
									<button
										id="mod-{node.module}"
										class="seat member"
										data-state={stateOf(node)}
										style="grid-column: {r + 1}; grid-row: {slotOf(node) + 2}"
										type="button"
										tabindex={node.module === anchorModule ? 0 : -1}
										aria-current={selectedModule === node.module ? 'true' : undefined}
										aria-label={describe(node)}
										onclick={() => selectModule(node.module)}
										onkeydown={(event) => oncombkeydown(event, node)}
									>
										<span class="ring" aria-hidden="true"></span>
										<span class="mark" class:dim={moduleMatches !== null && !moduleMatches.has(node.module)}>{markOf(node.module)}</span>
									</button>
								{/each}
							{/each}
						</div>
					</div>
					<p class="narrow-note">
						At this width the comb draws shape only — the rings carry rank and state, not
						names. Selecting one marks it here and names it in the ledger below.
					</p>
				{/if}
				<ol class="ledger" aria-label="Module ledger">
					{#each modules as node (node.module)}
						<li class:current={selectedModule === node.module} aria-current={selectedModule === node.module ? 'true' : undefined}>
							<ul class="dims">
								<li class="name"><button type="button" class:dim={moduleMatches !== null && !moduleMatches.has(node.module)} onclick={() => selectModule(node.module)}>{node.module}</button></li>
								<li><span class="label">Rank</span><span>{node.rank}</span></li>
								<li><span class="label">Symbols</span><span>{node.symbols.toLocaleString()}</span></li>
								<li><span class="label">Imports</span><span>{node.out.length.toLocaleString()}</span></li>
								<li><span class="label">Imported by</span><span>{node.in.length.toLocaleString()}</span></li>
								<li><span class="label">Entry points</span><span class:held={node.entries > 0}>{node.entries.toLocaleString()}</span></li>
								<li><span class="label">Unresolved</span><span class:slack-reading={node.unresolved > 0}>{node.unresolved.toLocaleString()}</span></li>
							</ul>
						</li>
					{/each}
				</ol>
			</section>

			{#each entryClasses as cls (cls.key)}
				<section class="entry-class" aria-labelledby="entry-{cls.key}">
					<p id="entry-{cls.key}" class="rule-label label"><span>{cls.label}</span><span class="rule"></span><span>{cls.matched.length} of {cls.total}</span></p>
					{#if cls.key === 'test' && cls.matched.length > 0}
						<details class="entry-fold">
							<summary>{cls.matched.length} declared test head{cls.matched.length === 1 ? '' : 's'}{query.trim() ? ` match this find` : ''}</summary>
							<ul class="entry-list">
								{#each cls.shown as route (route.id)}
									<li><button type="button" class:current={selectedRoute?.id === route.id} aria-current={selectedRoute?.id === route.id ? 'true' : undefined} onclick={() => selectRoute(route)}><span>{route.label}</span><small>{route.source}</small></button></li>
								{/each}
							</ul>
						</details>
					{:else if cls.matched.length > 0}
						<ul class="entry-list">
							{#each cls.shown as route (route.id)}
								<li><button type="button" class:current={selectedRoute?.id === route.id} aria-current={selectedRoute?.id === route.id ? 'true' : undefined} onclick={() => selectRoute(route)}><span>{route.label}</span><small>{route.source}</small></button></li>
							{/each}
						</ul>
					{/if}
					{#if cls.matched.length === 0}
						<p class="absent">No {cls.noun} matches this find. The other entry-point classes remain grouped above.</p>
					{:else if cls.matched.length > cls.shown.length}
						<p class="absent">Showing {cls.shown.length} of {cls.matched.length} declared {cls.noun}s; narrow the find to reach the rest.</p>
					{/if}
				</section>
			{/each}

			{#if symbolsActive && index}
				<section class="symbols" aria-labelledby="symbols-label">
					<p id="symbols-label" class="rule-label label">
						<span>Symbols</span><span class="rule"></span>
						<span>{query.trim() ? `${symbolPool.length.toLocaleString()} of ${index.symbols.length.toLocaleString()} indexed` : selectedModule ?? ''}</span>
					</p>
					{#if symbolPool.length === 0}
						<p class="absent">{query.trim() ? `No indexed symbol matches this find. The comb still draws ${modules.length} modules.` : `${selectedModule} declares no indexed symbol.`}</p>
					{:else}
						{#each symbolGroups as group (group.module)}
							<section class="symbol-group">
								<p class="rail-label label"><span>{group.module}</span><span class="rail-rule"></span><span>{group.symbols.length.toLocaleString()}</span></p>
								<ul class="symbol-rows">
									{#each group.symbols as symbol (`${symbol.file}:${symbol.line}:${symbol.name}`)}
										<li><button type="button" class:current={selectedSymbol === symbol} aria-current={selectedSymbol === symbol ? 'true' : undefined} onclick={() => selectSymbol(symbol)}><span>{symbol.name}</span>{#if symbol.signature}<code>{symbol.signature}</code>{/if}<small>{source(symbol.file, symbol.line)} · {symbol.kind}</small></button></li>
									{/each}
								</ul>
							</section>
						{/each}
						{#if symbolPool.length > symbolShown.length}
							<p class="absent">Showing {SYMBOL_CAP} of {symbolPool.length.toLocaleString()} indexed symbols; narrow the find to reach the rest.</p>
						{/if}
					{/if}
				</section>
			{/if}

			<div class="route-layout">
				<aside class="route-apparatus">
					<nav class="route-rail" aria-label="Repository routes">
						<section>
							<p class="rail-label label"><span>Curated route</span><span class="rail-rule"></span><span>{tour ? (curated.some((route) => route.id === tour.id) ? 'authored' : 'filtered') : 'absent'}</span></p>
							{#if tour && curated.some((route) => route.id === tour.id)}
								<button class:current={selectedRoute?.id === tour.id} class="route-choice" type="button" onclick={() => selectRoute(tour)}><span>{tour.label}</span><small>{tour.stops.length} stops · authored facts</small></button>
							{:else if !tour}
								<p class="rail-notice">No curated tour is available for this repository; derived routes remain readable from structural evidence.</p>
							{/if}
						</section>
						<section>
							<p class="rail-label label"><span>Curated flow</span><span class="rail-rule"></span><span>{flow ? (curated.some((route) => route.id === flow.id) ? 'authored' : 'filtered') : 'absent'}</span></p>
							{#if flow && curated.some((route) => route.id === flow.id)}
								<button class:current={selectedRoute?.id === flow.id} class="route-choice" type="button" onclick={() => selectRoute(flow)}><span>{flow.label}</span><small>{flow.source} · authored facts</small></button>
							{:else if !flow}
								<p class="rail-notice">No curated flow answered. The entry-point classes remain grouped above.</p>
							{/if}
						</section>
					</nav>
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
	.readout { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 0 0 0.75rem; background: var(--rule); border: 1px solid var(--rule); }
	.readout > div { min-width: 0; padding: 0.75rem 0.9rem; background: var(--plate); }
	.readout dt { margin: 0 0 0.3rem; }
	.readout dd { margin: 0; font-size: 0.8125rem; font-weight: 500; overflow-wrap: anywhere; }
	.unstamped { max-width: 68ch; margin: 0 0 1.5rem; color: var(--ink-2); font-size: 0.8125rem; text-decoration: underline dashed var(--ash); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }
	.find { display: block; max-width: 42rem; margin-bottom: 1.75rem; }
	.find input { display: block; width: 100%; margin-top: 0.35rem; padding: 0.45rem 0.7rem; font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); }
	.find input:focus { border-color: var(--red); outline: 2px solid var(--red); outline-offset: 2px; }

	.architecture { margin: 0 0 2.5rem; }
	.rule-label, .rail-label { display: flex; align-items: center; gap: 0.5rem; margin: 0 0 0.55rem; }
	.rail-rule, .rule { flex: 1; min-width: 1rem; height: 1px; background: var(--rule); }
	.absent { max-width: 68ch; margin: 0.5rem 0 0; color: var(--ink-2); font-size: 0.8125rem; }

	/* The scroll box fades only when the comb really overflows, measured — never assumed. */
	.comb-scroll { overflow-x: auto; overflow-y: hidden; padding-left: 6px; margin-left: -6px; }
	.comb-scroll.scrollable { mask-image: linear-gradient(to right, #000 calc(100% - 2.5rem), transparent); }

	/* One grid rules the whole drawing: the rank headings, every ring and the
	   cord layer land on the same lines. */
	.comb {
		--head: 1.5rem;
		--lane: 2.25rem;
		display: grid;
		grid-template-columns: repeat(var(--ranks), minmax(0, 9rem)) minmax(0, 1fr);
		grid-template-rows: var(--head) repeat(var(--rows), var(--lane));
		position: relative;
		width: max-content;
		min-width: 100%;
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
	}
	.rank { align-self: end; margin: 0; padding-bottom: 0.3rem; border-bottom: 1px solid var(--rule); font-size: 0.625rem; letter-spacing: 0.1em; color: var(--ink-2); text-align: center; }

	.plot { grid-column: 1 / span var(--ranks); grid-row: 2 / -1; position: relative; pointer-events: none; }
	.plot svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }

	/* Stroke widths and dash patterns stay in real pixels under the non-uniform
	   viewBox, so a wide comb does not draw fatter cords than a narrow one. */
	.plot :is(line, path) { fill: none; vector-effect: non-scaling-stroke; stroke-linecap: butt; stroke-linejoin: miter; }
	.ruling { stroke: var(--rule); stroke-width: 1; stroke-dasharray: 1 5; }
	.run { stroke: var(--member-line); stroke-width: 1.25; }
	/* Cords draw only for the selection, so heavy-out / dashed-ash-in carries
	   direction — a deliberate step up from the brief's --rule-strong 1.25px. */
	.cord.imports { stroke: var(--ink); stroke-width: 2.5; }
	.cord.imported-by { stroke: var(--ash); stroke-width: 1.25; stroke-dasharray: 3 3; }

	.seat {
		position: relative;
		z-index: 1;
		min-width: 0;
		padding: 0;
		font: inherit;
		background: none;
		border: 0;
		color: var(--ink-2);
		cursor: pointer;
	}
	.comb .seat[data-state='held'] { color: var(--ink-2); }
	.comb .seat[data-state='balanced'] { color: var(--ink); }
	.comb .seat[data-state='slack'] { color: var(--ash); }
	.ring { position: absolute; top: 50%; left: 50%; width: 11px; height: 11px; margin: -5.5px 0 0 -5.5px; border: 1.5px solid currentColor; border-radius: 50%; background: var(--plate); }
	.comb .seat[data-state='slack'] .ring { border-style: dashed; }
	.comb .seat[data-state='balanced'] .ring::before { content: ''; position: absolute; inset: 2px; border-radius: 50%; background: currentColor; }
	.comb .seat[aria-current='true'] .ring { box-shadow: 0 0 0 3px var(--plate), 0 0 0 4px var(--member-line); }
	.mark { position: absolute; top: 50%; left: 50%; transform: translateY(-50%); max-width: 5.5rem; margin-left: 0.5rem; padding: 0 0.25rem; overflow: hidden; font-size: 0.75rem; font-weight: 500; color: var(--ink); background: var(--plate); text-overflow: ellipsis; white-space: nowrap; }
	.comb .seat[data-state='slack'] .mark { color: var(--ink-2); }
	.comb .seat:hover .mark, .comb .seat:focus-visible .mark { color: var(--red); }
	.mark.dim { color: var(--ink-2); }
	.narrow-note { display: none; max-width: 68ch; margin: 1.25rem 0 0; color: var(--ink-2); font-size: 0.8125rem; }

	.ledger { margin: 1.25rem 0 0; padding: 0; list-style: none; }
	.ledger li { padding: 0.55rem 0; border-top: 1px solid var(--rule); }
	.ledger li:first-child { border-top: 0; }
	.ledger li[aria-current='true'] .name button { color: var(--ink); border-bottom-color: var(--member-line); }
	.dims { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.3rem 0.65rem; margin: 0; padding: 0; list-style: none; }
	.dims li { display: flex; align-items: baseline; gap: 0.4rem; padding: 0; border-top: 0; white-space: nowrap; }
	.dims .label { margin: 0; }
	.dims span:not(.label) { font-size: 0.8125rem; font-weight: 500; color: var(--ink); }
	.dims .held { color: var(--ink); }
	.slack-reading { color: var(--ink-2) !important; text-decoration: underline dashed var(--ash); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }
	.name button.dim { color: var(--ink-2); }
	.name button { padding: 0 0 0.1rem; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--rule); font: inherit; font-size: 0.8125rem; font-weight: 500; cursor: pointer; overflow-wrap: anywhere; }
	.name button:hover, .name button:focus-visible { color: var(--red); border-bottom-color: var(--red); }

	.entry-class { margin: 0 0 2rem; }
	.entry-fold summary { margin: 0 0 0.35rem; color: var(--ink); cursor: pointer; }
	.entry-list { display: flex; flex-wrap: wrap; gap: 0.3rem 1.25rem; margin: 0; padding: 0; list-style: none; }
	.entry-list button { display: inline-flex; flex-direction: column; align-items: flex-start; max-width: 100%; padding: 0.2rem 0; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--rule); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
	.entry-list button span { font-size: 0.8125rem; font-weight: 500; }
	.entry-list button small { color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.04em; }
	.entry-list button:hover, .entry-list button:focus-visible { color: var(--red); }
	.entry-list button.current { border-bottom-color: var(--member-line); }

	.symbols { margin: 0 0 2.5rem; }
	.symbol-group { margin: 1rem 0 0; }
	.symbol-rows { display: flex; flex-direction: column; gap: 0; margin: 0.35rem 0 0; padding: 0; list-style: none; }
	.symbol-rows button { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.3rem 0.75rem; width: 100%; padding: 0.3rem 0; color: var(--ink); background: transparent; border: 0; border-top: 1px solid var(--rule); font: inherit; text-align: left; cursor: pointer; }
	.symbol-rows li:first-child button { border-top: 0; }
	.symbol-rows button span { font-size: 0.8125rem; font-weight: 500; }
	.symbol-rows button code { font-size: 0.75rem; color: var(--ink-2); white-space: nowrap; }
	.symbol-rows button small { margin-left: auto; color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.04em; white-space: nowrap; }
	.symbol-rows button:hover, .symbol-rows button:focus-visible { color: var(--red); }
	.symbol-rows button.current span { text-decoration: underline; text-decoration-color: var(--member-line); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }

	.route-layout { display: grid; grid-template-columns: minmax(15rem, 0.7fr) minmax(0, 1.7fr); gap: 2.5rem; align-items: start; }
	.route-apparatus, .route-sheet { min-width: 0; }
	.route-rail { padding: 1.25rem 0 1.5rem; border-top: 1px solid var(--rule-strong); border-bottom: 1px solid var(--rule-strong); }
	.route-rail section + section { margin-top: 1.25rem; }
	.route-choice { display: inline-flex; flex-direction: column; align-items: flex-start; max-width: 100%; padding: 0.2rem 0; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--rule); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
	.route-choice span { font-size: 0.8125rem; font-weight: 500; }
	.route-choice small { color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.04em; }
	.route-choice:hover, .route-choice:focus-visible { color: var(--red); }
	.route-choice.current { background: var(--red-quiet); color: var(--ink); border-color: var(--member-line); }
	.route-choice.current small { color: var(--ink-2); }
	.rail-notice, .route-state, .route-proof, .terminal-note, .edge-summary { max-width: 68ch; color: var(--ink-2); }
	.rail-notice { margin: 0; font-size: 0.8125rem; }
	.route-state { margin: 1rem 0 0; font-size: 0.8125rem; text-decoration: underline dashed var(--ash); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }
	.reading, .symbol-detail { margin-top: 2.5rem; }
	.reading h2, .symbol-detail h2 { margin: 0 0 0.65rem; font-family: 'Archivo', ui-sans-serif, sans-serif; font-size: 2rem; font-weight: 620; font-variation-settings: 'wdth' 70, 'wght' 620; line-height: 1; overflow-wrap: anywhere; }
	.route-proof { margin: 0 0 1.75rem; }

	.member-chain { position: relative; margin: 0; padding: 0; list-style: none; }
	.member-chain::before { content: ''; position: absolute; top: 0.35rem; bottom: -0.75rem; left: 0.35rem; width: 1px; background: var(--member-line); }
	.member-chain li { position: relative; display: grid; grid-template-columns: 1.5rem minmax(0, 1fr); gap: 0.75rem; min-height: 2rem; margin-left: calc(var(--depth) * 0.85rem); padding-bottom: 1.15rem; }
	.member-chain .seat { z-index: 1; width: 11px; height: 11px; margin-top: 0.3rem; padding: 0; border: 1.5px solid var(--member-line); border-radius: 50%; background: var(--plate); }
	.member-chain button.seat { cursor: pointer; }
	.member-chain .terminal .seat { border-style: dashed; border-color: var(--ink-2); }
	.member-chain button.seat:hover, .member-chain button.seat:focus-visible { border-color: var(--red); }
	.stop-body { min-width: 0; }
	.stop-name { padding: 0; color: var(--ink); background: var(--plate); border: 0; font: inherit; font-size: 0.875rem; font-weight: 500; text-align: left; overflow-wrap: anywhere; }
	button.stop-name { cursor: pointer; }
	button.stop-name:hover, button.stop-name:focus-visible { color: var(--red); }
	.resolution { display: inline-block; margin-left: 0.5rem; color: var(--ink-2); font-size: 0.625rem; letter-spacing: 0.12em; text-transform: uppercase; }
	.resolution.static { color: var(--ink); }
	.resolution.dynamic, .resolution.external, .resolution.unresolved { text-decoration: underline dashed var(--ash); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }
	.fact { max-width: 68ch; margin: 0.35rem 0 0; color: var(--seat); }
	.fact .label { display: inline-block; margin-right: 0.5rem; color: var(--ink-2); }
	.citations { display: flex; flex-wrap: wrap; gap: 0.35rem 1rem; margin-top: 0.45rem; }
	.citation { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0; color: var(--ink-2); background: transparent; border: 0; font: inherit; font-size: 0.75rem; cursor: pointer; overflow-wrap: anywhere; }
	.citation::before { content: ''; width: 1.25rem; height: 1px; background: var(--rule-strong); }
	.citation:not(:disabled):hover, .citation:not(:disabled):focus-visible { color: var(--red); }
	.citation:disabled { cursor: default; }
	.checkpoint { display: flex; align-items: baseline; gap: 0.75rem; max-width: 68ch; margin: 0.65rem 0 0; padding-top: 0.45rem; border-top: 1px solid var(--rule); color: var(--ink-2); }
	.checkpoint .label { flex: none; }

	.terminal-note { margin: 0.45rem 0 0; font-size: 0.8125rem; text-decoration: underline dashed var(--ash); text-decoration-thickness: 1px; text-underline-offset: 0.3em; }
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
		.readout, .symbol-readout { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.comb { --lane: 1.75rem; grid-template-columns: repeat(var(--ranks), minmax(0, 3rem)) minmax(0, 1fr); }
		.mark { display: none; }
		.narrow-note { display: block; }
		.route-layout { grid-template-columns: 1fr; gap: 1.75rem; }
		.route-sheet { order: -1; }
		.member-chain li { margin-left: calc(var(--depth) * 0.45rem); }
	}
	@media (max-width: 38rem) {
		.readout, .symbol-readout { grid-template-columns: 1fr; }
		.checkpoint { display: block; }
		.checkpoint .label { display: block; margin-bottom: 0.3rem; }
		.member-chain li { margin-left: calc(var(--depth) * 0.25rem); }
	}
</style>
