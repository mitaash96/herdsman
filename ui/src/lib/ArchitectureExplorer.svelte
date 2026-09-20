<script lang="ts">
	import { onMount } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import { daemon, type NavEdge, type NavEntryPoints, type NavIndex, type NavSymbol } from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';

	const resource = new Resource<NavIndex>((signal) => daemon.codemap(signal));
	let query = $state('');
	let selectedKey = $state<string | null>(null);

	onMount(() => {
		void resource.load();
		return () => resource.dispose();
	});

	type ModuleRow = { name: string; files: string[]; symbols: NavSymbol[] };
	type EntryRow = { key: string; label: string; target: string; file: string; line: number | null; kind: string };

	const entries = (entry: NavEntryPoints): EntryRow[] => [
		...(entry.console_script
			? [{
					key: `entry:script:${entry.console_script.name}`,
					label: entry.console_script.name,
					target: entry.console_script.target,
					file: entry.console_script.file,
					line: entry.console_script.line,
					kind: 'console script'
				}]
			: []),
		...entry.cli.map((item) => ({
			key: `entry:cli:${item.command}`,
			label: item.command,
			target: item.command,
			file: item.file,
			line: item.line,
			kind: 'CLI command'
		})),
		...entry.routes.map((item) => ({
			key: `entry:route:${item.method}:${item.path}`,
			label: `${item.method} ${item.path}`,
			target: item.handler,
			file: item.file,
			line: item.line,
			kind: 'daemon route'
		})),
		...entry.tests.map((item) => ({
			key: `entry:test:${item.node}`,
			label: item.node,
			target: item.node,
			file: item.file,
			line: item.line,
			kind: 'test entry'
		}))
	];

	function modulesOf(index: NavIndex): ModuleRow[] {
		const grouped = new Map<string, ModuleRow>();
		for (const symbol of index.symbols) {
			const name = symbol.module || symbol.file;
			const row = grouped.get(name) ?? { name, files: [], symbols: [] };
			if (!row.files.includes(symbol.file)) row.files.push(symbol.file);
			row.symbols.push(symbol);
			grouped.set(name, row);
		}
		for (const file of index.files) {
			const name = file.path.replace(/\.py$/, '').replaceAll('/', '.');
			const row = grouped.get(name);
			if (row && !row.files.includes(file.path)) row.files.push(file.path);
		}
		return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name));
	}

	function matches(text: string, term: string): boolean {
		return text.toLocaleLowerCase().includes(term.toLocaleLowerCase());
	}

	function select(key: string): void {
		selectedKey = key;
	}

	function symbolKey(symbol: NavSymbol): string {
		return `symbol:${symbol.module}:${symbol.file}:${symbol.line}:${symbol.name}`;
	}

	function uniqueSymbol(index: NavIndex, name: string): NavSymbol | null {
		const matches = index.symbols.filter((symbol) => symbol.name === name);
		return matches.length === 1 ? matches[0] : null;
	}

	function selectSymbol(index: NavIndex, name: string): void {
		const symbol = uniqueSymbol(index, name);
		if (symbol) select(symbolKey(symbol));
	}

	function symbolOf(index: NavIndex, key: string | null): NavSymbol | null {
		return key?.startsWith('symbol:')
			? index.symbols.find((symbol) => symbolKey(symbol) === key) ?? null
			: null;
	}

	function edgeLabel(edge: NavEdge): string {
		return edge.resolution;
	}

	function isSymbol(index: NavIndex, name: string): boolean {
		return uniqueSymbol(index, name) !== null;
	}

	function shortName(name: string): string {
		return name.split('.').at(-1) ?? name;
	}

	function testsFor(index: NavIndex, symbol: NavSymbol): NavIndex['entry_points']['tests'] {
		const leaf = shortName(symbol.name).toLocaleLowerCase();
		return index.entry_points.tests.filter((test) => test.node.toLocaleLowerCase().includes(leaf));
	}

	function source(row: { file: string; line: number | null }): string {
		return `${row.file}:${row.line ?? '—'}`;
	}
</script>

