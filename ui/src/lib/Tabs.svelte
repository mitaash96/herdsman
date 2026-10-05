<script lang="ts" module>
	import type { IconName } from './icons';
	export interface TabItem {
		id: string;
		label: string;
		icon?: IconName;
		/** A count or short tag after the label (`v2`, `9`, `+112 −14`). */
		count?: string | number;
		countTone?: 'nd' | 'bad';
	}
	/** Ids the caller's panel uses: `role="tabpanel" id={panelId(p, id)} aria-labelledby={tabId(p, id)}`. */
	export const tabId = (prefix: string, id: string) => `${prefix}-tab-${id}`;
	export const panelId = (prefix: string, id: string) => `${prefix}-panel-${id}`;
</script>

<script lang="ts">
	import Icon from './Icon.svelte';

	/** Hairline tabs with a sliding indicator (DS §9.5); roving tabindex, ←/→/Home/End. */
	let {
		items,
		selected,
		prefix,
		label,
		small = false,
		onselect
	}: {
		items: readonly TabItem[];
		selected: string;
		prefix: string;
		label: string;
		small?: boolean;
		onselect: (id: string) => void;
	} = $props();

	let list = $state<HTMLDivElement>();
	let ind = $state({ left: 0, width: 0, ready: false });

	function measure() {
		const el = list?.querySelector<HTMLElement>(`[aria-selected="true"]`);
		if (!el) return;
		ind = { left: el.offsetLeft, width: el.offsetWidth, ready: true };
	}
	$effect(() => {
		void selected;
		void items.length;
		measure();
	});
	$effect(() => {
		if (!list) return;
		const ro = new ResizeObserver(measure);
		ro.observe(list);
		document.fonts?.ready.then(measure);
		return () => ro.disconnect();
	});

	function onkeydown(event: KeyboardEvent) {
		const i = items.findIndex((t) => t.id === selected);
		let next = -1;
		if (event.key === 'ArrowRight') next = (i + 1) % items.length;
		else if (event.key === 'ArrowLeft') next = (i - 1 + items.length) % items.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = items.length - 1;
		if (next < 0) return;
		event.preventDefault();
		onselect(items[next].id);
		requestAnimationFrame(() => list?.querySelector<HTMLElement>(`#${CSS.escape(tabId(prefix, items[next].id))}`)?.focus());
	}
</script>

<div class="tabs" class:sm={small} role="tablist" aria-label={label} bind:this={list} {onkeydown} tabindex="-1">
	{#each items as t (t.id)}
		<button
			type="button"
			class="tab"
			role="tab"
			id={tabId(prefix, t.id)}
			aria-selected={t.id === selected}
			aria-controls={panelId(prefix, t.id)}
			tabindex={t.id === selected ? 0 : -1}
			onclick={() => onselect(t.id)}
		>
			{#if t.icon}<Icon name={t.icon} size={small ? 14 : 15} />{/if}
			{t.label}
			{#if t.count !== undefined && t.count !== ''}<span class="count" class:nd={t.countTone === 'nd'} class:bad={t.countTone === 'bad'}>{t.count}</span>{/if}
		</button>
	{/each}
	<span class="ind" class:ready={ind.ready} style:left="{ind.left}px" style:width="{ind.width}px" aria-hidden="true"></span>
</div>

<style>
	.tabs { display: flex; align-items: stretch; position: relative; gap: 2px; min-width: 0; outline: none; }
	.tab {
		display: flex; align-items: center; gap: 7px; padding: 0 14px; height: 38px; white-space: nowrap;
		font: 500 12px/1 var(--f-label); letter-spacing: 0.14em; text-transform: uppercase; color: var(--dim);
		position: relative; transition: color var(--t-fast);
	}
	.tab:hover, .tab[aria-selected='true'] { color: var(--tx); }
	.sm .tab { height: 34px; padding: 0 12px; }
	.ind { position: absolute; bottom: -1px; height: 1px; background: var(--tx); opacity: 0; pointer-events: none; }
	.ind.ready { opacity: 1; transition: left var(--t-tab) var(--ease), width var(--t-tab) var(--ease); }
	.ind::after {
		content: ''; position: absolute; left: 50%; bottom: 0; width: 1px; height: 5px;
		background: var(--tx); transform: translateX(-50%);
	}
</style>
