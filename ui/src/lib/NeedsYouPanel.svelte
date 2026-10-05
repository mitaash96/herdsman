<script lang="ts" module>
	const STORE = 'herdsman.home.needs-panel';
	/** Persisted choice; absent means the width default (DS §11: collapsed below 1280). */
	export function initialCollapsed(): boolean {
		try {
			const v = localStorage.getItem(STORE);
			if (v === 'collapsed') return true;
			if (v === 'expanded') return false;
		} catch { /* the default holds */ }
		return typeof window !== 'undefined' && window.innerWidth < 1280;
	}
</script>

<script lang="ts">
	/* DS §9.19 — the right column of Home. Decisions are made in Run, where the
	   evidence is: the primary here is a link to the right surface, never a write. */
	import { goto } from '$app/navigation';
	import Icon from './Icon.svelte';
	import IconButton from './IconButton.svelte';
	import Button from './Button.svelte';
	import type { IconName } from './icons';
	import type { AttentionItem } from './daemon';
	import { ago } from './bank';
	import { seen } from './seen.svelte';

	let { items, now, collapsed = $bindable(false), stale = false, unknown = false }: {
		items: AttentionItem[]; now: number; collapsed?: boolean; stale?: boolean; unknown?: boolean;
	} = $props();

	const KIND: Record<string, { name: string; icon: IconName }> = {
		plan_gate: { name: 'Plan approval', icon: 'file-text' },
		checkpoint_review: { name: 'Checkpoint review', icon: 'check-check' },
		blocked_on_user: { name: 'Question', icon: 'circle-help' },
		failed: { name: 'Failed member', icon: 'rotate-ccw' },
		stalled: { name: 'Stalled attempt', icon: 'clock' }
	};
	const kind = (k: string) => KIND[k] ?? { name: k.replace(/_/g, ' '), icon: 'activity' as IconName };

	let open = $state<string | null>(null);
	let earlierAll = $state(false);
	const EARLIER = 3;

	const sorted = $derived([...items].sort((a, b) => Date.parse(b.since) - Date.parse(a.since)));
	/* An item opened while new stays in New until it is closed, so it does not jump away under the pointer. */
	let pinned = $state<string | null>(null);
	const isNew = (i: AttentionItem) => seen.isUnread(i) || i.key === pinned;
	const fresh = $derived(sorted.filter(isNew));
	/* The true unread count (matches the sidebar); `fresh` also holds the pinned open item. */
	const unreadCount = $derived(sorted.filter((i) => seen.isUnread(i)).length);
	const earlier = $derived(sorted.filter((i) => !isNew(i)));
	const shownEarlier = $derived(earlierAll ? earlier : earlier.slice(0, EARLIER));

	function setCollapsed(next: boolean) {
		collapsed = next;
		try { localStorage.setItem(STORE, next ? 'collapsed' : 'expanded'); } catch { /* session only */ }
	}
	function toggle(item: AttentionItem) {
		open = open === item.key ? null : item.key;
		pinned = open && seen.isUnread(item) ? open : null;
		if (open) seen.markRead([item]);
	}

	/* Browser notifications opt-in (the shell polls and fires; this only holds the switch). */
	let notify = $state(false);
	let permission = $state<'denied' | 'unsupported' | 'default' | 'granted'>('default');
	$effect(() => {
		notify = localStorage.getItem('herdsman-notify') === 'on';
		permission = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
	});
	async function toggleNotify() {
		if (notify) {
			notify = false;
			localStorage.removeItem('herdsman-notify');
		} else {
			if (typeof Notification === 'undefined') { permission = 'unsupported'; return; }
			permission = await Notification.requestPermission();
			if (permission !== 'granted') return;
			/* Home's visible fleet read is the first read after opt-in. */
			localStorage.setItem('herdsman-notify-seen', JSON.stringify(items.filter((i) => i.blocking).map((i) => i.key)));
			notify = true;
			localStorage.setItem('herdsman-notify', 'on');
		}
		window.dispatchEvent(new Event('herdsman-notify-change'));
	}
	const notifyReason = $derived(
		permission === 'denied' ? 'Notifications are denied. Change the permission in browser settings.'
		: permission === 'unsupported' ? 'Browser notifications are unsupported here.' : undefined
	);
</script>

