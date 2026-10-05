<script lang="ts" module>
	export interface RichOption {
		value: string;
		label: string;
		harness?: string;
		model?: string;
		/** A readiness word or short note at the right. */
		word?: string;
		wordTone?: 'pass' | 'waiting' | 'failed';
		disabled?: boolean;
	}
</script>

<script lang="ts">
	import Icon from './Icon.svelte';
	import Mark from './Mark.svelte';

	/** A select that shows marks (DS §9.12 `.selx`); a listbox in palette styling. No focus trap. */
	let {
		options,
		value,
		label,
		placeholder = 'Choose…',
		disabled = false,
		onchange
	}: {
		options: readonly RichOption[];
		value: string | null;
		label: string;
		placeholder?: string;
		disabled?: boolean;
		onchange: (value: string) => void;
	} = $props();

	const id = $props.id();
	let open = $state(false);
	let active = $state(0);
	let root = $state<HTMLDivElement>();
	const current = $derived(options.find((o) => o.value === value) ?? null);

	function show() {
		if (disabled) return;
		open = true;
		active = Math.max(0, options.findIndex((o) => o.value === value));
	}
	function pick(o: RichOption) {
		if (o.disabled) return;
		onchange(o.value);
		open = false;
		root?.querySelector<HTMLButtonElement>('button.selx')?.focus();
	}
	function onkeydown(event: KeyboardEvent) {
		if (!open) {
			if (['ArrowDown', 'Enter', ' '].includes(event.key)) { event.preventDefault(); show(); }
			return;
		}
		if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); open = false; }
		else if (event.key === 'ArrowDown') { event.preventDefault(); active = Math.min(options.length - 1, active + 1); }
		else if (event.key === 'ArrowUp') { event.preventDefault(); active = Math.max(0, active - 1); }
		else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (options[active]) pick(options[active]); }
		else if (event.key === 'Tab') open = false;
	}
</script>

<svelte:window onpointerdown={(e) => { if (open && root && !root.contains(e.target as Node)) open = false; }} />

{#snippet face(o: RichOption)}
	{#if o.harness !== undefined}<Mark harness={o.harness} size={14} /><span class="mono">{o.harness}</span>{/if}
	{#if o.harness !== undefined && o.model !== undefined}<Icon name="chevron-right" size={12} />{/if}
	{#if o.model !== undefined}<Mark model={o.model} size={14} /><span class="mono ellipsis">{o.model}</span>{/if}
	{#if o.harness === undefined && o.model === undefined}<span class="ellipsis">{o.label}</span>{/if}
{/snippet}

<div class="rs" bind:this={root}>
	<button type="button" class="selx" aria-haspopup="listbox" aria-expanded={open} aria-label={label}
		aria-controls={open ? `${id}-list` : undefined} {disabled} onclick={() => (open ? (open = false) : show())} {onkeydown}
	>
		<span class="v">{#if current}{@render face(current)}{:else}<span class="muted">{placeholder}</span>{/if}</span>
		{#if current?.word}<span class="state" data-tone={current.wordTone ?? 'pass'}>{current.word}</span>{/if}
		<span class="chev"><Icon name="chevron-down" size={14} /></span>
	</button>
	{#if open}
		<ul class="list pane-in" role="listbox" id="{id}-list" aria-label={label} tabindex="-1">
			{#each options as o, i (o.value)}
				<li role="option" aria-selected={o.value === value} aria-disabled={o.disabled || undefined}
					class:on={i === active} onpointermove={() => (active = i)} onclick={() => pick(o)}
					onkeydown={() => {}}
				>
					<span class="v">{@render face(o)}</span>
					{#if o.word}<span class="state" data-tone={o.wordTone ?? 'pass'}>{o.word}</span>{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.rs { position: relative; min-width: 0; }
	.selx {
		display: flex; align-items: center; gap: 8px; width: 100%; height: 34px; padding: 0 10px;
		background: var(--p2); border: 1px solid var(--ln2); font: 400 13px var(--f-ui); text-align: left;
		transition: border-color var(--t-fast);
	}
	.selx:hover:not(:disabled), .selx[aria-expanded='true'] { border-color: var(--dim); }
	.selx:disabled { color: var(--fnt); border-color: var(--ln); cursor: not-allowed; }
	.v { flex: 1; display: flex; align-items: center; gap: 7px; min-width: 0; color: var(--tx2); }
	.v :global(.i) { color: var(--fnt); }
	.chev { color: var(--dim); display: inline-grid; }
	.list {
		position: absolute; left: 0; right: 0; top: calc(100% + 4px); z-index: 70; max-height: 280px; overflow: auto;
		background: var(--p2); border: 1px solid var(--ln2); padding: 4px 0;
	}
	li { display: flex; align-items: center; gap: 10px; padding: 7px 10px; cursor: pointer; }
	li.on { background: var(--p3); }
	li.on .v { color: var(--tx); }
	li[aria-disabled] { opacity: 0.5; cursor: not-allowed; }
</style>
