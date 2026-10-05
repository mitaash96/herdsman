<script lang="ts">
	/* Home — the fleet (views.md §1). One row per run; the needs-you panel beside it.
	   The active fleet is the shell's shared read; the archived list is read when opened. */
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import AsyncField from '$lib/AsyncField.svelte';
	import Tabs, { panelId } from '$lib/Tabs.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import IconButton from '$lib/IconButton.svelte';
	import Button from '$lib/Button.svelte';
	import StateMark from '$lib/StateMark.svelte';
	import Spectrum from '$lib/Spectrum.svelte';
	import Mark from '$lib/Mark.svelte';
	import Icon from '$lib/Icon.svelte';
	import NeedsYouPanel, { initialCollapsed } from '$lib/NeedsYouPanel.svelte';
	import WhileAway from '$lib/WhileAway.svelte';
	import { boundary, SEEN_KEY, type DigestWindow } from '$lib/digest';
	import { Resource } from '$lib/resource.svelte';
	import { modelMark } from '$lib/marks';
	import type { Tone } from '$lib/tones';
	import { daemon, type DigestEntry, type Fleet, type RunRollup } from '$lib/daemon';
	import { ago, needsUser, spendReading } from '$lib/bank';

	const ctx = getContext<{ resource: Resource<Fleet>; reload: () => void }>('fleet');
	const active = $derived(ctx.resource);
	const archived = new Resource<Fleet>((signal) => daemon.fleetArchived(signal));

	type Tab = 'active' | 'archived' | 'away';
	let tab = $state<Tab>('active');
	type Filter = 'all' | 'running' | 'needs' | 'failed';
	let filter = $state<Filter>('all');
	let collapsed = $state(initialCollapsed());

	/* The clock relative times read against; the fleet itself is polled by the shell. */
	let now = $state(Date.now());
	$effect(() => {
		const timer = setInterval(() => (now = Date.now()), 6000);
		return () => clearInterval(timer);
	});

	/* The archived list is its own read, polled only while it is the visible tab. */
	$effect(() => {
		if (tab !== 'archived') return;
		void archived.load();
		const timer = setInterval(() => { if (!document.hidden) void archived.load(); }, 6000);
		return () => clearInterval(timer);
	});

	const fleet = $derived(active.data);
	const current = $derived(tab === 'archived' ? archived : active);
	const initiatives = $derived(fleet ? Object.values(fleet.counts).reduce((a, b) => a + b, 0) : null);
	const attention = $derived(fleet?.attention);
	const tokens = $derived(spendReading(fleet?.spend).value);

	/* Home already showed these keys; its fleet read seeds the shell's notified set. */
	$effect(() => {
		const items = fleet?.notifications;
		if (items && !document.hidden) {
			localStorage.setItem('herdsman-notify-seen', JSON.stringify(items.filter((item) => item.blocking).map((item) => item.key)));
		}
	});

	/* --- run row model ------------------------------------------------------ */
	function runTone(run: RunRollup): { tone: Tone; word: string } {
		if (run.archived) return { tone: 'idle', word: 'Archived' };
		switch (run.status) {
			case 'awaiting_approval': return { tone: 'needs', word: 'Awaiting approval' };
			case 'running': return { tone: 'running', word: 'Running' };
			case 'settled': return { tone: 'settled', word: 'Settled' };
			case 'failed': return { tone: 'failed', word: 'Failed' };
			case 'paused': return { tone: 'paused', word: 'Paused' };
			case 'empty': return { tone: 'idle', word: 'Empty' };
			default: return { tone: 'idle', word: 'Idle' };
		}
	}
	const titleOf = (run: RunRollup) => run.title ?? run.brief.split('\n').find((l) => l.trim())?.trim() ?? run.plan_id;
	const briefOf = (run: RunRollup) => run.brief.replace(/\s+/g, ' ').trim();
	/* Optional rollup fields (backend B2 / B4): drawn only when the daemon sends them. */
	type Extra = { models?: string[]; start_branch?: string | null; target_branch?: string | null };
	const modelsOf = (run: RunRollup) => {
		const all = (run as RunRollup & Extra).models ?? [];
		const seenMarks = new Set<string>();
		return all.filter((m) => { const k = modelMark(m) ?? m; if (seenMarks.has(k)) return false; seenMarks.add(k); return true; });
	};
	const branchOf = (run: RunRollup) => (run as RunRollup & Extra).target_branch ?? (run as RunRollup & Extra).start_branch ?? null;
	function spectrum(run: RunRollup) {
		const c = run.counts;
		const need = Math.min(
			run.attention?.filter((a) => a.blocking && (a.kind === 'checkpoint_review' || a.kind === 'blocked_on_user')).length ?? 0,
			(c.running ?? 0) + (c.pending ?? 0)
		);
		const fromRunning = Math.min(need, c.running ?? 0);
		return {
			settled: c.settled ?? 0,
			running: (c.running ?? 0) - fromRunning,
			needs: need,
			failed: c.failed ?? 0,
			waiting: (c.pending ?? 0) + (c.paused ?? 0) - (need - fromRunning)
		};
	}
	const matches = (run: RunRollup, f: Filter): boolean =>
		f === 'all' ? true
		: f === 'running' ? run.status === 'running'
		: f === 'failed' ? run.status === 'failed' || (run.counts.failed ?? 0) > 0
		: needsUser(run).count > 0 || run.status === 'awaiting_approval';
	const rows = (view: Fleet) => view.runs.filter((run) => matches(run, filter));

	/* --- row actions: copy, archive / restore, delete (inline confirm) ------ */
	let hot = $state<string | null>(null);
	let confirming = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let copied = $state<string | null>(null);
	let failure = $state<{ id: string; message: string } | null>(null);
	let roving = $state<string | null>(null);

	async function copyId(id: string) {
		try {
			await navigator.clipboard.writeText(id);
			copied = id;
			setTimeout(() => { if (copied === id) copied = null; }, 1500);
		} catch { /* the clipboard may be blocked; nothing to report beside the control */ }
	}
	function reloadBoth() {
		ctx.reload();
		if (archived.hasData) void archived.load();
	}
	async function shelve(run: RunRollup) {
		if (busy) return;
		busy = run.plan_id; failure = null;
		try {
			const verb = run.archived ? 'unarchive' : 'archive';
			await daemon[verb](run.plan_id, '', `${verb}:${run.plan_id}`);
			reloadBoth();
		} catch (cause) {
			failure = { id: run.plan_id, message: cause instanceof Error ? cause.message : 'The write failed.' };
		} finally { busy = null; }
	}
	async function erase(run: RunRollup) {
		if (busy) return;
		busy = run.plan_id; failure = null;
		try {
			await daemon.deletePlan(run.plan_id);
			confirming = null;
			reloadBoth();
		} catch (cause) {
			failure = { id: run.plan_id, message: cause instanceof Error ? cause.message : 'The delete failed.' };
		} finally { busy = null; }
	}

	/* --- keyboard: ↑/↓ move between rows, Enter opens ------------------------ */
	function rowKeys(event: KeyboardEvent, run: RunRollup) {
		if (event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey) return;
		const el = event.currentTarget as HTMLElement;
		if (event.key === 'Enter') { event.preventDefault(); void goto(run.link.path); return; }
		const next = event.key === 'ArrowDown' ? el.nextElementSibling : event.key === 'ArrowUp' ? el.previousElementSibling : null;
		if (!next && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) event.preventDefault();
		if (next instanceof HTMLElement) { event.preventDefault(); next.focus(); }
	}
	function rowClick(event: MouseEvent, run: RunRollup) {
		if ((event.target as HTMLElement).closest('button, a, .bar')) return;
		void goto(run.link.path);
	}
	function leave(event: FocusEvent | PointerEvent, id: string) {
		const row = event.currentTarget as HTMLElement;
		if (event instanceof FocusEvent && row.contains(event.relatedTarget as Node | null)) return;
		if (confirming !== id) hot = null;
	}

	/* --- While away: the return digest -------------------------------------- */
	let digestWindow = $state<DigestWindow>('since');
	let digestSeen = $state<string | null>(null);
	let since = $state(new Date(Date.now() - 86_400_000).toISOString());
	let digestRead = false;
	const digest = new Resource<DigestEntry[]>((signal) => daemon.whileAway(since, signal));
	let sinceCount = $state<number | null>(null);
	$effect(() => {
		digestSeen = localStorage.getItem(SEEN_KEY);
		since = boundary(digestWindow, digestSeen);
	});
	$effect(() => {
		if (tab !== 'away') return;
		void digest.load().then(() => {
			if (!digest.stale && digest.data) {
				digestRead = true;
				if (digestWindow === 'since') sinceCount = digest.data.length;
			}
		});
		const wake = () => { if (!document.hidden) void digest.load(); };
		window.addEventListener('focus', wake);
		return () => window.removeEventListener('focus', wake);
	});
	function selectWindow(next: DigestWindow) {
		digestWindow = next;
		since = boundary(next, digestSeen);
		void digest.load().then(() => { if (next === 'since' && !digest.stale) sinceCount = digest.data?.length ?? null; });
	}
	function markRead() {
		digestSeen = new Date().toISOString();
		localStorage.setItem(SEEN_KEY, digestSeen);
		sinceCount = 0;
		if (digestWindow === 'since') { since = digestSeen; void digest.load(); }
	}
	function selectTab(next: string) {
		if (tab === 'away' && digestRead) markRead();
		digestRead = false;
		tab = next as Tab;
	}

	const tabs = $derived([
		{ id: 'active', label: 'Active', count: fleet?.total_runs ?? '—' },
		{ id: 'archived', label: 'Archived', count: fleet?.archived ?? '—' },
		{ id: 'away', label: 'While away', count: sinceCount ?? undefined }
	]);
	const FILTERS = [
		{ id: 'all', label: 'All' },
		{ id: 'running', label: 'Running' },
		{ id: 'needs', label: 'Needs you' },
		{ id: 'failed', label: 'Failed' }
	] as const;