{#snippet row(item: AttentionItem)}
	{@const k = kind(item.kind)}
	{@const unread = seen.isUnread(item) || item.key === pinned}
	{@const isOpen = open === item.key}
	<div class="qi" class:unread class:failed={item.kind === 'failed'} class:open={isOpen} data-attention-key={item.key}>
		<button type="button" aria-expanded={isOpen} onclick={() => toggle(item)}>
			<i class="ud" aria-hidden="true"></i>
			<span class="ic2"><Icon name={k.icon} size={14} /></span>
			<span class="ttl">{item.summary}<small>{k.name} · {item.plan_id}{item.blocking ? '' : ' · not blocking'}</small></span>
			<span class="count">{ago(item.since, now).replace(/ (min|h|d) ago$/, '$1').replace('just now', 'now')}</span>
			<span class="chev"><Icon name="chevron-right" size={14} /></span>
		</button>
		{#if isOpen}
			<div class="det pane-in">
				<p>{item.summary}</p>
				<p class="mono muted ids">{item.plan_id}{#if item.initiative_id} · {item.initiative_id}{/if}{#if item.checkpoint_id} · {item.checkpoint_id}{/if}</p>
				<div class="btnrow">
					<Button small kind="primary" icon={item.kind === 'failed' ? 'rotate-ccw' : item.kind === 'blocked_on_user' ? 'send-horizontal' : 'check-check'} href={item.link.path}>{item.action.label}</Button>
					<span class="ibar end">
						<IconButton small icon="arrow-up-right" label="Open in Run" onclick={() => void goto(item.link.path)} />
						<IconButton small icon="mail-open" label="Mark unread" onclick={() => { seen.markUnread(item); open = null; }} />
					</span>
				</div>
			</div>
		{/if}
	</div>
{/snippet}

<aside class="queue" class:min={collapsed} aria-label="Needs you">
	{#if collapsed}
		<button type="button" class="qstrip" aria-label="Expand needs you, {items.length} waiting, {fresh.length} new" onclick={() => setCollapsed(false)}>
			<Icon name={fresh.length ? 'bell-dot' : 'bell'} size={16} />
			{#if fresh.length}<span class="dotn">{fresh.length}</span>{/if}
			<span class="vt">Needs you · {unknown ? '—' : items.length}</span>
			<Icon name="panel-right-open" size={16} />
		</button>
	{:else}
		<div class="qh">
			<Icon name={fresh.length ? 'bell-dot' : 'bell'} size={16} />
			<span class="lbl" id="needs-you-title" style="color: var(--tx)">Needs you</span>
			<span class="count">{unknown ? '—' : items.length}</span>
			{#if unreadCount}<span class="new" role="status">{unreadCount} new</span>{/if}
			<span class="ibar end">
				<IconButton small icon="bell" label={notify ? 'Notify me: on' : 'Notify me'} pressed={notify} reason={notifyReason} disabled={!!notifyReason && !notify} onclick={() => void toggleNotify()} />
				<IconButton small icon="check-check" label="Mark all read" disabled={!fresh.length} reason="Nothing unread" onclick={() => seen.markRead(items)} />
				<IconButton small icon="panel-right-close" label="Collapse" onclick={() => setCollapsed(true)} />
			</span>
		</div>
		<div class="qbody">
			{#if stale}<p class="hint note">Stale · the last read is kept</p>{/if}
			{#if unknown}
				<p class="empty-line">This daemon does not report attention.</p>
			{:else if !items.length}
				<p class="empty-line">Nothing waits on you.</p>
			{:else}
				<div class="qgroup"><span class="lbl">New</span><span class="count nd">{fresh.length}</span></div>
				{#each fresh as item (item.key)}{@render row(item)}{:else}
					<p class="hint note">Nothing new since you last looked.</p>
				{/each}
				{#if earlier.length}
					<div class="qgroup"><span class="lbl">Earlier</span><span class="count">{earlier.length}</span></div>
					{#each shownEarlier as item (item.key)}{@render row(item)}{/each}
					{#if !earlierAll && earlier.length > EARLIER}
						<div class="more"><Button small icon="ellipsis" onclick={() => (earlierAll = true)}>{earlier.length - EARLIER} more</Button></div>
					{/if}
				{/if}
			{/if}
		</div>
	{/if}
</aside>

<style>
	.queue { border-left: 1px solid var(--ln); background: var(--p1); min-height: 0; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
	.qh { padding: 14px 16px 12px 20px; border-bottom: 1px solid var(--ln); display: flex; align-items: center; gap: 10px; flex: none; }
	.qh .end, .det .end { margin-left: auto; }
	.new { font: 500 11px var(--f-label); letter-spacing: 0.14em; text-transform: uppercase; color: var(--l589); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
	.new::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--l589); animation: pulse 1.6s ease-in-out infinite; }
	.qbody { overflow: auto; min-height: 0; flex: 1; }
	.qstrip { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 14px 0; width: 100%; height: 100%; color: var(--dim); transition: color var(--t-fast); }
	.qstrip:hover { color: var(--tx); }
	.vt { writing-mode: vertical-rl; font: 500 12px var(--f-label); letter-spacing: 0.2em; text-transform: uppercase; color: var(--tx2); }
	.dotn { min-width: 20px; height: 20px; display: grid; place-items: center; background: var(--l589); color: var(--on-pri, #121212); font: 500 11px var(--f-mono); }
	.qgroup { padding: 12px 20px 6px; display: flex; align-items: center; gap: 8px; }
	.note { padding: 6px 20px 12px; }
	.more { padding: 12px 20px; }
	.qi { border-bottom: 1px solid var(--ln); }
	.qi > button { display: grid; grid-template-columns: 6px 16px minmax(0, 1fr) auto 14px; gap: 10px; align-items: center; width: 100%; padding: 11px 16px 11px 14px; text-align: left; color: var(--tx2); transition: background var(--t-fast); }
	.qi > button:hover, .qi.open > button { background: var(--p2); }
	.ud { width: 6px; height: 6px; border-radius: 50%; }
	.qi.unread .ud { background: var(--l589); }
	.qi.unread > button { color: var(--tx); }
	.ttl { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 13px; min-width: 0; }
	.qi.unread .ttl { font-weight: 600; }
	.ttl small { display: block; font: 400 11px var(--f-mono); color: var(--dim); font-weight: 400; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; }
	.ic2 { color: var(--dim); display: inline-grid; }
	.qi.unread .ic2 { color: var(--l589); }
	.qi.unread.failed .ic2 { color: var(--l656); }
	.chev { color: var(--fnt); display: inline-grid; transition: transform var(--t-fast); }
	.qi.open .chev { transform: rotate(90deg); }
	.det { padding: 2px 16px 14px 46px; }
	.det p { color: var(--tx2); margin-bottom: 4px; overflow-wrap: anywhere; }
	.ids { font-size: 11.5px; }
	.det .btnrow { margin-top: 10px; }
</style>