<section class="architecture-explorer" data-r13="architecture-explorer" aria-labelledby="architecture-title">
	<p class="label rule-label">
		<span>Run / Read mode</span><span class="rule"></span><span>Source architecture</span>
	</p>
	<h2 id="architecture-title" class="architecture-title">Repository map</h2>
	<p class="prose architecture-intro">
		Entry points lead into modules and symbols. Select a node to read the structural evidence the
		daemon answered; this is a map, not a completeness or verification verdict.
	</p>

	<AsyncField resource={resource} reading="the source map" onretry={() => void resource.load()}>
		{#snippet children(index: NavIndex)}
			{@const allModules = modulesOf(index)}
			{@const allEntries = entries(index.entry_points)}
			{@const term = query.trim()}
			{@const shownModules = allModules.filter((row) => !term || matches(row.name, term) || row.symbols.some((symbol) => matches(symbol.name, term))).slice(0, 80)}
			{@const shownEntries = allEntries.filter((row) => !term || matches(`${row.label} ${row.target} ${row.file}`, term)).slice(0, 80)}
			{@const selectedSymbol = symbolOf(index, selectedKey)}
			{@const selectedModule = selectedKey?.startsWith('module:') ? allModules.find((row) => row.name === (selectedKey ? selectedKey.slice(7) : '')) ?? null : null}
			{@const selectedEntry = selectedKey?.startsWith('entry:') ? allEntries.find((row) => row.key === selectedKey) ?? null : null}
			{@const displayedSymbols = (selectedModule?.symbols ?? index.symbols.filter((symbol) => !term || matches(`${symbol.name} ${symbol.file} ${symbol.signature}`, term))).slice(0, 120)}
			{@const callers = selectedSymbol ? index.edges.filter((edge) => edge.dst === selectedSymbol.name) : []}
			{@const dependencies = selectedSymbol ? index.edges.filter((edge) => edge.src === selectedSymbol.name) : []}
			{@const constructions = dependencies.filter((edge) => edge.kind === 'instantiates')}
			{@const linkedTests = selectedSymbol ? testsFor(index, selectedSymbol) : []}
			{@const unresolved = selectedSymbol ? index.unresolved.filter((edge) => edge.src === selectedSymbol.name) : []}

			<div class="architecture-meta">
				<div><span class="label">Repository / ref</span><strong>unstamped</strong><small>API repo_ref is null</small></div>
				<div><span class="label">Freshness</span><strong>unstamped</strong><small>live read; no freshness stamp supplied</small></div>
				<div><span class="label">Coverage</span><strong>{index.coverage.languages.join(', ') || 'unknown'}</strong><small>{index.symbols.length} symbols · {index.edges.length} resolved edges</small></div>
				<div><span class="label">Responsibilities</span><strong>unknown</strong><small>not provided by this projection</small></div>
			</div>

			<div class="evidence-strip" aria-label="Evidence legend">
				<span class="evidence structural">structural · indexed</span>
				<span class="evidence static">static · resolved</span>
				<span class="evidence dynamic">dynamic · unproven</span>
				<span class="evidence external">external · boundary</span>
				<span class="evidence unresolved">unresolved · listed</span>
				<span class="evidence curated">curated · none supplied</span>
				<span class="evidence unknown">unknown · not inferred</span>
			</div>

			<label class="architecture-search">
				<span class="label">Search entry points, modules, symbols</span>
				<input bind:value={query} type="search" placeholder="Filter the register…" aria-label="Search source architecture" />
			</label>

			<div class="architecture-grid">
				<aside class="architecture-register" aria-label="Architecture register">
					<section>
						<p class="label register-heading"><span>Entry points</span><span>{shownEntries.length}/{allEntries.length}</span></p>
						{#if shownEntries.length === 0}<p class="quiet">No matching entry point.</p>{/if}
						<ul class="register-list">
							{#each shownEntries as entry, i (`${entry.key}:${i}`)}
								<li><button class:chosen={selectedKey === entry.key} class="register-button" type="button" onclick={() => select(entry.key)}>
									<strong>{entry.label}</strong><span>{entry.kind} · {source(entry)}</span><small class="evidence structural">structural</small>
								</button></li>
							{/each}
						</ul>
					</section>
					<section>
						<p class="label register-heading"><span>Modules</span><span>{shownModules.length}/{allModules.length}</span></p>
						<ul class="register-list">
							{#each shownModules as module (module.name)}
								<li><button class:chosen={selectedKey === `module:${module.name}`} class="register-button" type="button" onclick={() => select(`module:${module.name}`)}>
									<strong>{module.name}</strong><span>{module.symbols.length} symbols · {module.files.length} files</span><small class="evidence structural">structural</small>
								</button></li>
							{/each}
						</ul>
						{#if allModules.length > shownModules.length}<p class="quiet scale-note">Showing 80. Refine search to continue.</p>{/if}
					</section>
				</aside>

				<section class="architecture-reading" aria-live="polite">
					{#if selectedSymbol}
						<p class="label rule-label"><span>Symbol</span><span class="rule"></span><span>selected</span></p>
						<h3 class="architecture-heading">{selectedSymbol.name}</h3>
						<p class="source-line"><code>{source(selectedSymbol)}</code> <span class="evidence static">indexed</span></p>
						<dl class="symbol-surface">
							<div class="wide"><dt class="label">Signature</dt><dd><code>{selectedSymbol.signature || 'unknown'}</code></dd></div>
							<div><dt class="label">Kind</dt><dd>{selectedSymbol.kind}</dd></div>
							<div><dt class="label">Returns</dt><dd>{selectedSymbol.returns || 'unknown'}</dd></div>
							<div><dt class="label">Bases</dt><dd>{selectedSymbol.bases.join(', ') || '—'}</dd></div>
							<div><dt class="label">Exported</dt><dd>{selectedSymbol.exported ? 'yes' : 'no'}</dd></div>
							<div class="wide"><dt class="label">Documentation</dt><dd>{selectedSymbol.doc || 'unknown — no docstring in the projection'}</dd></div>
						</dl>

						<div class="topology" aria-label="Visual caller and dependency map">
							<svg viewBox="0 0 600 84" preserveAspectRatio="none" aria-hidden="true"><path d="M8 42H198M402 42H592" /></svg>
							<div class="topology-side">
								{#each callers.slice(0, 4) as edge, i (`${edge.src}:${edge.file}:${edge.line}:${i}`)}
									{#if isSymbol(index, edge.src)}<button type="button" onclick={() => selectSymbol(index, edge.src)}>{shortName(edge.src)}</button>{:else}<span>{shortName(edge.src)}</span>{/if}
								{:else}<span class="quiet">no callers</span>{/each}
								{#if callers.length > 4}<small>+{callers.length - 4} more</small>{/if}
							</div>
							<div class="topology-focus">{shortName(selectedSymbol.name)}</div>
							<div class="topology-side">
								{#each dependencies.slice(0, 4) as edge, i (`${edge.dst}:${edge.file}:${edge.line}:${i}`)}
									{#if isSymbol(index, edge.dst)}<button type="button" onclick={() => selectSymbol(index, edge.dst)}>{shortName(edge.dst)}</button>{:else}<span>{shortName(edge.dst)}</span>{/if}
								{:else}<span class="quiet">no dependencies</span>{/each}
								{#if dependencies.length > 4}<small>+{dependencies.length - 4} more</small>{/if}
							</div>
						</div>
						<div class="semantic-links">
							<section><p class="label">Callers / dependencies</p>
								{#if callers.length === 0 && dependencies.length === 0}<p class="quiet">No resolved edge is recorded.</p>{/if}
								<ul>{#each [...callers, ...dependencies].slice(0, 80) as edge, i (`${edge.src}-${edge.dst}-${i}`)}<li><code>{edge.src === selectedSymbol.name ? '→' : '←'} {edge.src === selectedSymbol.name ? edge.dst : edge.src}</code> <span class="evidence {edge.resolution}">{edgeLabel(edge)}</span> <small>{edge.kind} · {source(edge)}</small></li>{/each}</ul>
							</section>
							<section><p class="label">Construction sites</p>
								{#if constructions.length === 0}<p class="quiet">No resolved construction site.</p>{:else}<ul>{#each constructions as edge, i (`${edge.src}:${edge.file}:${edge.line}:${i}`)}<li><code>{edge.src}</code> <span class="evidence {edge.resolution}">{edge.resolution}</span> <small>{source(edge)}</small></li>{/each}</ul>{/if}
							</section>
							<section><p class="label">Linked tests</p>
								{#if linkedTests.length === 0}<p class="quiet">unknown — no test was resolved by symbol name.</p>{:else}<ul>{#each linkedTests as test, i (`${test.node}:${i}`)}<li><code>{test.node}</code> <span class="evidence static">name-resolved</span> <small>{source(test)}</small></li>{/each}</ul>{/if}
							</section>
							<section><p class="label">Unresolved edges</p>
								{#if unresolved.length === 0}<p class="quiet">None recorded for this symbol.</p>{:else}<ul>{#each unresolved as edge, i (`${edge.name}:${edge.file}:${edge.line}:${i}`)}<li><code>{edge.name}</code> <span class="evidence unresolved">unresolved</span> <small>{source(edge)}</small></li>{/each}</ul>{/if}
							</section>
						</div>
				{:else if selectedModule}
						<p class="label rule-label"><span>Module</span><span class="rule"></span><span>selected</span></p>
						<h3 class="architecture-heading">{selectedModule.name}</h3>
						<p class="prose">Responsibility: <strong>unknown</strong> — no module blurbs are supplied by this API. Freshness: <strong>unstamped</strong>.</p>
						<p class="label register-heading"><span>Symbols in module</span><span>{displayedSymbols.length}/{selectedModule.symbols.length}</span></p>
						<ul class="symbol-register">{#each displayedSymbols as symbol (symbolKey(symbol))}<li><button class="register-button" type="button" onclick={() => select(symbolKey(symbol))}><strong>{symbol.name}</strong><span>{symbol.signature || symbol.kind} · {source(symbol)}</span></button></li>{/each}</ul>
					{:else if selectedEntry}
						<p class="label rule-label"><span>Entry point</span><span class="rule"></span><span>structural</span></p>
						<h3 class="architecture-heading">{selectedEntry.label}</h3>
						<p class="source-line"><code>{source(selectedEntry)}</code> <span class="evidence structural">structural</span></p>
						<dl class="symbol-surface"><div><dt class="label">Kind</dt><dd>{selectedEntry.kind}</dd></div><div><dt class="label">Target</dt><dd><code>{selectedEntry.target}</code></dd></div><div class="wide"><dt class="label">Evidence</dt><dd>Discovered from the existing entry-point projection; target reachability is not inferred here.</dd></div></dl>
					{:else}
						<p class="label rule-label"><span>Selection</span><span class="rule"></span><span>none</span></p>
						<h3 class="architecture-heading">Choose an entry point, module, or symbol</h3>
						<p class="prose">The register is keyboard navigable. Search narrows large repositories; details stay closed until selected.</p>
					{/if}

					{#if selectedModule === null && selectedSymbol === null && selectedEntry === null && displayedSymbols.length > 0}
						<p class="label register-heading"><span>Symbol register</span><span>{displayedSymbols.length}/{index.symbols.length}</span></p>
						<ul class="symbol-register">{#each displayedSymbols as symbol (symbolKey(symbol))}<li><button class="register-button" type="button" onclick={() => select(symbolKey(symbol))}><strong>{symbol.name}</strong><span>{symbol.signature || symbol.kind} · {source(symbol)}</span></button></li>{/each}</ul>
					{/if}
				</section>
			</div>
		{/snippet}
	</AsyncField>
</section>

<style>
	.architecture-explorer { margin: 0 0 4rem; }
	.architecture-title { margin: 0 0 0.6rem; font-family: 'Archivo', ui-sans-serif, sans-serif; font-size: 2rem; line-height: 1; text-transform: uppercase; }
	.architecture-intro { margin: 0 0 1.5rem; max-width: 66ch; }
	.architecture-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 1.5rem 0 1px; background: var(--rule); border: 1px solid var(--rule); }
	.architecture-meta > div { min-width: 0; padding: 0.7rem 0.8rem; background: var(--plate); }
	.architecture-meta strong, .architecture-meta small { display: block; }
	.architecture-meta strong { margin-top: 0.2rem; font-weight: 500; }
	.architecture-meta small { margin-top: 0.25rem; color: var(--ink-2); font-size: 0.65rem; line-height: 1.4; }
	.evidence-strip { display: flex; flex-wrap: wrap; gap: 0.45rem 0.75rem; padding: 0.8rem 0; color: var(--ink-2); }
	.evidence { display: inline-block; font-size: 0.62rem; letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap; }
	.evidence::before { content: '· '; }
	.evidence.static { color: var(--ink); }
	.evidence.dynamic { color: #986b00; }
	.evidence.external { color: var(--ink-2); }
	.evidence.unresolved, .evidence.unknown { color: var(--red); }
	.evidence.curated { color: var(--ink-2); text-decoration: underline dashed var(--ash); text-underline-offset: 0.2rem; }
	.evidence.structural { color: var(--ink-2); }
	.architecture-search { display: block; max-width: 34rem; margin: 0 0 1.5rem; }
	.architecture-search input { display: block; width: 100%; margin-top: 0.35rem; padding: 0.55rem 0.7rem; font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); }
	.architecture-search input:focus { border-color: var(--red); outline: 2px solid var(--red); outline-offset: 2px; }
	.architecture-grid { display: grid; grid-template-columns: minmax(15rem, 0.75fr) minmax(0, 1.65fr); gap: 2rem; align-items: start; }
	.architecture-register { min-width: 0; }
	.architecture-register section + section { margin-top: 2rem; }
	.register-heading { display: flex; justify-content: space-between; gap: 1rem; margin: 0 0 0.45rem; }
	.register-list, .symbol-register, .semantic-links ul { margin: 0; padding: 0; list-style: none; }
	.register-list { max-height: 22rem; overflow: auto; border-top: 1px solid var(--rule); }
	.register-list li, .symbol-register li { border-bottom: 1px solid var(--rule); }
	.register-button { display: block; width: 100%; padding: 0.55rem 0.65rem; border: 0; background: transparent; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
	.register-button:hover, .register-button.chosen { background: var(--red-quiet); }
	.register-button strong, .register-button span { display: block; overflow-wrap: anywhere; }
	.register-button strong { font-weight: 500; }
	.register-button span { color: var(--ink-2); font-size: 0.7rem; line-height: 1.4; }
	.register-button .evidence { margin-top: 0.1rem; }
	.scale-note { margin: 0.5rem 0 0; }
	.architecture-reading { min-width: 0; border-top: 1px solid var(--rule-strong); padding-top: 0.8rem; }
	.architecture-heading { margin: 0 0 0.5rem; font-family: 'Archivo', ui-sans-serif, sans-serif; font-size: clamp(1.4rem, 3vw, 2.3rem); line-height: 1; overflow-wrap: anywhere; }
	.source-line { margin: 0 0 1.2rem; color: var(--ink-2); }
	code { white-space: pre; overflow-wrap: normal; }
	.source-line, .symbol-surface dd, .semantic-links li { overflow-x: auto; }
	.symbol-surface { display: flex; flex-wrap: wrap; gap: 1px; margin: 0; background: var(--rule); border: 1px solid var(--rule); }
	.symbol-surface > div { flex: 1 1 10rem; min-width: 0; padding: 0.65rem 0.8rem; background: var(--plate); }
	.symbol-surface .wide { flex-basis: 100%; }
	.symbol-surface dt { margin-bottom: 0.25rem; }
	.symbol-surface dd { margin: 0; color: var(--ink-2); }
	.topology { position: relative; display: grid; grid-template-columns: 1fr 1fr 1fr; align-items: center; gap: 1.2rem; min-height: 5.25rem; margin: 1.5rem 0; }
	.topology svg { position: absolute; inset: 0; width: 100%; height: 100%; stroke: var(--rule-strong); stroke-width: 1.5; fill: none; }
	.topology-side, .topology-node, .topology-focus { position: relative; min-width: 0; padding: 0.6rem; background: var(--plate); border: 1px solid var(--rule-strong); text-align: center; overflow-wrap: anywhere; }
	.topology-side { display: flex; flex-direction: column; gap: 0.15rem; }
	.topology-side button { border: 0; padding: 0; background: transparent; color: var(--ink); font: inherit; text-decoration: underline; text-decoration-color: var(--red); text-underline-offset: 0.2rem; cursor: pointer; overflow-wrap: anywhere; }
	.topology-side small { color: var(--ink-2); }
	.topology-focus { border-color: var(--red); color: var(--ink); }
	.semantic-links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.5rem; }
	.semantic-links section { min-width: 0; }
	.semantic-links .label { display: block; margin-bottom: 0.5rem; }
	.semantic-links li { padding: 0.4rem 0; border-bottom: 1px solid var(--rule); overflow-wrap: anywhere; }
	.semantic-links small { display: block; color: var(--ink-2); font-size: 0.68rem; }
	.quiet { color: var(--ink-2); font-size: 0.8rem; }
	@media (max-width: 52rem) {
		.architecture-meta { grid-template-columns: repeat(2, 1fr); }
		.architecture-grid { grid-template-columns: 1fr; gap: 2.5rem; }
		.architecture-register { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
		.architecture-register section + section { margin-top: 0; }
	}
	@media (max-width: 34rem) {
		.architecture-meta, .architecture-register, .semantic-links { grid-template-columns: 1fr; }
		.architecture-register section + section { margin-top: 1.5rem; }
		.topology { gap: 0.4rem; font-size: 0.72rem; }
	}
</style>
