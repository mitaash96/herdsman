<script lang="ts">
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import { onMount } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import MarginSheet, { type MarginSection } from '$lib/MarginSheet.svelte';
	import DrawerSeat from '$lib/DrawerSeat.svelte';
	import type { SeatWidth } from '$lib/seat.svelte';
	import Markdown from '$lib/Markdown.svelte';
	import { Resource } from '$lib/resource.svelte';
	import {
		daemon,
		DaemonError,
		type Asset,
		type AssetOrigin,
		type AssetStatus,
		type AssetSummary,
		type Fleet,
		type Kitchen,
		type LibraryIssue,
		type LibrarySnapshot,
		type Plan
	} from '$lib/daemon';
	import {
		ALL,
		DRIFT_WORD,
		KINDS,
		KIND_GLOSS,
		KIND_WORD,
		ORIGIN_WORD,
		STATUS_WORD,
		closureOf,
		compareFrozen,
		count,
		driftState,
		filterShelf,
		groupByKind,
		issuesFor,
		referencedBy,
		statusState,
		type ClosureNode,
		type ShelfFilter
	} from '$lib/shelf';

	/* --- the two reads -------------------------------------------------------
	   The live shelf and one approved plan version's frozen set. Two reads, not
	   one filtered list, for the same reason Home splits active from archived:
	   they answer different questions and share no totals. The frozen set is
	   immutable by construction, so it carries no control at all. */
	type Read = 'shelf' | 'frozen';
	let read = $state<Read>('shelf');

	const shelf = new Resource<AssetSummary[]>((signal) => daemon.library(signal));
	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	const fleet = new Resource<Fleet>((signal) => daemon.fleet(signal));

	/** Every readable asset by ref, retired included — the closure walk needs both. */
	const index = $derived(new Map((shelf.data ?? []).map((row) => [row.ref, row])));

	let filter = $state<ShelfFilter>({ ...ALL });
	const listed = $derived(filterShelf(shelf.data ?? [], filter));
	const groups = $derived(groupByKind(listed));

	/* --- selection -----------------------------------------------------------
	   Held as a ref and nothing positional, so a re-read that changes the shelf
	   cannot move what is being read. A filter that excludes the selected asset
	   does not drop the selection either: the register says it is no longer
	   listed, and the sheet keeps reading. */
	let selected = $state<string | null>(null);
	let open = $state<string | null>(null);
	let registerWidth = $state<SeatWidth>('wide');

	const selectedRow = $derived(selected === null ? null : (index.get(selected) ?? null));
	const closure = $derived(
		selected === null || selectedRow === null ? null : closureOf(selected, index)
	);
	const inRegister = $derived(selected !== null && listed.some((row) => row.ref === selected));
	const incoming = $derived(selected === null ? [] : referencedBy(selected, shelf.data ?? []));

	/* --- the bodies ----------------------------------------------------------
	   `GET /library` returns summaries; a document needs its own read. One per
	   resolvable node in the closure, in parallel, each holding its own failure
	   so one unreadable asset leaves the rest of the stack readable. */
	type Doc = { phase: 'loading' | 'ready' | 'error'; asset: Asset | null; error: string };
	let docs = $state<Record<string, Doc>>({});
	let docRun = 0;
	// The same map, in a plain value. `loadDocs` runs inside the selection
	// effect, and an effect that *reads* `docs` re-triggers itself the moment it
	// writes one — a saturated queue where every document reads forever. Comparison
	// state a later render does not read is held outside `$state`, on purpose.
	let held: Record<string, Doc> = {};

	async function loadDocs(refs: string[]): Promise<void> {
		docRun += 1;
		const run = docRun;
		const next: Record<string, Doc> = {};
		for (const ref of refs) {
			// Keep a body already in hand rather than blanking it on a re-walk.
			next[ref] =
				held[ref]?.phase === 'ready'
					? held[ref]
					: { phase: 'loading', asset: null, error: '' };
		}
		held = next;
		docs = next;
		await Promise.all(
			refs.map(async (ref) => {
				if (next[ref].phase === 'ready') return;
				try {
					const asset = await daemon.asset(ref);
					if (run !== docRun) return;
					held = { ...held, [ref]: { phase: 'ready', asset, error: '' } };
					docs = held;
				} catch (cause) {
					if (run !== docRun) return;
					const message =
						cause instanceof DaemonError
							? cause.message
							: 'This build failed while reading the asset.';
					held = { ...held, [ref]: { phase: 'error', asset: null, error: message } };
					docs = held;
				}
			})
		);
	}

	/* --- the findings --------------------------------------------------------
	   The daemon owns every verdict. The sheet walks the graph to draw the
	   chain; what is *wrong* with a closure, and whether it is over the
	   project's context budget, is `POST /library/validate`'s answer. */
	let issues = $state<LibraryIssue[]>([]);
	let issuesPhase = $state<'idle' | 'loading' | 'ready' | 'error'>('idle');
	let issuesError = $state('');
	let issuesRun = 0;

	async function loadIssues(ref: string): Promise<void> {
		issuesRun += 1;
		const run = issuesRun;
		// Findings belong to one closure. Carrying the previous set across a
		// selection would print a verdict about a different set of assets — and
		// where two closures share a ref, print it under a document it is not
		// about, beside a column already saying the findings are unread.
		issues = [];
		issuesPhase = 'loading';
		try {
			const answer = await daemon.validateAssets([ref], ref);
			if (run !== issuesRun) return;
			issues = answer.issues;
			issuesPhase = 'ready';
			issuesError = '';
		} catch (cause) {
			if (run !== issuesRun) return;
			issuesPhase = 'error';
			issuesError =
				cause instanceof DaemonError
					? cause.message
					: 'This build failed while validating the closure.';
		}
	}

	function select(ref: string | null): void {
		selected = ref;
		open = null;
		const url = new URL(page.url);
		if (ref === null) url.searchParams.delete('asset');
		else url.searchParams.set('asset', ref);
		replaceState(url, {});
	}

	/* The address is a live input, not a one-time mount read: a jump from the
	   band while already on /library changes the selection. Held in a plain
	   value, not `$state` — an effect that reads and writes one reactive value
	   re-triggers itself on its own write, and the selection is written here.
	   select()'s own write finds the address already held and stops there. */
	let addressedAsset: string | null | undefined;
	$effect(() => {
		const asset = page.url.searchParams.get('asset');
		if (asset === addressedAsset) return;
		addressedAsset = asset;
		if (asset !== null && asset !== '') selected = asset;
	});

	// One effect, one direction: the selection drives the reads, and neither read
	// writes back to it. An effect that reads and writes one reactive value
	// re-triggers itself on its own write and saturates the queue.
	$effect(() => {
		const walk = closure;
		if (walk === null) {
			held = {};
			docs = {};
			issues = [];
			issuesPhase = 'idle';
			return;
		}
		const refs = walk.nodes.filter((node) => node.asset !== null).map((node) => node.ref);
		void loadDocs(refs);
		void loadIssues(walk.root);
	});

	/* --- the frozen read ------------------------------------------------------ */
	let frozenPlan = $state('');
	let frozenVersion = $state('');
	const planRead = new Resource<Plan>((signal) => daemon.plan(frozenPlan, signal));

	const approvedRuns = $derived(
		(fleet.data?.runs ?? []).filter((run) => run.approval === 'approved')
	);
	const snapshots = $derived<Record<string, LibrarySnapshot>>(
		planRead.data?.asset_snapshots ?? {}
	);
	const versions = $derived(Object.keys(snapshots).sort((a, b) => Number(b) - Number(a)));
	const snapshot = $derived<LibrarySnapshot | null>(
		frozenVersion === '' ? null : (snapshots[frozenVersion] ?? null)
	);
	const frozenRows = $derived(snapshot === null ? [] : compareFrozen(snapshot.assets, index));

	$effect(() => {
		if (frozenPlan === '') return;
		void planRead.load();
	});

	// Correcting a version that the chosen run does not have is not a preference,
	// so it is applied once against the list the read returned.
	$effect(() => {
		const available = versions;
		if (available.length > 0 && !available.includes(frozenVersion)) {
			frozenVersion = available[0];
		}
	});

	/* --- context budget ------------------------------------------------------- */
	const budget = $derived(kitchen.data?.context_warning_tokens ?? null);
	const overBudget = $derived(budget !== null && closure !== null && closure.tokens > budget);

	const kindsPresent = $derived(
		KINDS.filter((kind) => (shelf.data ?? []).some((row) => row.kind === kind))
	);
	const sections = $derived<MarginSection[]>(
		read === 'shelf' && selected !== null && shelf.data
			? [{ id: 'register', label: 'Register', count: count(listed.length), state: 'seated' }]
			: []
	);

	function reload(): void {
		void shelf.load();
		if (read === 'frozen') void fleet.load();
	}

	onMount(() => {
		void shelf.load();
		void kitchen.load();

		// The shelf is disk, and $EDITOR is how v1 authors an asset, so a read on
		// return to the window is the whole watch this unit needs. `GET
		// /library/events` exists and belongs to L2, where a terminal edit is the
		// thing being watched for rather than a side effect.
		const onFocus = () => {
			if (!document.hidden) reload();
		};
		window.addEventListener('focus', onFocus);
		return () => {
			window.removeEventListener('focus', onFocus);
			shelf.dispose();
			kitchen.dispose();
			fleet.dispose();
			planRead.dispose();
		};
	});

	function openFrozen(): void {
		open = null;
		read = 'frozen';
		if (!fleet.hasData) void fleet.load();
	}

	const ISSUE_WORD: Record<string, string> = {
		'reference-missing': 'Reference missing',
		'reference-retired': 'Reference archived',
		'reference-cycle': 'Reference cycle',
		'context-size': 'Over the context budget',
		'memory-stale': 'Marked stale',
		'memory-conflicted': 'Marked conflicted',
		'contract-ambiguous': 'Contract ambiguous',
		'contract-conflict': 'Contract conflict'
	};

	/** The ring state one closure node is drawn at. */
	function nodeState(node: ClosureNode): string {
		switch (node.state) {
			case 'root':
				return 'loaded';
			case 'present':
				return 'seated';
			case 'retired':
				return 'slack';
			case 'missing':
			case 'cycle':
				return 'failed';
		}
	}

	/** One asset's own findings — never the closure-wide context-size warning. */
	const assetIssues = (ref: string) =>
		issuesFor(ref, issues).filter((issue) => issue.code !== 'context-size');

	const NODE_WORD: Record<ClosureNode['state'], string> = {
		root: 'Root',
		present: 'Referenced',
		retired: 'Archived',
		missing: 'Missing',
		cycle: 'Cycle'
	};
