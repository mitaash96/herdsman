<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { VIEWS, viewFor } from '$lib/views';
	import { daemon, type Fleet, type PlanGraph } from '$lib/daemon';
	import { Resource } from '$lib/resource.svelte';
	import { CHORDS } from '$lib/locate';
	import { shouldNotify } from '$lib/attention';
	import { seen } from '$lib/seen.svelte';
	import { titleblock } from '$lib/shell.svelte';
	import { MARKS } from '$lib/marks';
	import Icon from '$lib/Icon.svelte';
	import IconButton from '$lib/IconButton.svelte';
	import LocatePalette from '$lib/LocatePalette.svelte';
	import { setContext, tick } from 'svelte';

	let { children } = $props();

	const view = $derived(viewFor(page.url.pathname));

	/* --- browser notifications (opt-in from Home), unchanged behaviour ------- */
	let notifyEnabled = $state(false);
	let notificationSeeded = false;
	$effect(() => {
		const update = () => {
			const enabled = localStorage.getItem('herdsman-notify') === 'on';
			if (enabled && !notifyEnabled) notificationSeeded = localStorage.getItem('herdsman-notify-seen') !== null;
			notifyEnabled = enabled;
		};
		update();
		window.addEventListener('herdsman-notify-change', update);
		window.addEventListener('storage', update);
		return () => {
			window.removeEventListener('herdsman-notify-change', update);
			window.removeEventListener('storage', update);
		};
	});
	$effect(() => {
		if (!notifyEnabled || typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
		const pathname = page.url.pathname;
		let busy = false;
		const read = async () => {
			if (busy || (pathname === '/home' && !document.hidden)) return;
			busy = true;
			try {
				const items = (await daemon.fleetNotifications()).filter((item) => item.blocking);
				const keys = items.map((item) => item.key);
				const previous = localStorage.getItem('herdsman-notify-seen');
				const seenKeys: string[] = previous ? (JSON.parse(previous) as string[]) : [];
				if (notificationSeeded)
					for (const item of items) {
						if (shouldNotify(pathname, document.hidden) && !seenKeys.includes(item.key)) {
							const note = new Notification('Herdsman · needs you', { body: item.summary, tag: item.key });
							note.onclick = () => {
								window.focus();
								void goto(item.link.path);
								note.close();
							};
						}
					}
				localStorage.setItem('herdsman-notify-seen', JSON.stringify(keys));
				notificationSeeded = true;
			} catch {
				/* A failed poll leaves seen keys intact for the next read. */
			} finally {
				busy = false;
			}
		};
		void read();
		const timer = setInterval(() => void read(), 15_000);
		return () => clearInterval(timer);
	});

	/* --- the fleet: one shell read, shared --------------------------------
	   The sidebar's unread badge, the daemon status, the Run sub-item and the
	   Locate index all read it; Home consumes this same resource rather than
	   polling its own. 6 s while visible, paused while the tab is hidden. */
	const fleet = new Resource<Fleet>((signal) => daemon.fleet(signal));
	$effect(() => {
		void fleet.load();
		const timer = setInterval(() => {
			if (!document.hidden) void fleet.load();
		}, 6_000);
		const onvis = () => {
			if (!document.hidden) void fleet.load();
		};
		document.addEventListener('visibilitychange', onvis);
		return () => {
			clearInterval(timer);
			document.removeEventListener('visibilitychange', onvis);
			fleet.dispose();
		};
	});
	setContext('fleet', {
		get resource() {
			return fleet;
		},
		reload: () => void fleet.load()
	});

	/* --- the addressed plan: the shell's own graph read --------------------- */
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

	/* The last plan opened this session: Run's sub-item and its nav target. */
	let lastPlan = $state<string | null>(null);
	$effect(() => {
		if (view?.id === 'run' && planId) lastPlan = planId;
	});
	const lastRun = $derived(lastPlan ? fleet.data?.runs.find((r) => r.plan_id === lastPlan) : undefined);
	const lastTitle = $derived(lastRun ? (lastRun.title ?? lastRun.brief.split('\n')[0]) : lastPlan);
	const runHref = $derived(lastPlan ? `/run?plan=${encodeURIComponent(lastPlan)}` : '/run');

	const unread = $derived((fleet.data?.attention ?? []).filter((item) => seen.isUnread(item)).length);

	const daemonWord = $derived(
		fleet.phase === 'error' ? 'Not answering' : fleet.stale ? 'Stale' : fleet.data ? 'Answering' : 'Reading'
	);

	/* --- theme: system unless the operator has said otherwise --------------- */
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
	const THEMES = [
		{ id: 'system', icon: 'monitor', label: 'Follow the system theme' },
		{ id: 'light', icon: 'sun', label: 'Light theme' },
		{ id: 'dark', icon: 'moon', label: 'Dark theme' }
	] as const;
	function setTheme(next: Theme) {
		theme = next;
		const root = document.documentElement;
		try {
			if (next === 'system') {
				delete root.dataset.theme;
				localStorage.removeItem('herdsman-theme');
			} else {
				root.dataset.theme = next;
				localStorage.setItem('herdsman-theme', next);
			}
		} catch {
			/* the choice still applies to this session */
		}
	}

	/* --- sidebar: 224 / 60, persisted; hidden below 1024 (overlay) ---------- */
	let collapsed = $state(false);
	let narrow = $state(false);
	let overlay = $state(false);
	let armed = $state(false);
	$effect(() => {
		const mid = matchMedia('(max-width: 1279px)');
		const small = matchMedia('(max-width: 1023px)');
		let stored: string | null = null;
		try {
			stored = localStorage.getItem('herdsman.sidebar');
		} catch {
			/* default */
		}
		collapsed = stored ? stored === 'collapsed' : mid.matches;
		narrow = small.matches;
		const onsmall = () => {
			narrow = small.matches;
			overlay = false;
		};
		small.addEventListener('change', onsmall);
		const t = setTimeout(() => (armed = true), 50);
		return () => {
			small.removeEventListener('change', onsmall);
			clearTimeout(t);
		};
	});
	function toggleSide() {
		if (narrow) {
			overlay = !overlay;
			return;
		}
		collapsed = !collapsed;
		try {
			localStorage.setItem('herdsman.sidebar', collapsed ? 'collapsed' : 'expanded');
		} catch {
			/* session only */
		}
	}
	const showCollapsed = $derived(collapsed && !(narrow && overlay));
	$effect(() => {
		void page.url.pathname;
		overlay = false;
	});

	/* --- Locate and the shell's keys ---------------------------------------- */
	let locateOpen = $state(false);
	let trigger = $state<HTMLButtonElement>();
	const isMac = (() => {
		try {
			return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
		} catch {
			return false;
		}
	})();
	function closeLocate(refocus: boolean) {
		locateOpen = false;
		if (refocus) trigger?.focus();
	}

	let chordPending = false;
	let chordTimer: ReturnType<typeof setTimeout> | undefined;
	const isTyping = (target: EventTarget | null): boolean =>
		target instanceof HTMLElement &&
		(target instanceof HTMLInputElement ||
			target instanceof HTMLTextAreaElement ||
			target instanceof HTMLSelectElement ||
			target.isContentEditable);

	async function arrive(): Promise<void> {
		await tick();
		const main = document.getElementById('main');
		if (main && !main.contains(document.activeElement)) main.focus();
	}

	function onkeydown(event: KeyboardEvent) {
		if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
			event.preventDefault();
			locateOpen = !locateOpen;
			return;
		}
		if (locateOpen || isTyping(event.target)) return;
		if (event.metaKey || event.ctrlKey || event.altKey) return;
		if (event.key === 'Escape' && overlay) {
			overlay = false;
			return;
		}
		if (event.key === 'g') {
			chordPending = true;
			clearTimeout(chordTimer);
			chordTimer = setTimeout(() => (chordPending = false), 1200);
			return;
		}
		if (chordPending && event.key in CHORDS) {
			clearTimeout(chordTimer);
			chordPending = false;
			const id = CHORDS[event.key as keyof typeof CHORDS];
			const target = id === 'run' ? runHref : VIEWS.find((v) => v.id === id)?.href;
			if (target) void goto(target).then(arrive);
			return;
		}
		chordPending = false;
		if (event.key.toLowerCase() === 'n') {
			event.preventDefault();
			void goto('/home/dispatch').then(arrive);
		} else if (event.key === '[') {
			event.preventDefault();
			toggleSide();
		}
	}
