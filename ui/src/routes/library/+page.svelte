<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import AssetActions from '$lib/AssetActions.svelte';
	import { LibraryWatch } from '$lib/libraryWatch.svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import Button from '$lib/Button.svelte';
	import Icon from '$lib/Icon.svelte';
	import MarkdownReader from '$lib/MarkdownReader.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import Tabs, { panelId, tabId, type TabItem } from '$lib/Tabs.svelte';
	import { parseMarkdown } from '$lib/markdown';
	import { useTitleActions } from '$lib/shell.svelte';
	import MemoryShelf from '$lib/MemoryShelf.svelte';
	import { leafRows, filterLeaves, memoryBudget, type MemoryRead, type MemoryShelfStatus } from '$lib/memory';
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
		ISSUE_WORD,
		referencedBy,
		statusState,
		type ClosureNode,
		type FrozenRow,
		type ShelfFilter
	} from '$lib/shelf';

	/* Shelf, approved bytes and memory answer different reading questions.
	   The two live reads share one all-status shelf; frozen bytes stay immutable. */
	type Read = 'shelf' | 'frozen' | 'memory';
	let read = $state<Read>('shelf');

	const shelf = new Resource<AssetSummary[]>((signal) => daemon.library(signal));
	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	const fleet = new Resource<Fleet>((signal) => daemon.fleet(signal));

	/** Every readable asset by ref, retired included — the closure walk needs both. */
	const index = $derived(new Map((shelf.data ?? []).map((row) => [row.ref, row])));

	let filter = $state<ShelfFilter>({ ...ALL });
	const shelfRows = $derived((shelf.data ?? []).filter((row) => row.kind !== 'memory-leaf'));
	const listed = $derived(filterShelf(shelfRows, filter));
	const groups = $derived(groupByKind(listed));

	/* --- selection -----------------------------------------------------------
	   Held as a ref and nothing positional, so a re-read that changes the shelf
	   cannot move what is being read. A filter that excludes the selected asset
	   does not drop the selection either: the register says it is no longer
	   listed, and the sheet keeps reading. */
	let selected = $state<string | null>(null);
	type Rtab = 'document' | 'refs' | 'used' | 'versions';
	let rtab = $state<Rtab>('document');

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
	const watch = new LibraryWatch();
	let changedAt = $state<Record<string, string>>({});
	let editRef = $state<string | null>(null);
	let assetOutcome = $state('');
	let newOutcome = $state('');
	let createOpen = $state(false);
	let mounted = $state(false);
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
				held[ref]?.phase === 'ready' && held[ref].asset?.digest === index.get(ref)?.digest
					? held[ref]
					: { phase: 'loading', asset: held[ref]?.asset ?? null, error: '' };
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

	/* A selection or a read switch is navigation here, so it takes a history
	   entry and Back/Forward walk it: the address effect below re-derives both
	   from the URL. A real navigation, not shallow pushState — `page.url` does not
	   follow a shallow entry on popstate. The route has no load, so it is free.
	   An unchanged address adds nothing. */
	function navigate(url: URL): void {
		if (url.href !== page.url.href) void goto(url, { noScroll: true, keepFocus: true });
	}

	function select(ref: string | null): void {
		if (ref !== null) read = ref.startsWith('memory-leaf/') ? 'memory' : 'shelf';
		assetOutcome = ''; newOutcome = '';
		selected = ref;
		rtab = 'document';
		const url = new URL(page.url);
		url.searchParams.set('read', read);
		if (ref === null) url.searchParams.delete('asset');
		else url.searchParams.set('asset', ref);
		navigate(url);
	}

	/* The address is a live input, not a one-time mount read: a jump from the
	   band while already on /library changes the selection. Held in a plain
	   value, not `$state` — an effect that reads and writes one reactive value
	   re-triggers itself on its own write, and the selection is written here.
	   select()'s own write finds the address already held and stops there. */
	let addressed: string | undefined;
	$effect(() => {
		const asset = page.url.searchParams.get('asset');
		const requested = page.url.searchParams.get('read');
		const key = `${requested ?? ''}|${asset ?? ''}`;
		if (key === addressed) return;
		addressed = key;
		if (asset?.startsWith('memory-leaf/')) read = 'memory';
		else if (requested === 'memory' || requested === 'frozen') read = requested;
		else read = 'shelf';
		selected = asset || null;
		rtab = 'document';
	});

	function setRead(next: Read): void {
		read = next;
		const url = new URL(page.url);
		url.searchParams.set('read', next);
		if (selected !== null && (next === 'memory') !== selected.startsWith('memory-leaf/')) {
			selected = null;
			url.searchParams.delete('asset');
		}
		navigate(url);
	}

	// One effect, one direction: the selection drives the reads, and neither read
	// writes back to it. An effect that reads and writes one reactive value
	// re-triggers itself on its own write and saturates the queue.
	$effect(() => {
		if (read === 'frozen') return;
		const walk = closure;
		if (walk === null) {
			if (selected !== null && held[selected]?.asset) return;
			docRun += 1;
			issuesRun += 1;
			held = {};
			docs = {};
			issues = [];
			issuesPhase = 'idle';
			return;
		}
		// One body: the selected asset's. The References tab walks summaries only.
		void loadDocs([walk.root]);
		void loadIssues(walk.root);
	});

	/* Memory list costs summaries only. Validate individual leaves for the size
	   readout; bodies are read only for selection and the conflicted cohort. */
	let memoryStatus = $state<MemoryShelfStatus>('current');
	let memoryQuery = $state('');
	const leaves = $derived(leafRows(shelf.data ?? []));
	const memoryListed = $derived(filterLeaves(leaves, memoryStatus, memoryQuery));
	const capabilities = new Resource((signal) => daemon.memoryCapabilities(signal));
	const memoryChecks = new Resource<Record<string, MemoryRead>>(async (signal) => {
		const refs = leaves.map((leaf) => leaf.ref);
		const result: Record<string, MemoryRead> = {};
		// Four workers bound the validation fan-out even on a large shelf.
		let cursor = 0;
		await Promise.all(Array.from({ length: Math.min(4, refs.length) }, async () => {
			while (cursor < refs.length && !signal.aborted) {
				const ref = refs[cursor++];
				try {
					const answer = await daemon.validateAssets([ref], ref, signal);
					result[ref] = { asset: null, issues: answer.issues, error: '', issuesError: '' };
				} catch (cause) {
					result[ref] = { asset: null, issues: null, error: '', issuesError: cause instanceof Error ? cause.message : 'Findings unread.' };
				}
			}
		}));
		return result;
	});
	const conflictRead = new Resource<Asset[]>(async (signal) => {
		const refs = leaves.filter((leaf) => leaf.status === 'conflicted').map((leaf) => leaf.ref);
		const assets: Asset[] = [];
		let cursor = 0;
		await Promise.all(Array.from({ length: Math.min(4, refs.length) }, async () => {
			while (cursor < refs.length && !signal.aborted) assets.push(await daemon.asset(refs[cursor++], signal));
		}));
		return assets;
	});
	const memoryReads = $derived.by(() => {
		const result = { ...(memoryChecks.data ?? {}) };
		if (selected !== null && selected.startsWith('memory-leaf/') && docs[selected]) {
			result[selected] = {
				asset: docs[selected].asset, error: docs[selected].error,
				issues: issuesPhase === 'ready' ? issues : null,
				issuesError: issuesPhase === 'error' ? issuesError : 'Reading the findings…'
			};
		}
		return result;
	});
	$effect(() => {
		if (read !== 'memory' || shelf.data === null) return;
		void memoryChecks.load();
	});
	$effect(() => {
		if (read !== 'memory' || selectedRow?.status !== 'conflicted') return;
		// Only the conflicted cohort needs bodies to compare subject_key.
		void conflictRead.load();
	});
	function retryMemory(): void {
		held = {};
		if (selected !== null) { void loadDocs([selected]); void loadIssues(selected); }
		void memoryChecks.load();
		if (selectedRow?.status === 'conflicted') void conflictRead.load();
	}

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

	// Shelf summaries can change the closure; only invalidated on-screen bodies
	// are fetched again. Cached bodies remain visible throughout the read.
	let refreshQueue = Promise.resolve();
	function refresh(changed: string[] = Object.keys(held)): Promise<void> {
		refreshQueue = refreshQueue.then(async () => {
			const onscreen = read !== 'frozen' ? changed.filter((ref) => held[ref] !== undefined) : [];
			await shelf.load();
			await Promise.all(onscreen.map(async (ref) => {
				if (!index.has(ref)) return;
				try {
					const asset = await daemon.asset(ref);
					if (held[ref]) {
						held = { ...held, [ref]: { phase: 'ready', asset, error: '' } };
						docs = held;
					}
				} catch (cause) {
					if (held[ref]) {
						held = { ...held, [ref]: { ...held[ref], error: cause instanceof Error ? cause.message : 'Could not re-read.' } };
						docs = held;
					}
				}
			}));
			if (read !== 'frozen' && selected && index.has(selected)) await loadIssues(selected);
		});
		return refreshQueue;
	}
	async function afterWrite(ref: string, edit = false): Promise<void> {
		await shelf.load();
		editRef = edit ? ref : null;
		if (edit) createOpen = false;
		select(ref);
	}

	onMount(() => {
		mounted = true;
		void shelf.load();
		void kitchen.load();
		void capabilities.load();
		void fleet.load();
		watch.start((changed) => {
			// Hidden live bodies are invalidated, never fetched behind the frozen read.
			if (read === 'frozen') {
				const invalidated = new Set(changed.length ? changed : Object.keys(held));
				held = Object.fromEntries(Object.entries(held).map(([ref, doc]) =>
					[ref, invalidated.has(ref) ? { ...doc, phase: 'loading' } : doc]
				));
			}
			const time = new Date().toLocaleTimeString();
			for (const ref of changed) if (read !== 'frozen' && held[ref]) changedAt = { ...changedAt, [ref]: time };
			void refresh(changed.length ? changed : undefined);
		});
		const onFocus = () => {
			if (!document.hidden && !watch.connected) {
				void refresh();
				if (read === 'frozen') void fleet.load();
			}
		};
		window.addEventListener('focus', onFocus);
		return () => {
			watch.dispose();
			window.removeEventListener('focus', onFocus);
			shelf.dispose();
			kitchen.dispose();
			fleet.dispose();
			planRead.dispose();
			capabilities.dispose();
			memoryChecks.dispose();
			conflictRead.dispose();
		};
	});

	function openFrozen(): void {
		setRead('frozen');
		if (!fleet.hasData) void fleet.load();
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

	/* --- presentation ----------------------------------------------------------
	   Everything below only draws what the logic above already holds. */
	let vw = $state(1440);
	const KIND_ONE: Record<string, string> = {
		role: 'Role', contract: 'Contract', skill: 'Skill', agent: 'Agent',
		'checkpoint-template': 'Checkpoint template', 'memory-leaf': 'Memory leaf'
	};
	/** The kind's emission line (DS §9.21). */
	const KIND_LINE: Record<string, string> = {
		role: 'var(--l486)', contract: 'var(--l405)', skill: 'var(--l546)',
		agent: 'var(--tx2)', 'checkpoint-template': 'var(--dim)'
	};
	const KIND_OPTIONS = [
		{ id: 'all', label: 'All' }, { id: 'role', label: 'Roles' },
		{ id: 'contract', label: 'Contracts' }, { id: 'skill', label: 'Skills' }
	] as const;
	const MEMORY_OPTIONS = [
		{ value: 'current', label: 'Current' }, { value: 'active', label: 'Active only' },
		{ value: 'stale', label: 'Stale' }, { value: 'conflicted', label: 'Conflicted' },
		{ value: 'retired', label: 'Retired' }, { value: 'all', label: 'All' }
	] as const;

	const bundledCount = $derived(shelfRows.filter((row) => row.origin === 'bundled').length);
	const projectCount = $derived(shelfRows.length - bundledCount);

	const readTabs = $derived<TabItem[]>([
		{ id: 'shelf', label: 'Live shelf', icon: 'book-open', count: shelf.data ? count(shelfRows.length) : undefined },
		{ id: 'frozen', label: 'Approved plan', icon: 'file-text' },
		{ id: 'memory', label: 'Memory', icon: 'brain', count: shelf.data ? count(leaves.length) : undefined }
	]);
	function pickRead(id: string): void {
		if (id === 'frozen') openFrozen();
		else setRead(id as Read);
	}

	const doc = $derived(selected === null ? undefined : docs[selected]);
	const readerTabs = $derived<TabItem[]>([
		{ id: 'document', label: 'Document' },
		{ id: 'refs', label: 'References', count: closure ? count(closure.nodes.length - 1) : undefined,
			countTone: closure && closure.missing.length > 0 ? 'bad' : undefined },
		{ id: 'used', label: 'Used by', count: count(incoming.length) },
		{ id: 'versions', label: 'Versions', count: 1 }
	]);
	const label = (row: { kind: string; origin: string; status: string; digest: string }): string =>
		[KIND_ONE[row.kind] ?? row.kind, ORIGIN_WORD[row.origin as AssetOrigin].toLowerCase(),
			...(row.status !== 'active' ? [STATUS_WORD[row.status as AssetStatus].toLowerCase()] : []),
			`rev ${row.digest.slice(0, 6)}`].join(' · ');

	/** The contents rail earns its 220px only with a long document and a wide window. */
	const wantToc = (body: string): boolean =>
		vw >= 1280 && parseMarkdown(body).filter((block) => block.kind === 'heading' && (block.level === 2 || block.level === 3)).length >= 3;

	const sub = (row: AssetSummary): string =>
		[row.origin + (row.shadows_bundled ? ' override' : ''), `${count(row.tokens)} tok`,
			...(row.references.length > 0 ? [`${count(row.references.length)} ref`] : [])].join(' · ');

	const NODE_TONE: Record<ClosureNode['state'], string> = {
		root: '', present: 'settled', retired: 'waiting', missing: 'failed', cycle: 'failed'
	};

	function retryDoc(): void {
		held = {};
		if (selected !== null) { void loadDocs([selected]); void loadIssues(selected); }
	}

	/* ↑/↓ roving through whichever asset list holds focus. */
	function roving(event: KeyboardEvent): void {
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		const all = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('button.asset')];
		const at = all.indexOf(document.activeElement as HTMLElement);
		const next = all[event.key === 'ArrowDown' ? Math.min(at + 1, all.length - 1) : Math.max(at - 1, 0)];
		if (!next) return;
		event.preventDefault();
		next.focus();
	}
	const rove = $derived(inRegister ? selected : (listed[0]?.ref ?? null));

	let frozenSelected = $state<string | null>(null);
	const frozenCurrent = $derived<FrozenRow | null>(
		frozenRows.find((row) => row.ref === frozenSelected) ?? frozenRows[0] ?? null
	);
	const frozenRove = $derived(frozenCurrent?.ref ?? null);
	const DRIFT_TONE = { same: 'settled', edited: 'waiting', gone: 'waiting' } as const;

	useTitleActions(() => (read === 'shelf' ? newAsset : null));
