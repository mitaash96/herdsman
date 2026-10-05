<script lang="ts">
	/*
	  Locate (DS §9.2): the titleblock's palette. Same index as the retired band
	  (`locate.ts`, unchanged): views, every run, the shelf, and the addressed
	  plan's members and checkpoints. Non-modal: it never dims, never traps focus,
	  and hands focus back to its opener on close. It can only navigate to a row
	  the daemon enumerated, never to typed text.
	*/
	import { getContext, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { VIEWS } from '$lib/views';
	import { daemon, type AssetSummary, type CheckpointReport, type Fleet, type PlanGraph } from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';
	import { buildIndex, filterRows, groupRows, step, type LocateKind, type LocateRow } from '$lib/locate';
	import type { MemberState } from '$lib/field';
	import type { Tone } from '$lib/tones';
	import type { IconName } from '$lib/icons';
	import Icon from './Icon.svelte';

	let { open, onclose }: { open: boolean; onclose: (refocus: boolean) => void } = $props();

	const plan = getContext<{ readonly resource: Resource<PlanGraph> | null; readonly id: string | null }>('plan');
	const fleetCtx = getContext<{ readonly resource: Resource<Fleet> }>('fleet');

	/* Held for the session once first opened: a locator that re-reads per keystroke is a poll. */
	let opened = false;
	let archived = $state<Resource<Fleet> | null>(null);
	let shelf = $state<Resource<AssetSummary[]> | null>(null);
	$effect(() => {
		if (!open || opened) return;
		opened = true;
		archived = new Resource((s) => daemon.fleetArchived(s));
		void archived.load();
		shelf = new Resource((s) => daemon.library(s));
		void shelf.load();
	});

	let reviews = $state<Resource<CheckpointReport> | null>(null);
	let requested: string | null = null;
	$effect(() => {
		const id = plan.id;
		if (!open || id === requested) return;
		requested = id;
		reviews?.dispose();
		reviews = id ? new Resource((s) => daemon.checkpoints(id, s)) : null;
		void reviews?.load();
	});

	let query = $state('');
	const index = $derived(
		buildIndex({
			views: VIEWS,
			fleet: fleetCtx.resource.data,
			archived: archived?.data ?? null,
			assets: shelf?.data ?? null,
			graph: plan.resource?.data ?? null,
			report: reviews?.data ?? null,
			planId: plan.id
		})
	);
	const groups = $derived(groupRows(filterRows(index, query)));
	const visible = $derived(groups.flatMap((g) => g.rows));
	let activeKey = $state<string | null>(null);

	/* Re-aim on a new query or when the held row leaves; a live read never moves the reading position. */
	let seenQuery: string | null = null;
	$effect(() => {
		if (!open) {
			seenQuery = null;
			return;
		}
		const rows = visible;
		const q = query;
		const held = untrack(() => activeKey);
		const keep = q === seenQuery && held !== null && rows.some((r) => r.key === held);
		seenQuery = q;
		if (!keep) activeKey = rows[0]?.key ?? null;
	});
	$effect(() => {
		if (open && activeKey) document.getElementById(`loc-${activeKey}`)?.scrollIntoView({ block: 'nearest' });
	});

	let input = $state<HTMLInputElement>();
	let root = $state<HTMLDivElement>();
	$effect(() => {
		if (open) tick().then(() => input?.select());
	});

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			activeKey = step(visible, activeKey, event.key === 'ArrowDown' ? 1 : -1);
		} else if (event.key === 'Enter') {
			event.preventDefault();
			void go();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			onclose(true);
		}
	}

	async function go() {
		const row = visible.find((r) => r.key === activeKey);
		if (!row) return;
		onclose(false);
		await goto(row.path);
		await tick();
		const main = document.getElementById('main');
		if (main && !main.contains(document.activeElement)) main.focus();
	}

	const KIND_ICON: Record<LocateKind, IconName> = {
		attention: 'bell-dot',
		view: 'layout-grid',
		run: 'workflow',
		member: 'circle-dot',
		checkpoint: 'check-check',
		asset: 'book-open'
	};
	const TONE: Record<MemberState, Tone> = {
		loaded: 'running',
		failed: 'failed',
		seated: 'settled',
		balanced: 'waiting',
		slack: 'idle'
	};
	const toneOf = (row: LocateRow): Tone => (row.kind === 'attention' && row.memberState !== 'failed' ? 'needs' : TONE[row.memberState]);

	const notices = $derived(
		[
			{ what: 'the fleet', r: fleetCtx.resource as Resource<unknown> },
			{ what: 'the archived runs', r: archived as Resource<unknown> | null },
			{ what: 'the shelf', r: shelf as Resource<unknown> | null },
			{ what: 'the checkpoint report', r: reviews as Resource<unknown> | null }
		].filter((n) => n.r && (n.r.phase === 'loading' || (n.r.error && (n.r.phase === 'error' || n.r.stale))))
	);