</script>

<svelte:head><title>Fleet · Herdsman</title></svelte:head>

{#snippet skeleton()}
	<div class="skel" aria-hidden="true">
		{#each { length: 5 } as _, i (i)}<div class="srow"><i></i><span class="b1"></span><span class="b2"></span></div>{/each}
	</div>
{/snippet}

<div class="home">
	<section class="ph">
		<div class="title">
			<h1 class="h-title">Fleet</h1>
			<div class="meta"><span class="lbl">{fleet ? `${fleet.total_runs} active · ${fleet.archived} archived · ${initiatives} initiatives` : '—'}</span></div>
		</div>
		<div class="tele">
			<div><span class="lbl">Needs you</span><span class="num" class:nd={!!attention?.length}>{attention?.length ?? '—'}</span></div>
			<div><span class="lbl">Running</span><span class="num">{fleet?.running_runs ?? '—'}</span></div>
			<div><span class="lbl">Failed members</span><span class="num" class:bad={!!fleet?.counts.failed}>{fleet ? (fleet.counts.failed ?? 0) : '—'}</span></div>
			<div><span class="lbl">Tokens · accounted</span><span class="num">{tokens ?? '—'}</span></div>
		</div>
	</section>

	<nav class="subnav" aria-label="Fleet views">
		<Tabs prefix="home" label="Fleet views" items={tabs} selected={tab} onselect={selectTab} />
		{#if tab !== 'away'}
			<div class="tools">
				<Segmented label="Filter runs" options={FILTERS} value={filter} onchange={(id) => (filter = id)} />
				<IconButton small icon="filter" label="Filter" disabled reason="More filters are not available yet" />
			</div>
		{/if}
	</nav>

	<div class="grid" class:min={collapsed}>
		<div class="main" role="tabpanel" id={panelId('home', tab)} aria-labelledby="home-tab-{tab}">
			{#if tab === 'away'}
				<WhileAway entries={digest.data} {since} window={digestWindow} onwindow={selectWindow} onmark={markRead}
					loading={digest.phase === 'loading'} error={digest.stale ? (digest.error?.message ?? 'The read failed.') : (digest.phase === 'error' ? (digest.error?.message ?? 'The read failed.') : null)}
					firstVisit={digestSeen === null} />
			{:else}
				<AsyncField resource={current} reading={tab === 'archived' ? 'the archived runs' : 'the fleet'} onretry={() => void current.load()} {skeleton}>
					{#snippet children(view: Fleet)}
						{@const list = rows(view)}
						{#if view.runs.length === 0}
							<p class="empty-line">{tab === 'archived' ? 'No archived runs.' : 'No runs yet. New dispatch starts one.'}</p>
						{:else if list.length === 0}
							<p class="empty-line">No run matches this filter.</p>
						{:else}
							<ul class="runs" aria-label={tab === 'archived' ? 'Archived runs' : 'Runs'}>
								{#each list as run, i (run.plan_id)}
									{@const st = runTone(run)}
									{@const sp = spectrum(run)}
									{@const need = needsUser(run)}
									{@const models = modelsOf(run)}
									{@const branch = branchOf(run)}
									{@const spend = spendReading(run.spend).value}
									{@const settledTotal = run.total === 0 ? '—' : `${run.counts.settled ?? 0}/${run.total}`}
									<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
									<li class="run-row" data-tone={st.tone}
										tabindex={(roving ?? list[0]?.plan_id) === run.plan_id ? 0 : -1}
										onkeydown={(e) => rowKeys(e, run)} onclick={(e) => rowClick(e, run)}
										onfocusin={() => { roving = run.plan_id; hot = run.plan_id; }} onfocusout={(e) => leave(e, run.plan_id)}
										onpointerenter={() => (hot = run.plan_id)} onpointerleave={(e) => leave(e, run.plan_id)}>
										<i class="em" aria-hidden="true"></i>
										<div class="main-c">
											<a class="t" href={run.link.path}>{titleOf(run)}</a>
											<div class="b">{briefOf(run)}</div>
											<div class="m">
												<StateMark tone={st.tone} word={st.word} />
												{#if branch}<span class="branch"><Icon name="git-branch" size={12} />{branch}</span>{/if}
												{#if models.length}
													<span class="harn" title={models.join(', ')}>{#each models as m (m)}<Mark model={m} size={14} />{/each}</span>
												{/if}
											</div>
											{#if failure?.id === run.plan_id}<p class="mono fail" role="alert">{failure.message}</p>{/if}
										</div>
										<div class="sp">
											<Spectrum tall {...sp} />
											<div class="mono muted sline">{settledTotal} settled{#if need.count}<span aria-hidden="true"> · </span><span class="nd">{need.count} need you</span>{/if}</div>
										</div>
										<div class="facts">
											<span><b>{spend ?? '—'}</b> tok</span>
											<span>{ago(run.updated_at, now).replace(/ (min|h|d) ago$/, '$1 ago')}</span>
											<span class="id" title={run.plan_id}>{run.plan_id}</span>
										</div>
										{#if hot === run.plan_id || confirming === run.plan_id || busy === run.plan_id}
											<span class="ibar bar fade-in">
												{#if confirming === run.plan_id}
													<span class="lbl ask">Delete?</span>
													<Button small kind="danger" icon="trash-2" busy={busy === run.plan_id} onclick={() => void erase(run)}>Delete</Button>
													<Button small icon="x" onclick={() => { confirming = null; hot = null; }}>Keep</Button>
												{:else}
													<IconButton small icon={copied === run.plan_id ? 'check' : 'copy'} label={copied === run.plan_id ? 'Copied' : 'Copy id'} onclick={() => void copyId(run.plan_id)} />
													<IconButton small icon={run.archived ? 'archive-restore' : 'archive'} label={run.archived ? 'Restore' : 'Archive'} disabled={busy === run.plan_id} onclick={() => void shelve(run)} />
													<IconButton small danger icon="trash-2" label="Delete" onclick={() => { confirming = run.plan_id; failure = null; }} />
												{/if}
											</span>
										{/if}
									</li>
								{/each}
							</ul>
						{/if}
					{/snippet}
				</AsyncField>
			{/if}
		</div>
		<NeedsYouPanel items={attention ?? []} {now} bind:collapsed stale={active.stale} unknown={fleet !== null && attention === undefined} />
	</div>
</div>

<style>
	.home { display: grid; grid-template-rows: auto auto minmax(0, 1fr); min-height: 0; height: 100%; }
	.subnav :global(.tabs) { align-self: stretch; }
	.grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; min-height: 0; transition: grid-template-columns var(--t-dock) var(--ease); }
	.grid.min { grid-template-columns: minmax(0, 1fr) 52px; }
	.main { overflow: auto; min-height: 0; }
	.runs { list-style: none; padding: 0; margin: 0; }
	.run-row {
		display: grid; grid-template-columns: 2px minmax(0, 1fr) 200px 132px; gap: 0 20px; align-items: center;
		padding: 14px 24px; border-bottom: 1px solid var(--ln); cursor: pointer; position: relative;
		transition: background var(--t-fast);
	}
	.run-row:hover { background: color-mix(in srgb, var(--p2) 60%, transparent); }
	.run-row:focus-visible { outline-offset: -2px; }
	.em { width: 2px; height: 44px; background: var(--sc); }
	.run-row[data-tone='needs'] .em { background: repeating-linear-gradient(var(--sc) 0 5px, transparent 5px 9px); box-shadow: 4px 0 0 var(--sc); }
	.run-row[data-tone='failed'] .em { background: linear-gradient(var(--sc) 0 16px, transparent 16px 26px, var(--sc) 26px); }
	.run-row[data-tone='idle'] .em { background: repeating-linear-gradient(var(--sc) 0 5px, transparent 5px 9px); }
	.run-row[data-tone='paused'] .em { background: linear-gradient(transparent 0 22px, var(--sc) 22px); }
	.main-c { min-width: 0; }
	.t { display: block; text-decoration: none; font: 500 15px/1.25 var(--f-ui); color: var(--tx); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.b { color: var(--dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 3px; font-size: 12.5px; }
	.m { display: flex; gap: 12px; align-items: center; margin-top: 6px; white-space: nowrap; min-height: 14px; }
	.harn { display: inline-flex; gap: 4px; align-items: center; }
	.fail { color: var(--l656); font-size: 12px; margin-top: 6px; overflow-wrap: anywhere; }
	.sp { min-width: 0; }
	.sline { margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.sline .nd { color: var(--l589); margin-left: 4px; }
	.facts { display: flex; flex-direction: column; gap: 4px; text-align: right; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font: 400 12px var(--f-mono); color: var(--dim); }
	.facts b { color: var(--tx); font-weight: 400; }
	.facts .id { color: var(--fnt); overflow: hidden; text-overflow: ellipsis; }
	.bar { position: absolute; right: 16px; top: 10px; background: var(--p2); border: 1px solid var(--ln); gap: 2px; align-items: center; }
	.ask { color: var(--tx); padding: 0 6px 0 8px; }
	.skel { display: grid; }
	.srow { display: grid; grid-template-columns: 2px 1fr 200px; gap: 20px; align-items: center; height: 88px; border-bottom: 1px solid var(--ln); padding: 0 24px; }
	.srow i { height: 44px; background: var(--p2); }
	.srow span { height: 12px; background: var(--p1); }
	.srow .b1 { width: 60%; } .srow .b2 { width: 100%; height: 22px; }
	@media (max-width: 1023px) {
		.run-row { grid-template-columns: 2px minmax(0, 1fr) 132px; }
		.sp { display: none; }
	}
</style>