</script>

<svelte:head><title>Library — Herdsman</title></svelte:head>
<svelte:window bind:innerWidth={vw} />

{#snippet newAsset()}
	<Button icon="plus" small onclick={() => { createOpen = true; newOutcome = ''; }}>New asset</Button>
{/snippet}

{#snippet issueList(items: LibraryIssue[])}
	{#each items as issue, n (issue.code + issue.ref + n)}
		<div class="stmt find" data-tone={issue.severity === 'error' ? 'failed' : 'waiting'}>
			<span class="lbl">{ISSUE_WORD[issue.code] ?? issue.code}</span>
			<p class="mono ref">{issue.ref}</p>
			<p>{issue.message}</p>
			{#if issue.detail !== ''}<p class="hint">{#if issue.code === 'context-size'}Largest · {/if}{issue.detail}</p>{/if}
		</div>
	{/each}
{/snippet}

<!-- The front-matter strip: what the daemon knows about this asset, as marks. -->
{#snippet front(row: AssetSummary, own: LibraryIssue[])}
	<div class="front">
		<div><span class="lbl">Ref</span><span class="mono">{row.ref}</span></div>
		<div><span class="lbl">Tokens</span><span class="mono">{count(row.tokens)}</span></div>
		<div><span class="lbl">References</span>{#if row.references.length === 0}<span class="mono muted">—</span>{:else}<span class="refs">{#each row.references as ref (ref)}<button type="button" class="bare mono" onclick={() => select(ref)}>{ref}</button>{/each}</span>{/if}</div>
		<div><span class="lbl">Origin</span><span class="state" data-tone="idle">{row.origin === 'bundled' ? 'Bundled · read-only' : row.shadows_bundled ? 'Project · overrides bundled' : 'Project'}</span></div>
		{#if row.status !== 'active'}<div><span class="lbl">Status</span><span class="state" data-tone={row.status === 'conflicted' ? 'failed' : 'waiting'}>{STATUS_WORD[row.status]}</span></div>{/if}
		{#if changedAt[row.ref]}<div><span class="lbl">Changed on disk</span><span class="mono">{changedAt[row.ref]}</span></div>{/if}
		{#if !inRegister}<div><span class="lbl">Filter</span><span class="state" data-tone="waiting" title="The filter does not list this asset; it is still selected and read.">Not in the list</span></div>{/if}
	</div>
	{#if own.length > 0}<div class="finds">{@render issueList(own)}</div>{/if}
{/snippet}

<div class="page">
	<section class="ph">
		<div class="title">
			<h1 class="h-title">Library</h1>
			<div class="meta">
				<span class="lbl" aria-live="polite">{#if !shelf.data}—{:else if read === 'memory'}{#if memoryListed.length === leaves.length}{count(leaves.length)} leaves{:else}{count(memoryListed.length)} of {count(leaves.length)} leaves{/if}{:else if listed.length === shelfRows.length}{count(shelfRows.length)} assets · {count(bundledCount)} bundled · {count(projectCount)} project{:else}{count(listed.length)} of {count(shelfRows.length)} assets · {count(bundledCount)} bundled · {count(projectCount)} project{/if}</span>
				{#if mounted && watch.connected}<span class="live" title="Following the shelf"></span>{:else if mounted}<span class="state" data-tone="waiting" role="status">Live updates disconnected — re-reading on focus</span>{/if}
				{#if newOutcome}<span class="state" data-tone="settled" role="status">{newOutcome}</span>{/if}
			</div>
		</div>
	</section>

	<nav class="subnav" aria-label="Library">
		<Tabs items={readTabs} selected={read} prefix="lib" label="Which set to read" onselect={pickRead} />
		<div class="tools">
			{#if read === 'shelf'}
				<Segmented options={KIND_OPTIONS} value={filter.kind === 'all' || filter.kind === 'role' || filter.kind === 'contract' || filter.kind === 'skill' ? filter.kind : 'all'} label="Kind" onchange={(kind) => (filter = { ...filter, kind })} />
				<select class="sel t-origin" aria-label="Origin" value={filter.origin} onchange={(event) => (filter = { ...filter, origin: event.currentTarget.value as AssetOrigin | 'all' })}><option value="all">Bundled + project</option><option value="bundled">Bundled only</option><option value="project">Project only</option></select>
				<select class="sel t-status" aria-label="Status" value={filter.status} onchange={(event) => (filter = { ...filter, status: event.currentTarget.value as AssetStatus | 'all' })}><option value="active">Active</option><option value="all">With archived</option><option value="retired">Archived only</option></select>
				<span class="find"><Icon name="search" size={14} /><input class="inp" type="search" aria-label="Find by ref or title" placeholder="ref or title" value={filter.query} oninput={(event) => (filter = { ...filter, query: event.currentTarget.value })} /></span>
			{:else if read === 'memory'}
				<select class="sel t-origin" aria-label="Memory status" bind:value={memoryStatus}>{#each MEMORY_OPTIONS as option (option.value)}<option value={option.value}>{option.label}</option>{/each}</select>
				<span class="find"><Icon name="search" size={14} /><input class="inp" type="search" aria-label="Find by subject or ref" placeholder="subject or ref" bind:value={memoryQuery} /></span>
			{:else}
				<select class="sel t-plan" aria-label="Approved run" bind:value={frozenPlan}><option value="">Choose a run…</option>{#each approvedRuns as run (run.plan_id)}<option value={run.plan_id}>{run.plan_id} — v{run.version}</option>{/each}</select>
				<select class="sel t-status" aria-label="Approved version" bind:value={frozenVersion} disabled={versions.length === 0}>{#if versions.length === 0}<option value="">Version</option>{:else}{#each versions as version (version)}<option value={version}>Version {version}</option>{/each}{/if}</select>
			{/if}
		</div>
	</nav>

	{#if read === 'memory'}
		<div class="body" role="tabpanel" id={panelId('lib', 'memory')} aria-labelledby={tabId('lib', 'memory')}>
			<AsyncField resource={shelf} reading="the memory shelf" onretry={() => void shelf.load()}>
				{#snippet children()}
					<div class="lib">
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div class="shelf" onkeydown={roving}>
							<MemoryShelf mode="list" rows={leaves} reads={memoryReads} {selected} onselect={select} status={memoryStatus} query={memoryQuery} onretry={retryMemory} />
						</div>
						<div class="reader">
							{#if selected !== null && selected.startsWith('memory-leaf/')}
								<MemoryShelf mode="reader" rows={leaves} reads={memoryReads} {selected} onselect={select} planIds={(fleet.data?.runs ?? []).map((run) => run.plan_id)}
									reading={docs[selected]?.phase === 'loading'} conflictAssets={conflictRead.data ?? []} conflictError={conflictRead.error?.message ?? ''} onretry={retryMemory}>
									{#snippet actions(heading: string, rule: string)}
										{#if selectedRow}
											{#key selectedRow.ref}<AssetActions asset={selectedRow} label={rule} {heading} incoming={incoming.length} connected={watch.connected} changed={changedAt[selectedRow.ref] ?? ''} autoEdit={editRef === selectedRow.ref} onwrite={afterWrite} onreread={() => refresh()} outcome={assetOutcome} onsuccess={(message) => assetOutcome = message} onclear={() => { assetOutcome = ''; newOutcome = ''; }} />{/key}
										{/if}
									{/snippet}
								</MemoryShelf>
							{:else}
								<MemoryShelf mode="readout" rows={leaves} reads={memoryReads} {selected} onselect={select}
									budget={memoryBudget(capabilities.data)} capabilityError={capabilities.error?.message ?? ''} onretry={retryMemory} />
							{/if}
						</div>
					</div>
				{/snippet}
			</AsyncField>
		</div>
	{:else if read === 'shelf'}
		<div class="body" role="tabpanel" id={panelId('lib', 'shelf')} aria-labelledby={tabId('lib', 'shelf')}>
			<AsyncField resource={shelf} reading="the shelf" onretry={() => void shelf.load()}>
				{#snippet skeleton()}
					<div class="skel" aria-hidden="true">{#each [0, 1, 2, 3, 4, 5] as n (n)}<i></i>{/each}</div>
				{/snippet}
				{#snippet children(_rows: AssetSummary[])}
					{#if shelfRows.length === 0 && selected === null}
						<p class="empty-line">The shelf is empty: this project ships and has authored no assets. <span class="mono">uv run python ui/dev/seed_library.py</span> seeds a set; New asset starts one.</p>
					{:else}
						<div class="lib">
							<!-- svelte-ignore a11y_no_static_element_interactions -->
							<div class="shelf" onkeydown={roving}>
								{#if createOpen}<AssetActions create onwrite={afterWrite} onreread={() => refresh()} onsuccess={(message) => newOutcome = message} onclear={() => newOutcome = ''} onclose={() => createOpen = false} />{/if}
								{#if listed.length === 0}
									<p class="empty-line">No asset matches this filter. {count(shelfRows.length)} on disk.</p>
								{:else}
									{#each groups as group (group.kind)}
										<div class="grp rule-h"><span class="lbl">{KIND_WORD[group.kind]}</span><span class="count r">{count(group.rows.length)}</span></div>
										<ul>
											{#each group.rows as row (row.ref)}
												<li><button type="button" class="asset" class:retired={row.status === 'retired'} data-asset={row.ref} style="--c:{KIND_LINE[row.kind] ?? 'var(--dim)'}"
													aria-current={selected === row.ref ? 'true' : undefined} tabindex={rove === row.ref ? 0 : -1} onclick={() => select(row.ref)}>
													<i class="em"></i>
													<span class="nm-wrap"><span class="nm ellipsis">{row.name}</span>
														<span class="sub ellipsis">{sub(row)}</span></span>
													{#if row.status !== 'active'}<span class="state" data-tone={row.status === 'conflicted' ? 'failed' : 'waiting'}>{STATUS_WORD[row.status]}</span>{:else}<span class="count">{row.digest.slice(0, 6)}</span>{/if}
												</button></li>
											{/each}
										</ul>
									{/each}
								{/if}
							</div>

							<div class="reader">
								{#if selected === null}
									<p class="empty-line">Choose an asset to read it.</p>
								{:else if selectedRow === null}
									<div class="solo">
										<div class="stmt" data-tone="waiting" role="status">
											<b>{docs[selected]?.asset ? 'No longer on the shelf' : 'Not on the shelf'}</b>
											<p><span class="mono">{selected}</span> is not on the shelf this read returned. It may have been renamed or archived, or the link names an asset this project never had.</p>
											<div class="btnrow"><Button icon="x" small onclick={() => select(null)}>Clear selection</Button></div>
										</div>
									</div>
									{#if docs[selected]?.asset}<div class="scroll"><MarkdownReader source={docs[selected].asset?.body ?? ''} /></div>{/if}
								{:else}
									{@const asset = selectedRow}
									{#key asset.ref}
										<div class="pane-in head-wrap">
											<AssetActions {asset} label={label(asset)} heading={asset.title !== '' ? asset.title : asset.name} incoming={incoming.length} connected={watch.connected} changed={changedAt[asset.ref] ?? ''} autoEdit={editRef === asset.ref}
												onversions={() => (rtab = 'versions')} onwrite={afterWrite} onreread={() => refresh()} outcome={assetOutcome}
												onsuccess={(message) => assetOutcome = message} onclear={() => { assetOutcome = ''; newOutcome = ''; }} />
										</div>
									{/key}
									<div class="rtabs"><Tabs items={readerTabs} selected={rtab} prefix="rd" label="Asset" small onselect={(id) => (rtab = id as Rtab)} /></div>
									<div class="scroll" role="tabpanel" id={panelId('rd', rtab)} aria-labelledby={tabId('rd', rtab)}>
										{#if rtab === 'document'}
											{#if doc === undefined || doc.phase === 'loading' && !doc.asset}
												<div class="solo" aria-busy="true">{@render front(asset, [])}<div class="skel doc" aria-hidden="true"><i></i><i></i><i></i></div><span class="lbl">Reading {asset.ref}…</span></div>
											{:else if doc.phase === 'error'}
												<div class="solo">{@render front(asset, [])}
													<div class="stmt" data-tone="failed" role="alert"><b>This body did not read</b><p class="mono">{doc.error}</p>
														<div class="btnrow"><Button icon="rotate-ccw" small onclick={retryDoc}>Read again</Button></div></div>
												</div>
											{:else if doc.asset}
												{#key asset.ref}
													<MarkdownReader source={doc.asset.body} toc={wantToc(doc.asset.body)}
														empty="This asset has no body. Its frontmatter is all there is; on a contract that is legitimate, the gates are the frontmatter.">
														{#snippet toolbar()}<div class="tb-wrap">{@render front(asset, issuesPhase === 'ready' ? assetIssues(asset.ref) : [])}</div>{/snippet}
													</MarkdownReader>
												{/key}
											{/if}
										{:else if rtab === 'refs'}
											<div class="solo wide">
												{#if closure}
													<div class="tele">
														<div><span class="lbl">Effective context</span><span class="num" class:bad={overBudget}>{count(closure.tokens)}{#if budget !== null}<small>/{count(budget)}</small>{/if}</span></div>
														<div><span class="lbl">Assets</span><span class="num">{count(closure.nodes.filter((node) => node.asset).length)}</span></div>
														<div><span class="lbl">Broken</span><span class="num" class:bad={closure.missing.length > 0}>{count(closure.missing.length)}</span></div>
													</div>
													<div class="rule-h"><span class="lbl">Chain</span><span class="count r">{count(closure.nodes.length)}</span></div>
													<ol class="chain">
														{#each closure.nodes as node, n (node.ref + ':' + n)}
															<li style="--depth: {node.depth}">
																<span class="dot" data-tone={NODE_TONE[node.state] || undefined} aria-hidden="true"></span>
																<span class="link" style="padding-left: {node.depth * 16}px">
																	{#if node.asset && node.state !== 'root'}<button type="button" class="bare mono" onclick={() => select(node.ref)}>{node.ref}</button>{:else}<span class="mono">{node.ref}</span>{/if}
																	{#if node.state !== 'present'}<span class="state" data-tone={NODE_TONE[node.state] || 'idle'}>{NODE_WORD[node.state]}</span>{/if}
																</span>
																<span class="count">{node.asset ? count(node.running) : '—'}</span>
															</li>
														{/each}
													</ol>
													<div class="rule-h gap"><span class="lbl">Findings</span></div>
													{#if issuesPhase === 'loading' || issuesPhase === 'idle'}
														<span class="lbl" aria-busy="true">Reading the findings…</span>
													{:else if issuesPhase === 'error'}
														<div class="stmt" data-tone="failed" role="alert"><b>Findings unread</b><p class="mono">{issuesError}</p><div class="btnrow"><Button icon="rotate-ccw" small onclick={() => void loadIssues(asset.ref)}>Read again</Button></div></div>
													{:else if issues.length === 0}
														<p class="hint">Nothing wrong with this set.</p>
													{:else}
														{@render issueList(issues)}
													{/if}
												{/if}
											</div>
										{:else if rtab === 'used'}
											<div class="solo wide">
												{#if incoming.length === 0}
													<p class="hint">Nothing on the shelf references this asset.</p>
												{:else}
													<table class="tbl">
														<thead><tr><th>Ref</th><th>Kind</th><th>Origin</th><th class="n">Tokens</th></tr></thead>
														<tbody>
															{#each incoming as ref (ref)}{@const user = index.get(ref)}
																<tr class="click"><td><button type="button" class="bare mono" onclick={() => select(ref)}>{ref}</button></td><td>{user ? (KIND_ONE[user.kind] ?? user.kind) : '—'}</td><td>{user ? ORIGIN_WORD[user.origin] : '—'}</td><td class="n">{user ? count(user.tokens) : '—'}</td></tr>
															{/each}
														</tbody>
													</table>
												{/if}
											</div>
										{:else}
											<div class="solo wide">
												<table class="tbl">
													<thead><tr><th>Revision</th><th>Status</th><th>Origin</th><th class="n">Tokens</th></tr></thead>
													<tbody><tr aria-current="true"><td class="mono">{asset.digest.slice(0, 12)}</td><td><span class="state" data-tone={asset.status === 'active' ? 'settled' : 'waiting'}>{STATUS_WORD[asset.status]}</span></td><td>{ORIGIN_WORD[asset.origin]}{asset.shadows_bundled ? ' · overrides bundled' : ''}</td><td class="n">{count(asset.tokens)}</td></tr></tbody>
												</table>
												<p class="hint">The shelf keeps one live revision; earlier edits live in your editor and git. Approved plans freeze their own copy — read those under Approved plan.</p>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						</div>
					{/if}
				{/snippet}
			</AsyncField>
		</div>
	{:else}
		<div class="body" role="tabpanel" id={panelId('lib', 'frozen')} aria-labelledby={tabId('lib', 'frozen')}>
			<AsyncField resource={fleet} reading="the fleet" onretry={() => void fleet.load()}>
				{#snippet children(view: Fleet)}
					{#if approvedRuns.length === 0}
						<p class="empty-line">No run is approved, so nothing has frozen a set of assets yet.{#if view.runs.length > 0} {count(view.runs.length)} {view.runs.length === 1 ? 'run exists and is' : 'runs exist and none is'} past its plan gate.{/if}</p>
					{:else if frozenPlan === ''}
						<p class="empty-line">Choose an approved run to read what its approval froze.</p>
					{:else}
						<AsyncField resource={planRead} reading="the approved plan" onretry={() => void planRead.load()}>
							{#snippet children(plan: Plan)}
								{#if versions.length === 0}
									<p class="empty-line">{plan.id} is approved but froze no assets — a plan approved before the Library landed, or one whose initiatives declared none. That is unknown, not an empty set.</p>
								{:else if snapshot}
									{#if snapshot.assets.length === 0}
										<p class="empty-line">Version {frozenVersion} of {plan.id} was approved with no assets declared, so it froze none.</p>
									{:else}
										<div class="lib">
											<!-- svelte-ignore a11y_no_static_element_interactions -->
											<div class="shelf" onkeydown={roving}>
												<div class="tele sum">
													<div><span class="lbl">Frozen</span><span class="num">{count(snapshot.assets.length)}</span></div>
													<div><span class="lbl">Tokens</span><span class="num">{count(snapshot.assets.reduce((sum, a) => sum + a.tokens, 0))}</span></div>
													<div><span class="lbl">Moved on</span><span class="num">{count(frozenRows.filter((row) => row.drift !== 'same').length)}</span></div>
												</div>
												<div class="grp rule-h"><span class="lbl">Frozen at approval</span><span class="count r">{count(frozenRows.length)}</span></div>
												<ul>
													{#each frozenRows as row (row.ref)}
														<li><button type="button" class="asset" data-asset={row.ref} style="--c:{KIND_LINE[row.kind] ?? 'var(--dim)'}"
															aria-current={frozenCurrent?.ref === row.ref ? 'true' : undefined} tabindex={frozenRove === row.ref ? 0 : -1} onclick={() => (frozenSelected = row.ref)}>
															<i class="em"></i>
															<span class="nm-wrap"><span class="nm ellipsis">{row.name}</span><span class="sub ellipsis">{row.origin} · {count(row.tokens)} tok · {row.digest.slice(0, 6)}</span></span>
															{#if row.drift !== 'same'}<span class="state" data-tone="waiting">{row.drift === 'gone' ? 'Gone' : 'Edited'}</span>{/if}
														</button></li>
													{/each}
												</ul>
											</div>
											<div class="reader">
												{#if frozenCurrent}
													{@const row = frozenCurrent}
													{#key row.ref}
														<div class="head pane-in">
															<div class="t"><span class="lbl">Frozen · {KIND_ONE[row.kind] ?? row.kind} · {row.origin} · rev {row.digest.slice(0, 6)}</span><h2 class="h-sec ellipsis">{row.title !== '' ? row.title : row.name}</h2></div>
															<span class="state" data-tone={DRIFT_TONE[row.drift]} role="status">{DRIFT_WORD[row.drift]}</span>
														</div>
													{/key}
													<div class="scroll">
														{#key row.ref}
															<MarkdownReader source={row.body} toc={wantToc(row.body)} empty="This asset was frozen with no body. Its frontmatter was all there was.">
																{#snippet toolbar()}
																	<div class="tb-wrap"><div class="front">
																		<div><span class="lbl">Ref</span><span class="mono">{row.ref}</span></div>
																		<div><span class="lbl">Tokens</span><span class="mono">{count(row.tokens)}</span></div>
																		<div><span class="lbl">References</span>{#if row.references.length === 0}<span class="mono muted">—</span>{:else}<span class="mono">{row.references.join(', ')}</span>{/if}</div>
																		{#if row.drift === 'edited'}<div><span class="lbl">Shelf now holds</span><span class="mono">{row.liveDigest?.slice(0, 12)}</span></div>{/if}
																	</div>
																	{#if snapshot.issues.length > 0}<div class="finds">{@render issueList(snapshot.issues)}</div>{/if}</div>
																{/snippet}
															</MarkdownReader>
														{/key}
													</div>
												{/if}
											</div>
										</div>
									{/if}
								{/if}
							{/snippet}
						</AsyncField>
					{/if}
				{/snippet}
			</AsyncField>
		</div>
	{/if}
</div>

<style>
	.page { display: grid; grid-template-rows: auto auto minmax(0, 1fr); min-height: 0; min-width: 0; }
	.subnav { flex-wrap: wrap; row-gap: 0; }
	.subnav .tools { flex-wrap: wrap; justify-content: flex-end; padding: 5px 0; min-width: 0; }
	.t-origin { width: 150px; height: 28px; }
	.t-status { width: 120px; height: 28px; }
	.t-plan { width: 260px; height: 28px; }
	.find { position: relative; display: block; width: 190px; }
	.find :global(svg) { position: absolute; left: 9px; top: 7px; color: var(--dim); pointer-events: none; }
	.find .inp { height: 28px; padding-left: 30px; }
	.body { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
	.lib { flex: 1; display: grid; grid-template-columns: 340px minmax(0, 1fr); min-height: 0; }
	.shelf { border-right: 1px solid var(--ln); overflow: auto; min-height: 0; padding: 0 0 24px; }
	.grp { margin: 0; padding: 16px 20px 6px 24px; }
	.shelf ul { margin: 0; padding: 0; }
	.asset {
		display: grid; grid-template-columns: 2px minmax(0, 1fr) auto; gap: 0 14px; align-items: center; width: 100%;
		text-align: left; padding: 9px 20px 9px 24px; transition: background var(--t-fast);
	}
	.asset:hover { background: var(--p1); }
	.asset[aria-current] { background: var(--p2); }
	.asset[aria-current] .em { box-shadow: 3px 0 0 var(--c); }
	.em { height: 30px; background: var(--c); }
	.retired .em { background: repeating-linear-gradient(var(--c) 0 4px, transparent 4px 7px); }
	.retired .nm { color: var(--dim); }
	.nm-wrap { display: grid; gap: 2px; min-width: 0; }
	.nm { font: 500 14px var(--f-ui); color: var(--tx); }
	.sub { font: 400 11.5px var(--f-mono); color: var(--dim); }
	.reader { display: grid; grid-template-rows: auto auto minmax(0, 1fr); min-height: 0; min-width: 0; }
	.head { display: flex; align-items: center; gap: 16px; padding: 16px 28px 12px; }
	.head .t { flex: 1; min-width: 0; display: grid; gap: 4px; }
	.head .h-sec { margin: 0; }
	.head-wrap { min-width: 0; }
	.rtabs { padding: 0 28px; border-bottom: 1px solid var(--ln); }
	.scroll { overflow: auto; min-height: 0; }
	.solo { padding: 22px 28px 40px; display: grid; gap: 16px; align-content: start; max-width: calc(var(--measure) + 56px); }
	.solo.wide { max-width: 880px; }
	.reader > .empty-line { grid-row: 1 / -1; }
	.tb-wrap { flex: 1; min-width: 0; }
	.front { display: flex; flex-wrap: wrap; gap: 8px 24px; }
	.front > div { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
	.refs { display: flex; flex-wrap: wrap; gap: 2px 12px; }
	.bare { color: var(--tx); text-align: left; overflow-wrap: anywhere; }
	.bare:hover { text-decoration: underline dashed var(--ln2); text-underline-offset: 4px; }
	.finds { display: grid; gap: 12px; margin-top: 16px; }
	.find { display: grid; grid-template-columns: minmax(0, 1fr); gap: 3px; }
	.find .ref { color: var(--tx); overflow-wrap: anywhere; }
	.gap { margin-top: 12px; }
	.tele { margin-bottom: 4px; }
	.tele > div:first-child { border-left: 0; padding-left: 0; }
	.tele small { font-size: 12px; }
	.tele .num.bad { color: var(--l656); }
	.sum { padding: 16px 24px 4px; }
	.chain li { grid-template-columns: 18px minmax(0, 1fr) auto; }
	.link { display: flex; align-items: center; gap: 12px; min-width: 0; }
	.skel { display: grid; gap: 1px; padding: 8px 24px; }
	.skel i { display: block; height: 30px; background: var(--p1); margin-bottom: 8px; }
	.skel.doc { padding: 0; }
	.skel.doc i { height: 14px; }
	.skel.doc i:nth-child(2) { width: 82%; }
	.skel.doc i:nth-child(3) { width: 64%; }

	@media (max-width: 767px) {
		.lib { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 38%) minmax(0, 1fr); }
		.shelf { border-right: 0; border-bottom: 1px solid var(--ln); }
	}
</style>