</script>

<svelte:window onpointerdown={(e) => { if (open && root && !root.contains(e.target as Node) && !(e.target as Element).closest?.('.locate-trigger')) onclose(false); }} />

{#if open}
	<div class="pal pane-in" bind:this={root} role="search" aria-label="Locate">
		<div class="q">
			<Icon name="search" />
			<input
				bind:this={input}
				bind:value={query}
				type="text"
				role="combobox"
				aria-expanded={visible.length > 0}
				aria-controls="locate-list"
				aria-activedescendant={activeKey ? `loc-${activeKey}` : undefined}
				aria-autocomplete="list"
				aria-label="Filter runs, members, checkpoints and assets"
				placeholder="Locate runs, members, checkpoints, assets"
				autocomplete="off"
				spellcheck="false"
				{onkeydown}
			/>
			<span class="kbd">Esc</span>
		</div>
		<div class="res-wrap" id="locate-list" role="listbox" aria-label="Matching rows">
			{#if visible.length === 0}
				<p class="none hint" role="status">
					{query ? 'Nothing matches.' : 'Nothing indexed yet.'}{#if !plan.id} Members and checkpoints appear when a run is open.{/if}
				</p>
			{/if}
			{#each groups as g (g.kind)}
				<div role="group" aria-label={g.label}>
					<p class="grp rule-h" aria-hidden="true">
						<span class="lbl">{g.label}</span>
						{#if g.total > g.rows.length}<span class="r count">{g.rows.length} of {g.total}</span>{/if}
					</p>
					{#each g.rows as row (row.key)}
						<div
							class="res"
							class:on={row.key === activeKey}
							role="option"
							id="loc-{row.key}"
							aria-selected={row.key === activeKey}
							tabindex="-1"
							onpointermove={() => (activeKey = row.key)}
							onclick={() => { activeKey = row.key; void go(); }}
							onkeydown={(e) => { if (e.key === 'Enter') { activeKey = row.key; void go(); } }}
						>
							<Icon name={row.kind === 'view' ? (VIEWS.find((v) => v.href === row.path)?.icon ?? 'layout-grid') : KIND_ICON[row.kind]} />
							<span class="t">{row.kind === 'run' || row.kind === 'asset' ? row.gloss || row.mark : row.mark}<small>{row.kind === 'run' || row.kind === 'asset' ? row.mark : row.gloss}</small></span>
							{#if row.state}
								{#if row.kind === 'view'}<span class="mono muted">{row.state}</span>
								{:else}<span class="state" data-tone={toneOf(row)}>{row.state}</span>{/if}
							{/if}
						</div>
					{/each}
				</div>
			{/each}
		</div>
		{#each notices as n (n.what)}
			<p class="notice hint" role="status">
				{#if n.r?.phase === 'loading'}Reading {n.what}…{:else}Could not read {n.what}: <span class="mono">{n.r?.error?.message}</span>{/if}
			</p>
		{/each}
		<div class="ft">
			<span><span class="kbd">↑</span><span class="kbd">↓</span> move</span>
			<span><span class="kbd">↵</span> open</span>
			<span><span class="kbd">g</span><span class="kbd">r</span> jump to Run</span>
		</div>
	</div>
{/if}

<style>
	.pal {
		position: absolute; top: 40px; left: 24px; width: min(600px, calc(100% - 48px));
		background: var(--p2); border: 1px solid var(--ln2); z-index: 80;
		display: flex; flex-direction: column; max-height: min(560px, calc(100vh - 80px));
	}
	.q { display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 14px; border-bottom: 1px solid var(--ln); color: var(--dim); flex: none; }
	.q input { flex: 1; background: none; border: 0; color: var(--tx); font: 400 14px var(--f-ui); outline: none; min-width: 0; }
	.q input::placeholder { color: var(--dim); }
	.res-wrap { overflow: auto; min-height: 0; padding-bottom: 6px; }
	.grp { padding: 10px 14px 4px; margin: 0; }
	.res {
		display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 10px; align-items: center;
		padding: 7px 14px; cursor: pointer; color: var(--tx2);
	}
	.res .t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.res .t small { font: 400 11px var(--f-mono); color: var(--dim); margin-left: 8px; }
	.res.on { background: var(--p3); color: var(--tx); }
	.res :global(.i) { color: var(--dim); }
	.none, .notice { padding: 12px 14px; }
	.ft {
		display: flex; gap: 16px; align-items: center; padding: 9px 14px; border-top: 1px solid var(--ln);
		font: 400 11.5px var(--f-ui); color: var(--dim); flex: none;
	}
	.ft > span { display: inline-flex; gap: 4px; align-items: center; }
	.ft .kbd { min-width: 18px; height: 18px; }
</style>