</script>

<svelte:window {onkeydown} />

<svelte:head>
	<title>{view ? `${view.name} — Herdsman` : 'Herdsman'}</title>
</svelte:head>

<a class="skip" href="#main">Skip to content</a>

<div class="app" class:collapsed={showCollapsed} class:narrow class:overlay class:armed>
	<aside class="side" aria-label="Herdsman">
		<div class="head">
			<svg class="glyph" width="20" height="20" viewBox={MARKS.herdsman.viewBox} aria-hidden="true">{@html MARKS.herdsman.body}</svg>
			<b class="word">Herdsman</b>
			<span class="toggle">
				<IconButton
					icon={showCollapsed ? 'panel-left-open' : 'panel-left-close'}
					label={showCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
					shortcut="["
					small
					onclick={toggleSide}
				/>
			</span>
		</div>

		<div class="dispatch">
			<a class="btn pri" href="/home/dispatch" aria-label="New dispatch" title={showCollapsed ? 'New dispatch ( N )' : undefined}>
				<Icon name="send-horizontal" size={15} /><span class="lab">New dispatch</span><kbd>N</kbd>
			</a>
		</div>

		<nav aria-label="Views">
			{#each VIEWS as v (v.id)}
				{@const current = view?.id === v.id}
				<a
					href={v.id === 'run' ? runHref : v.href}
					aria-current={current ? 'page' : undefined}
					title={showCollapsed ? v.name : undefined}
				>
					<span class="ic"><Icon name={v.icon} size={17} /></span>
					<span class="lab">{v.name}</span>
					{#if v.id === 'home' && unread > 0}<span class="count nd"><span class="lab">{unread} new</span></span>{/if}
					{#if v.dev}<span class="dev">Dev</span>{/if}
				</a>
				{#if v.id === 'run' && lastPlan}
					<a class="sub" href={runHref} title={lastTitle ?? ''}>{lastTitle}</a>
				{/if}
			{/each}
		</nav>

		<div class="foot">
			<div class="daemon" role="status" title="Daemon: {daemonWord}">
				{#if daemonWord === 'Answering'}
					<span class="live" aria-hidden="true"></span><span class="lbl">Daemon · answering</span>
				{:else if daemonWord === 'Not answering'}
					<span class="state" data-tone="failed"><span class="lab">Daemon · not answering</span></span>
				{:else}
					<span class="state" data-tone="waiting"><span class="lab">Daemon · {daemonWord.toLowerCase()}</span></span>
				{/if}
			</div>
			<div class="themes" role="group" aria-label="Theme">
				{#each THEMES as t (t.id)}
					<span class="th" class:cur={theme === t.id}>
						<IconButton icon={t.icon} label={t.label} small pressed={theme === t.id} onclick={() => setTheme(t.id)} />
					</span>
				{/each}
			</div>
		</div>
	</aside>

	<div class="main">
		<header class="tb">
			{#if narrow}
				<span class="open-side"><IconButton icon="panel-left-open" label="Open sidebar" shortcut="[" onclick={toggleSide} /></span>
			{/if}
			<div class="locate" class:open={locateOpen}>
				<button
					type="button"
					class="box locate-trigger"
					bind:this={trigger}
					aria-expanded={locateOpen}
					aria-haspopup="listbox"
					aria-label="Locate runs, members, checkpoints and assets"
					onclick={() => (locateOpen = !locateOpen)}
				>
					<Icon name="search" />
					<span class="ph2">Locate runs, members, checkpoints, assets</span>
					<span class="kbd">{isMac ? '⌘' : 'Ctrl'}</span><span class="kbd">K</span>
				</button>
				<LocatePalette open={locateOpen} onclose={closeLocate} />
			</div>
			<div class="acts">
				{#if titleblock.actions}{@render titleblock.actions()}{/if}
			</div>
		</header>

		<main id="main" tabindex="-1">
			{@render children()}
		</main>
	</div>
</div>

<style>
	.skip { position: absolute; left: -9999px; }
	.skip:focus { left: 8px; top: 8px; z-index: 100; background: var(--p2); border: 1px solid var(--tx); padding: 8px 12px; }

	.app { height: 100vh; display: grid; grid-template-columns: auto minmax(0, 1fr); }

	/* --- sidebar --- */
	.side {
		width: var(--side-w); display: flex; flex-direction: column; border-right: 1px solid var(--ln);
		background: var(--bg); position: relative; overflow: hidden; z-index: 50; min-height: 0;
	}
	.armed .side { transition: width var(--t-side) var(--ease); }
	.side::before { content: ''; position: absolute; left: 29px; top: 48px; bottom: 0; width: 1px; background: var(--ln2); }
	.head { height: 48px; display: flex; align-items: center; gap: 12px; padding: 0 10px 0 20px; border-bottom: 1px solid var(--ln); flex: none; position: relative; }
	.glyph { color: var(--tx); flex: none; }
	.word { font: 500 15px var(--f-label); letter-spacing: 0.32em; text-transform: uppercase; white-space: nowrap; }
	.toggle { margin-left: auto; }
	.dispatch { margin: 14px 12px 10px 44px; position: relative; }
	.dispatch .btn { width: 100%; justify-content: flex-start; }
	.dispatch kbd { margin-left: auto; font: 400 10.5px var(--f-mono); opacity: 0.6; }
	nav { display: flex; flex-direction: column; padding: 6px 0; position: relative; }
	nav a:not(.sub) {
		display: flex; align-items: center; gap: 16px; height: 42px; margin: 1px 8px; padding: 0 14px 0 13px;
		color: var(--dim); text-decoration: none; position: relative; white-space: nowrap;
		font: 500 14px var(--f-label); letter-spacing: 0.14em; text-transform: uppercase;
		transition: color var(--t-fast), background var(--t-fast);
	}
	nav .ic { display: inline-grid; place-items: center; background: var(--bg); box-shadow: 0 0 0 4px var(--bg); transition: background var(--t-fast), box-shadow var(--t-fast); }
	nav a:not(.sub):hover { color: var(--tx); background: var(--p1); }
	nav a:not(.sub):hover .ic { background: var(--p1); box-shadow: 0 0 0 4px var(--p1); }
	nav a[aria-current] { color: var(--tx); background: var(--p3); }
	nav a[aria-current] .ic, nav a[aria-current]:hover .ic { background: var(--p3); box-shadow: 0 0 0 4px var(--p3); }
	nav a[aria-current]:hover { background: var(--p3); }
	nav .count { margin-left: auto; font: 500 11px var(--f-label); letter-spacing: 0.14em; text-transform: uppercase; }
	nav .dev { font: 500 9.5px var(--f-label); letter-spacing: 0.14em; border: 1px dashed var(--ln2); padding: 2px 5px; color: var(--fnt); margin-left: auto; }
	nav .sub {
		display: block; margin: 2px 0 6px 54px; padding: 6px 10px; border-left: 1px solid var(--ln2);
		font: 400 12px/1.35 var(--f-ui); color: var(--tx2); text-decoration: none;
		white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 156px;
	}
	nav .sub:hover { color: var(--tx); }
	.foot { margin-top: auto; padding: 12px 12px 14px 44px; display: flex; flex-direction: column; gap: 12px; border-top: 1px solid var(--ln); position: relative; background: var(--bg); }
	.daemon { display: flex; align-items: center; gap: 10px; white-space: nowrap; min-height: 14px; }
	.daemon .lbl { font-size: 11px; }
	.themes { display: flex; gap: 2px; }
	.th.cur :global(.ib) { border-color: var(--ln2); color: var(--tx); }

	.collapsed .side { width: var(--side-w-collapsed); }
	.collapsed .word, .collapsed .lab, .collapsed .dev, .collapsed .dispatch kbd, .collapsed nav .sub,
	.collapsed .th:not(.cur) { display: none; }
	.collapsed .head { padding: 0 0 0 20px; }
	.collapsed .toggle { position: absolute; left: 16px; top: 56px; margin: 0; }
	.collapsed .dispatch { margin: 50px 12px 10px 13px; }
	.collapsed .dispatch .btn { padding: 0; justify-content: center; width: 34px; }
	.collapsed .dispatch .btn::before { display: none; }
	.collapsed .foot { padding: 12px 0 14px 15px; }
	.collapsed nav a:not(.sub) { padding: 0; justify-content: center; width: 44px; }
	.collapsed nav .count.nd { position: absolute; right: 6px; top: 8px; width: 5px; height: 5px; background: var(--l589); border-radius: 50%; }
	.collapsed .daemon { padding-left: 10px; }
	.collapsed .daemon .state { gap: 0; }

	/* below 1024: the sidebar is an overlay opened from the titleblock (no scrim) */
	.narrow { grid-template-columns: minmax(0, 1fr); }
	.narrow .side { position: fixed; left: 0; top: 0; bottom: 0; transform: translateX(-100%); visibility: hidden; transition: transform var(--t-side) var(--ease), visibility 0s var(--t-side); }
	.narrow.overlay .side { transform: none; visibility: visible; transition: transform var(--t-side) var(--ease); }

	/* --- main + titleblock --- */
	.main {
		display: grid; grid-template-rows: var(--tb-h) minmax(0, 1fr); min-width: 0; min-height: 0; overflow: hidden;
		background: repeating-linear-gradient(90deg, var(--bg) 0 3px, var(--band) 3px 7px, var(--bg) 7px 12px);
	}
	.tb { display: flex; align-items: center; border-bottom: 1px solid var(--ln); background: var(--bg); min-width: 0; position: relative; z-index: 40; }
	.open-side { padding-left: 12px; }
	.locate { flex: 1; display: flex; align-items: center; padding: 0 24px; position: relative; min-width: 0; height: 100%; }
	.box {
		position: relative; display: flex; align-items: center; gap: 10px; width: min(600px, 100%); height: 32px;
		padding: 0 8px 0 18px; border: 1px solid var(--ln2); background: var(--p1); color: var(--dim);
		font: 400 13.5px var(--f-ui); text-align: left;
		transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
	}
	.box::before {
		content: ''; position: absolute; left: 6px; top: 8px; bottom: 8px; width: 1px; background: var(--dim);
		transition: top var(--t-fast) var(--ease), bottom var(--t-fast) var(--ease), background var(--t-fast);
	}
	.box:hover, .open .box { border-color: var(--dim); color: var(--tx2); background: var(--p2); }
	.box:hover::before, .open .box::before { top: 4px; bottom: 4px; background: var(--tx); }
	.ph2 { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.acts { display: flex; align-items: center; gap: 2px; padding: 0 10px; flex: none; }

	main { min-height: 0; min-width: 0; overflow: hidden; outline: none; display: grid; }

	@media (max-width: 767px) {
		.ph2, .box .kbd { display: none; }
		.box { width: 34px; padding: 0; justify-content: center; }
		.box::before { display: none; }
		.locate { padding: 0 8px; flex: none; }
		.acts { margin-left: auto; }
	}
</style>
