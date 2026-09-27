<script lang="ts">
	import { tick } from 'svelte';
	import type { AttentionItem } from './daemon';
	import { ago, kindName } from './bank';
	import { attentionSurface } from './attention';

	let { items, now, stale = false, unknown = false }: {
		items: AttentionItem[]; now: number; stale?: boolean; unknown?: boolean;
	} = $props();
	let enabled = $state(false);
	let permission = $state<'denied' | 'unsupported' | 'default' | 'granted'>('default');
	let announcement = $state('');
	let former: string[] = [];
	let lostFocus: number | null = null;
	$effect(() => {
		enabled = localStorage.getItem('herdsman-notify') === 'on';
		permission = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
	});
	$effect.pre(() => {
		const keys = items.map((item) => item.key);
		const focusedKey = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>('[data-attention-key]')?.dataset.attentionKey;
		lostFocus = focusedKey && !keys.includes(focusedKey) ? former.indexOf(focusedKey) : null;
	});
	$effect(() => {
		const keys = items.map((item) => item.key);
		if (lostFocus !== null && lostFocus >= 0) {
			const index = lostFocus;
			void tick().then(() => (document.querySelector<HTMLElement>(`[data-attention-index="${Math.min(index, keys.length - 1)}"] a`) ?? document.getElementById('home-attention-title'))?.focus());
			lostFocus = null;
		}
		if (former.length && keys.filter((key) => !former.includes(key)).length) {
			announcement = `${keys.filter((key) => !former.includes(key)).length} new waiting`;
		}
		former = keys;
	});
	async function toggle() {
		if (enabled) {
			enabled = false;
			localStorage.removeItem('herdsman-notify');
		} else {
			if (typeof Notification === 'undefined') { permission = 'unsupported'; return; }
			permission = await Notification.requestPermission();
			if (permission !== 'granted') return;
			/* Home's visible fleet read is the first read after opt-in. */
			localStorage.setItem('herdsman-notify-seen', JSON.stringify(items.filter((item) => item.blocking).map((item) => item.key)));
			enabled = true;
			localStorage.setItem('herdsman-notify', 'on');
		}
		window.dispatchEvent(new Event('herdsman-notify-change'));
	}
</script>

<div class="feed-tools">
	<button type="button" class="plate control" aria-pressed={enabled} onclick={() => void toggle()}>
		{enabled ? 'Notify me: on' : 'Notify me'}
	</button>
	{#if permission === 'denied'}<p>Notifications denied. Change permission in browser settings.</p>{/if}
	{#if permission === 'unsupported'}<p>Browser notifications are unsupported here.</p>{/if}
</div>
<p class="sr" aria-live="polite">{announcement}</p>
{#if stale}<p class="label">Stale · the last read is kept</p>{/if}
{#if unknown}
	<p>This daemon does not report attention.</p>
{:else if !items.length}
	<p>Nothing needs you right now.</p>
{:else}
	<div role="list" class="feed">
		{#each items as item, index (item.key)}
			<article role="listitem" aria-labelledby="attention-{index}" class:quiet={!item.blocking} data-attention-key={item.key} data-attention-index={index}>
				<p class="label" id="attention-{index}">{kindName(item.kind)} · {item.plan_id}{#if item.checkpoint_id} · {item.checkpoint_id}{:else if item.initiative_id} · {item.initiative_id}{/if} · {ago(item.since, now)}{#if !item.blocking} · not blocking{/if}</p>
				<p>{item.summary}</p>
				<a href={item.link.path}>Open in Run → {attentionSurface(item)} · {item.action.label}</a>
			</article>
		{/each}
	</div>
{/if}
<p class="coverage">Covers plan gates, checkpoint reviews, agent questions, failures, stalls. Recovery of stale attempts is read in Run's Recovery section; it is not an attention item yet.</p>

<style>
	.feed { max-height: 65dvh; overflow-y: auto; }
	article { padding: 1rem 0; border-top: 1px solid var(--rule); overflow-wrap: anywhere; }
	article p { margin: 0 0 .6rem; }
	article.quiet { color: var(--ink-2); }
	.feed-tools { display: flex; flex-wrap: wrap; align-items: baseline; gap: .75rem; margin-bottom: 1.25rem; }
	.feed-tools p, .coverage { color: var(--ink-2); font-size: .8125rem; }
	.control { font: inherit; background: var(--plate); border: 1px solid var(--rule-strong); color: var(--ink); padding: .4rem .7rem; cursor: pointer; }
	.control[aria-pressed='true'] { box-shadow: 0 0 0 2px var(--member-line); }
	.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
</style>