</script>

{#snippet registerPicker(rows: AssetSummary[], prefix: string)}
	<div class="filters">
		<div class="chips" role="group" aria-label="Kind">
			<button type="button" class="chip" aria-pressed={filter.kind === 'all'} onclick={() => (filter = { ...filter, kind: 'all' })}>All kinds</button>
			{#each kindsPresent as kind (kind)}<button type="button" class="chip" aria-pressed={filter.kind === kind} onclick={() => (filter = { ...filter, kind })} title={KIND_GLOSS[kind]}>{KIND_WORD[kind]}</button>{/each}
		</div>
		<div class="picks">
			<span class="pick"><label class="label" for="{prefix}-origin">Origin</label><select id="{prefix}-origin" class="plate" value={filter.origin} onchange={(event) => (filter = { ...filter, origin: event.currentTarget.value as AssetOrigin | 'all' })}><option value="all">Bundled and project</option><option value="bundled">Bundled only</option><option value="project">Project only</option></select></span>
			<span class="pick"><label class="label" for="{prefix}-status">Status</label><select id="{prefix}-status" class="plate" value={filter.status} onchange={(event) => (filter = { ...filter, status: event.currentTarget.value as AssetStatus | 'all' })}><option value="active">Active</option><option value="all">Including archived</option><option value="retired">Archived only</option></select></span>
			<span class="pick find"><label class="label" for="{prefix}-find">Find</label><input id="{prefix}-find" class="plate" type="search" placeholder="ref or title" value={filter.query} oninput={(event) => (filter = { ...filter, query: event.currentTarget.value })} /></span>
		</div>
	</div>
	{#if listed.length === 0}
		<p class="prose">No asset on the shelf matches this filter. {count(rows.length)} are on disk; widen the filter to reach them. This is a filter with nothing behind it, not an empty shelf.</p>
	{:else}
		<div class="register">{#each groups as group (group.kind)}<div class="group"><p class="label rule-label tight"><span>{KIND_WORD[group.kind]}</span><span class="rule"></span><span class="n">{count(group.rows.length)}</span></p><ul class="rail">{#each group.rows as row (row.ref)}<li><button type="button" class="entry member" data-state={statusState(row.status)} aria-current={selected === row.ref ? 'true' : undefined} onclick={() => select(row.ref)}><span class="entry-name">{row.name}</span><span class="entry-dims"><span class="dim">{ORIGIN_WORD[row.origin]}{row.shadows_bundled ? ' override' : ''}</span><span class="dim">{count(row.tokens)} tok</span>{#if row.references.length > 0}<span class="dim">{count(row.references.length)} ref</span>{/if}{#if row.status !== 'active'}<span class="dim state">{STATUS_WORD[row.status]}</span>{/if}</span></button></li>{/each}</ul></div>{/each}</div>
	{/if}
{/snippet}

<svelte:head><title>Library — Herdsman</title></svelte:head>

<MarginSheet {sections} bind:open>
	{#snippet caption()}
		<div class="caption-row">
	<p class="label rule-label">
		<span>Shelf</span>
		<span class="rule"></span>
		<span
			class="member"
			data-state={shelf.phase === 'error'
				? 'failed'
				: shelf.stale || !shelf.data
					? 'slack'
					: 'seated'}
		>
			{#if !shelf.data}
				—
			{:else if listed.length === shelf.data.length}
				{count(shelf.data.length)}
				{shelf.data.length === 1 ? 'asset' : 'assets'}
			{:else}
				<!-- The register is filtered, so the total alone would state a count
				     nothing on screen can be counted to. -->
				{count(listed.length)} of {count(shelf.data.length)} assets
			{/if}
		</span>
	</p>

			<div class="read-controls">
				<!-- Two reads, not a filter over one. -->
				<div class="switch" role="group" aria-label="Which set to read">
					<button type="button" class="plate tab" aria-pressed={read === 'shelf'} onclick={() => { read = 'shelf'; open = null; }}>
						Live shelf {#if shelf.data}<span class="n">{count(shelf.data.length)}</span>{/if}
					</button>
					<button type="button" class="plate tab" aria-pressed={read === 'frozen'} onclick={openFrozen}>Approved plan</button>
				</div>
				{#if read === 'frozen'}
					<p class="gloss caption-gloss">An approved plan version froze the exact bytes each initiative received. Those assets are immutable: a later edit to the shelf cannot reach backwards into an approval, which is what makes a replay honest.</p>
				{/if}
			</div>
		</div>
	{/snippet}
	{#snippet hero()}

	{#if read === 'shelf'}
		<AsyncField resource={shelf} reading="the shelf" onretry={() => void shelf.load()}>
			{#snippet children(rows: AssetSummary[])}
				{#if rows.length === 0}
					<p class="prose">
						The shelf is empty. The daemon answered with no assets at all, which is a
						project that ships none and has authored none — not a failed read.
					</p>
					<p class="prose quiet">
						<code>uv run python ui/dev/seed_library.py</code> writes a real set of project-local
						assets into <code>.herdsman/library/</code>. Authoring one from here is L2's work
						and is not built; v1 authoring is <code>$EDITOR</code> plus a file watch.
					</p>
				{:else}
					{#if selected === null}{@render registerPicker(rows, 'hero')}{/if}

					{#if selected !== null}
					<!-- The closure sheet. -->
					{#if selectedRow === null}
						<p class="label rule-label">
							<span>Closure</span><span class="rule"></span>
							<span class="member" data-state="failed">Not on the shelf</span>
						</p>
						<p class="prose">
							<code>{selected}</code> is not on the shelf this read returned. It may have been
							renamed or archived out of reach of this link, or the link may name an asset this
							project never had.
						</p>
						<button type="button" class="plate ghost" onclick={() => select(null)}
							>Clear the selection</button
						>
					{:else if closure}
						<p class="label rule-label">
							<span>Closure</span>
							<span class="rule"></span>
							<span class="member" data-state={closure.missing.length > 0 ? 'failed' : 'seated'}
								>{selected}</span
							>
						</p>

						{#if !inRegister}
							<p class="prose note" role="status">
								The current filter does not list this asset, so it is not in the register
								above. It is still selected and still being read — a filter narrows what you
								can reach, not what you are reading.
							</p>
						{/if}

						<div class="sheet-grid">
							<!-- The chain. Rings on a carbon run that overshoots the last
							     one, each knocking out in plate: the drawer's subtask
							     chain at shelf scale, unchanged. -->
							<div class="chain-col">
								<ol class="chain">
									{#each closure.nodes as node, n (node.ref + ':' + n)}
										<li
											class="link member"
											data-state={nodeState(node)}
											style="--depth: {node.depth}"
										>
											<span class="ring" aria-hidden="true"></span>
											{#if node.asset}
												<a class="link-ref" href="#doc-{n}">{node.ref}</a>
											{:else}
												<span class="link-ref">{node.ref}</span>
											{/if}
											{#if node.state !== 'present'}
												<span class="link-state">{NODE_WORD[node.state]}</span>
											{/if}
											<span class="link-run">{node.asset ? count(node.running) : '—'}</span>
										</li>
									{/each}
								</ol>


							</div>

							<!-- The documents, in closure order, root first. -->
							<div class="docs">
								{#each closure.nodes as node, n (node.ref + ':' + n)}
									<!-- Keyed by position, not by ref: a cycle node repeats a ref a
									     resolved node already used, and two elements sharing an id make
									     every anchor to it resolve to the first. -->
									<article class="doc-block" id="doc-{n}">
										<!-- A heading, not a styled paragraph: the shell sets the h1 and
										     the Markdown renderer starts at h3, so without this a reader
										     moving by heading through a six-document closure gets an
										     undifferentiated run with nothing naming which asset it is in. -->
										<h2 class="label rule-label tight">
											<span>{NODE_WORD[node.state]}</span>
											<span class="rule"></span>
											<span class="member" data-state={nodeState(node)}>{node.ref}</span>
										</h2>

										{#if node.state === 'missing'}
											<p class="lead member" data-state="failed">This reference is broken.</p>
											<p class="prose">
												Nothing on the shelf answers to <code>{node.ref}</code>. It is declared
												by
												<code>{node.via[node.via.length - 1] ?? closure.root}</code>, so that
												asset's load path stops here: an initiative given it would receive a set
												the daemon refuses to snapshot.
											</p>
										{:else if node.state === 'cycle'}
											<p class="lead member" data-state="failed">This reference closes a cycle.</p>
											<p class="prose">
												<code>{node.ref}</code> references itself back through
												<code>{node.via.join(' → ')}</code>. The walk stops rather than
												following it, so nothing below this is lost — the cycle is.
											</p>
										{:else if node.asset}
											{@const doc = docs[node.ref]}
											<div class="doc-head">
												{#if node.asset.title !== ''}
													<p class="doc-title">{node.asset.title}</p>
												{/if}
													<p class="dims">{KIND_WORD[node.asset.kind]} · {ORIGIN_WORD[node.asset.origin]} · {node.asset.digest.slice(0, 8)} · {count(node.asset.tokens)} tokens · <span class="dim-v member" data-state={statusState(node.asset.status)}>{STATUS_WORD[node.asset.status]}</span></p>

												{#if node.asset.origin === 'bundled'}
													<p class="prose quiet small">
														Bundled with the package and read-only. Editing it in L2 writes a
														project copy that shadows this one; this file itself never changes.
													</p>
												{:else if node.asset.shadows_bundled}
													<p class="prose quiet small">
														A project override standing in front of a bundled asset of the same
														ref. The bundled copy is still on disk and unchanged; this is the one
														every read resolves to.
													</p>
												{/if}

												<!-- Only what is wrong with *this* asset. The context-size
												     finding is a property of the closure and is stated once,
												     beside the running total that carries it. -->
												{#if issuesPhase === 'ready' && assetIssues(node.ref).length > 0}
													<ul class="findings inline">
														{#each assetIssues(node.ref) as issue, i (issue.code + i)}
															<li
																class="finding member"
																data-state={issue.severity === 'error' ? 'failed' : 'slack'}
															>
																<span class="label">{ISSUE_WORD[issue.code] ?? issue.code}</span
																>
																<span class="finding-msg">{issue.message}</span>
															</li>
														{/each}
													</ul>
												{/if}
											</div>

											{#if doc === undefined || doc.phase === 'loading'}
												<p class="state member" data-state="balanced" aria-busy="true">
													<span class="bar"></span>
													<span class="label">Reading {node.ref}</span>
												</p>
											{:else if doc.phase === 'error'}
												<div role="alert">
													<p class="lead member" data-state="failed">This body did not read.</p>
													<p class="prose">
														{doc.error} The dimensions above come from the shelf read, which did
														answer, so what you can see of it is true and its body is unread
														rather than empty.
													</p>
												</div>
											{:else if doc.asset}
												<Markdown
													source={doc.asset.body}
													empty="This asset has no body. Its frontmatter is all there is, and on a contract that is legitimate — the gates are the frontmatter."
												/>
											{/if}
										{/if}
									</article>
								{/each}
							</div>
						</div>
					{/if}
				{/if}
				{/if}
			{/snippet}
		</AsyncField>
	{:else}
		<!-- The frozen read: documents stay in the hero. -->
		<AsyncField resource={fleet} reading="the fleet" onretry={() => void fleet.load()}>
			{#snippet children(view: Fleet)}
				{#if approvedRuns.length === 0}
					<p class="prose">
						No run on this daemon is approved, so nothing has frozen a set of assets yet.
						{#if view.runs.length > 0}
							{count(view.runs.length)}
							{view.runs.length === 1 ? 'run exists' : 'runs exist'} and
							{view.runs.length === 1 ? 'it is' : 'none is'} past its plan gate.
						{/if}
					</p>
				{:else}
					<div class="picks frozen-picks">
						<span class="pick"><label class="label" for="frozen-plan">Approved run</label><select id="frozen-plan" class="plate" bind:value={frozenPlan}><option value="">Choose a run…</option>{#each approvedRuns as run (run.plan_id)}<option value={run.plan_id}>{run.plan_id} — v{run.version}</option>{/each}</select></span>
						<span class="pick"><label class="label" for="frozen-version">Approved version</label><select id="frozen-version" class="plate" bind:value={frozenVersion} disabled={versions.length === 0}>{#if versions.length === 0}<option value="">Choose a run first…</option>{:else}{#each versions as version (version)}<option value={version}>Version {version}</option>{/each}{/if}</select></span>
					</div>
					{#if frozenPlan === ''}
						<p class="prose quiet">Choose an approved run to read what its approval froze.</p>
					{:else}
						<AsyncField
							resource={planRead}
							reading="the approved plan"
							onretry={() => void planRead.load()}
						>
							{#snippet children(plan: Plan)}
								{#if versions.length === 0}
									<p class="prose">
										{plan.id} is approved but froze no assets. A plan approved before the Library
										landed, or one whose initiatives declared none, has no snapshot at all —
										which is unknown, not an empty set.
									</p>
								{:else if snapshot}
									<p class="label rule-label">
										<span>Frozen at approval</span>
										<span class="rule"></span>
										<span class="member" data-state="seated"
											>{count(snapshot.assets.length)}
											{snapshot.assets.length === 1 ? 'asset' : 'assets'}</span
										>
									</p>

									{#if snapshot.assets.length === 0}
										<p class="prose">
											Version {frozenVersion} of {plan.id} was approved with no assets declared by
											any initiative, so it froze none.
										</p>
									{:else}


										<div class="docs frozen-docs">
											{#each frozenRows as row (row.ref)}
												<article class="doc-block">
													<h2 class="label rule-label tight">
														<span>Frozen</span>
														<span class="rule"></span>
														<span class="member" data-state={driftState(row.drift)}>{row.ref}</span
														>
													</h2>
													{#if row.title !== ''}
														<p class="doc-title">{row.title}</p>
													{/if}
													<p class="dims">{KIND_WORD[row.kind]} · {ORIGIN_WORD[row.origin]} · {row.digest.slice(0, 8)} · {count(row.tokens)} tokens · <span class="dim-v member" data-state={driftState(row.drift)}>{DRIFT_WORD[row.drift]}</span></p>
													{#if row.drift === 'edited'}
														<p class="prose quiet small">
															The shelf now holds <code>{row.liveDigest}</code> for this ref. What
															is below is what the approval froze and what a replay would hand an
															executor; it did not change.
														</p>
													{:else if row.drift === 'gone'}
														<p class="prose quiet small">
															Nothing on the live shelf answers to this ref any more. The frozen
															bytes below are unaffected — that is the point of a snapshot.
														</p>
													{/if}
													<Markdown
														source={row.body}
														empty="This asset was frozen with no body. Its frontmatter was all there was."
													/>
												</article>
											{/each}
										</div>
									{/if}
								{/if}
							{/snippet}
						</AsyncField>
					{/if}
				{/if}
			{/snippet}
		</AsyncField>
	{/if}
	{/snippet}
	{#snippet margin()}
		{#if read === 'shelf' && selected !== null && closure}
			<dl class="plate readout">
				<div class="member" data-state={overBudget ? 'failed' : 'seated'}>
					<dt class="label">Effective context</dt><dd class="value">{count(closure.tokens)}{#if budget !== null}<span class="of">/{count(budget)}</span>{/if}</dd>
					<p class="gloss">{#if budget === null}tokens; the project's budget was not read{:else if overBudget}tokens, over the project's warning budget{:else}tokens against the project's warning budget{/if}</p>
				</div>
				<div><dt class="label">Assets</dt><dd class="value">{count(closure.nodes.filter((node) => node.asset).length)}</dd><p class="gloss">what this initiative would carry</p></div>
				<div class:member={closure.missing.length > 0} data-state={closure.missing.length > 0 ? 'failed' : 'seated'}><dt class="label">Broken references</dt><dd class="value">{count(closure.missing.length)}</dd><p class="gloss">refs that resolve to nothing</p></div>
			</dl>
			<p class="label rule-label tight"><span>Findings</span><span class="rule"></span></p>
			{#if issuesPhase === 'loading'}
				<p class="state member" data-state="balanced" aria-busy="true"><span class="bar"></span><span class="label">Reading the findings</span></p>
			{:else if issuesPhase === 'error'}
				<div role="alert"><p class="lead member" data-state="failed">The findings are unread.</p><p class="prose">The daemon did not validate this closure: {issuesError} The chain above is this build's own walk of the same references, and it is drawn; the verdicts are not.</p></div>
			{:else if issues.length === 0}
				<p class="prose quiet">The daemon found nothing wrong with this set: every reference resolves, none is archived, and the effective context is inside the project's budget.</p>
			{:else}
				<ul class="findings">{#each issues as issue, n (issue.code + issue.ref + n)}<li class="finding member" data-state={issue.severity === 'error' ? 'failed' : 'slack'}><span class="label">{ISSUE_WORD[issue.code] ?? issue.code}</span><span class="finding-ref">{issue.ref}</span><span class="finding-msg">{issue.message}</span>{#if issue.detail !== ''}<span class="finding-detail">{#if issue.code === 'context-size'}<span class="label">Largest</span>{/if}{issue.detail}</span>{/if}</li>{/each}</ul>
			{/if}
			{#if incoming.length > 0}<p class="label rule-label tight"><span>Referenced by</span><span class="rule"></span></p><ul class="incoming">{#each incoming as ref (ref)}<li><button type="button" class="bare" onclick={() => select(ref)}>{ref}</button></li>{/each}</ul>{/if}
		{:else if read === 'frozen' && snapshot && snapshot.assets.length > 0}
			<dl class="plate readout">
				<div><dt class="label">Frozen assets</dt><dd class="value">{count(snapshot.assets.length)}</dd></div>
				<div><dt class="label">Union cost</dt><dd class="value">{count(snapshot.assets.reduce((sum, a) => sum + a.tokens, 0))}</dd><p class="gloss">tokens across every frozen asset</p></div>
				<div class="member" data-state={frozenRows.some((row) => row.drift !== 'same') ? 'slack' : 'seated'}><dt class="label">Shelf has moved on</dt><dd class="value">{count(frozenRows.filter((row) => row.drift !== 'same').length)}</dd><p class="gloss">frozen assets the live shelf no longer matches</p></div>
			</dl>
			{#if snapshot.issues.length > 0}<ul class="findings">{#each snapshot.issues as issue, n (issue.code + n)}<li class="finding member" data-state="slack"><span class="label">{ISSUE_WORD[issue.code] ?? issue.code}</span><span class="finding-ref">{issue.ref}</span><span class="finding-msg">{issue.message}</span></li>{/each}</ul><p class="prose quiet small">Recorded at approval and kept. An error blocks an approval, so every finding here is a warning the owner approved over.</p>{/if}
		{/if}
	{/snippet}
</MarginSheet>
{#if read === 'shelf'}
	<DrawerSeat open={open === 'register'} label="Index" tag={`${count(listed.length)} listed`} title="Register" titleId="library-register" bind:width={registerWidth} onclose={() => (open = null)}>
		{@render registerPicker(shelf.data ?? [], 'seat')}
	</DrawerSeat>
{/if}

<style>
	.caption-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
	.caption-row > .rule-label { flex: 1; margin-bottom: 0; }
	.read-controls { display: flex; flex-direction: column; align-items: flex-start; gap: 0.5rem; }
	.caption-gloss { max-width: 68ch; margin: 0; text-align: left; font-size: 0.75rem; display: -webkit-box; -webkit-box-orient: vertical; line-clamp: 2; -webkit-line-clamp: 2; overflow: hidden; }

	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1.75rem;
	}
	.rule-label.tight {
		margin: 1.75rem 0 0.75rem;
	}
	/* The document headings are headings for a screen reader and ruled labels for
	   an eye; the label cut wins over the UA's own h2. */
	h2.rule-label {
		font-size: 0.625rem;
		font-weight: 500;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.n {
		color: var(--ink-2);
		letter-spacing: 0;
	}

	/* --- the two reads -------------------------------------------------------- */
	.switch {
		display: flex;
		gap: 0.5rem;
		margin: 0;
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
	/* Which read you are in is location, not load: carbon, a harder edge, and the
	   carbon locator halo. Never red. */
	.tab[aria-pressed='true'] {
		color: var(--ink);
		border-color: var(--rule-strong);
		box-shadow:
			0 0 0 3px var(--ground),
			0 0 0 4px var(--member-line);
	}
	.tab .n {
		color: var(--ink-2);
	}
	.tab[aria-pressed='true'] .n,
	.tab:hover .n {
		color: inherit;
	}

	/* --- filters --------------------------------------------------------------
	   Kind is a chip row because the set is five and always visible; origin and
	   status are selects because their options are exclusive and few. Find is the
	   one text field on this surface and it names nothing: it narrows a list
	   already on screen. */
	.filters {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		align-items: flex-end;
		gap: 0.5rem;
		margin: 0 0 1rem;
	}
	.chips {
		display: flex;
		flex-wrap: nowrap;
		gap: 0.25rem;
	}
	.chip {
		font: inherit;
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
		background: transparent;
		border: 0;
		border-bottom: 1px solid transparent;
		padding: 0.2rem 0.5rem 0.25rem;
		cursor: pointer;
	}
	.chip:hover {
		color: var(--red);
	}
	.chip[aria-pressed='true'] {
		color: var(--ink);
		border-bottom-color: var(--member-line);
	}

	.picks {
		display: grid;
		grid-template-columns: 11rem 9rem minmax(8rem, 1fr);
		align-items: flex-end;
		gap: 0.5rem;
		margin: 0;
		min-width: 0;
	}
	.frozen-picks { grid-template-columns: minmax(11rem, 16rem) minmax(11rem, 16rem); justify-content: start; margin: 0 0 1rem; }
	.pick {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		min-width: 0;
	}
	.filters .pick { min-width: 0; }
	.filters select,
	.filters input { width: 100%; box-sizing: border-box; }
	.pick.find { min-width: 0; }
	select,
	input {
		--cut: 10px;
		font: inherit;
		color: var(--ink);
		background: var(--plate);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 2rem 0.45rem 0.7rem;
		min-width: 0;
	}
	input {
		padding-right: 0.7rem;
	}
	select {
		appearance: none;
		/* A 1px hairline chevron, drawn rather than glyphed. */
		background-image: linear-gradient(45deg, transparent 50%, var(--ink-2) 50%),
			linear-gradient(135deg, var(--ink-2) 50%, transparent 50%);
		background-position:
			right 1.05rem center,
			right 0.75rem center;
		background-size:
			1px 0.4em,
			0.4em 1px;
		background-repeat: no-repeat;
	}
	select:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	select:focus,
	input:focus {
		border-color: var(--red);
	}
	/* --- the register ---------------------------------------------------------
	   A wrapping rail, never a scroller, grouped under ruled kind labels. Each
	   entry is a bare button carrying its dimensions: repeating a bordered
	   control down a list would put forty operable edges on the sheet. */
	.register {
		margin: 0 0 2.5rem;
	}
	.group + .group {
		margin-top: 1.25rem;
	}
	.rail {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1.75rem;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.entry {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		font: inherit;
		text-align: left;
		background: transparent;
		border: 0;
		padding: 0.3rem 0 0.35rem;
		cursor: pointer;
		color: var(--ink);
	}
	.entry[data-state='slack'] .entry-name,
	.entry[data-state='failed'] .entry-name {
		color: var(--ink-2);
	}
	.entry-name {
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.entry:hover .entry-name {
		color: var(--red);
	}
	.entry[aria-current='true'] .entry-name {
		color: var(--ink);
		border-bottom: 1px solid var(--member-line);
	}
	.entry-dims {
		display: flex;
		flex-wrap: wrap;
		gap: 0.15rem 0.5rem;
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	/* A non-active status is a slack reading: graphite type carrying a dashed ash
	   rule, never ash type. */
	.entry-dims .state {
		border-bottom: 1px dashed var(--ash);
	}

	/* --- the closure sheet ----------------------------------------------------- */
	.sheet-grid {
		display: grid;
		grid-template-columns: 16rem minmax(0, 1fr);
		gap: 2.5rem;
		align-items: start;
	}
	.chain-col {
		position: sticky;
		top: 1.5rem;
		min-width: 0;
	}

	/* The chain: rings on a 1px carbon run that overshoots the last one, each
	   knocking out in plate. The drawer's subtask chain, unchanged. */
	.chain {
		position: relative;
		list-style: none;
		margin: 0 0 1.5rem;
		padding: 0;
	}
	.chain::before {
		content: '';
		position: absolute;
		left: 5px;
		top: 0.85rem;
		bottom: -0.55rem;
		width: 1px;
		background: var(--member-line);
	}
	.link {
		position: relative;
		display: grid;
		grid-template-columns: 11px minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.2rem 0.65rem;
		padding: 0.4rem 0 0.4rem calc(var(--depth, 0) * 0.85rem);
		color: var(--ink-2);
	}
	/* The run stays at the ring column whatever the indent, so a nested ring still
	   knocks out of one continuous member. */
	.link .ring {
		margin-left: calc(var(--depth, 0) * -0.85rem);
	}
	.ring {
		position: relative;
		align-self: center;
		width: 11px;
		height: 11px;
		border: 1.5px solid var(--member-ink);
		border-radius: 50%;
		background: var(--plate);
	}
	.link[data-state='slack'] .ring {
		border-style: dashed;
	}
	.link[data-state='loaded'] .ring {
		background: var(--red);
	}
	.link[data-state='seated'] .ring {
		background: var(--seat);
	}
	/* A break in the load path: the ring is cut open left and right. */
	.link[data-state='failed'] .ring {
		border-left-color: transparent;
		border-right-color: transparent;
	}
	.link-ref {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--ink);
		text-decoration: none;
	}
	.link[data-state='slack'] .link-ref,
	.link[data-state='failed'] .link-ref {
		color: var(--ink-2);
	}
	a.link-ref:hover {
		color: var(--red);
	}
	.link-state {
		grid-column: 2;
		justify-self: start;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.link[data-state='failed'] .link-state {
		color: var(--red);
	}
	.link[data-state='slack'] .link-state {
		border-bottom: 1px dashed var(--ash);
	}
	/* The running total climbs beside the chain: the closure's cost at this ring,
	   not this asset's own. */
	.link-run {
		grid-row: 1;
		grid-column: 3;
		font-size: 0.8125rem;
		font-variant-numeric: tabular-nums;
		color: var(--ink-2);
	}

	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		margin: 0 0 1rem;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		flex: 1 1 9rem;
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
		overflow-wrap: anywhere;
	}
	.of {
		color: var(--ink-2);
	}
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}

	/* --- findings -------------------------------------------------------------- */
	.findings {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.findings.inline {
		margin: 0.75rem 0 0;
	}
	.finding {
		display: grid;
		gap: 0.1rem;
		padding: 0.5rem 0;
		border-top: 1px solid var(--rule);
		color: var(--ink-2);
	}
	.finding .label {
		color: var(--member-ink);
		justify-self: start;
	}
	.finding[data-state='slack'] .label {
		color: var(--ink-2);
		border-bottom: 1px dashed var(--ash);
	}
	.finding-ref {
		color: var(--ink);
		overflow-wrap: anywhere;
	}
	.finding-msg,
	.finding-detail {
		overflow-wrap: anywhere;
	}
	.finding-detail {
		font-size: 0.75rem;
		color: var(--ink-2);
	}
	.finding-detail .label {
		margin-right: 0.35rem;
	}

	.incoming {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem 1rem;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.bare {
		font: inherit;
		color: var(--ink);
		background: transparent;
		border: 0;
		padding: 0;
		cursor: pointer;
		overflow-wrap: anywhere;
	}
	.bare:hover {
		color: var(--red);
	}

	/* --- the documents ---------------------------------------------------------- */
	.docs {
		min-width: 0;
	}
	.doc-block {
		min-width: 0;
		padding-bottom: 2rem;
	}
	.doc-block + .doc-block {
		border-top: 1px solid var(--rule);
		padding-top: 0.5rem;
	}
	.doc-title {
		margin: 0 0 0.6rem;
		font-weight: 500;
		color: var(--ink);
		overflow-wrap: anywhere;
	}
	/* One readable dimensions line, wrapping as a paragraph when the column is narrow. */
	.dims {
		display: block;
		margin: 0 0 1rem;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.dim-v {
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--ink);
	}
	.dim-v.member {
		color: var(--member-ink);
	}
	.dim-v.member[data-state='slack'] {
		color: var(--ink-2);
		border-bottom: 1px dashed var(--ash);
	}

	.state {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.5rem;
		margin: 0 0 1rem;
		color: var(--member-ink);
	}
	.bar {
		display: block;
		width: 8rem;
		height: 1.25px;
		background: currentColor;
		transform-origin: left center;
		animation: take-up-load 1.1s cubic-bezier(0.16, 1, 0.3, 1) infinite;
	}

	/* Red marks the break, in one lead sentence; the sentence that explains it is
	   graphite prose. A paragraph is never set in red. */
	.lead {
		margin: 0 0 0.4rem;
		color: var(--member-ink);
	}
	.note {
		margin: 0 0 1.25rem;
		color: var(--ink-2);
	}
	.quiet {
		color: var(--ink-2);
	}
	.small {
		font-size: 0.75rem;
		margin: 0 0 1rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
		overflow-wrap: anywhere;
	}
	.ghost {
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
	.ghost:hover {
		border-color: var(--red);
		color: var(--red);
	}
	@media (max-width: 60rem) {
		.caption-row { flex-wrap: wrap; }
		.read-controls { width: 100%; align-items: flex-start; }
		.filters { grid-template-columns: minmax(0, 1fr); align-items: flex-start; }
		.chips { flex-wrap: wrap; }
		.picks { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.pick.find { grid-column: 1 / -1; }
		.frozen-picks { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.sheet-grid {
			grid-template-columns: minmax(0, 1fr);
			gap: 1.75rem;
		}
		/* A sticky chain in a single column would sit on top of the document it
		   indexes, so it simply scrolls with it. */
		.chain-col {
			position: static;
		}
		.rail {
			gap: 0.25rem 1.25rem;
		}
		.gloss:not(.caption-gloss) {
			display: none;
		}
	}
</style>
