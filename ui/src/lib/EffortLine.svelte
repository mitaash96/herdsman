<script lang="ts">
	/**
	 * The full effort vocabulary in daemon order (DS §9.10). Viewing: the pool is
	 * underlined. Editing: toggles plus ALL, with a ≥1 floor. Never wraps.
	 */
	let {
		levels,
		selected,
		editing = false,
		onchange
	}: {
		levels: readonly string[];
		selected: readonly string[];
		editing?: boolean;
		onchange?: (next: string[]) => void;
	} = $props();

	const on = (l: string) => selected.includes(l);
	function toggle(l: string) {
		const next = on(l) ? selected.filter((x) => x !== l) : levels.filter((x) => x === l || selected.includes(x));
		if (next.length) onchange?.(next);
	}
	const FLOOR = 'At least one effort level must stay selected';
</script>

<span class="effline">
	{#if levels.length === 0}
		<span class="eff none">harness default</span>
	{:else if editing}
		{#each levels as l (l)}
			{@const last = on(l) && selected.length === 1}
			<button type="button" class="eff fade-in" aria-pressed={on(l)} disabled={last}
				title={last ? FLOOR : undefined} onclick={() => toggle(l)}>{l}</button>
		{/each}
		<button type="button" class="eff all fade-in" aria-pressed={selected.length === levels.length}
			onclick={() => onchange?.([...levels])}>All</button>
	{:else}
		{#each levels as l (l)}<span class="eff" class:on={on(l)}>{l}</span>{/each}
	{/if}
</span>

<style>
	.effline { display: flex; flex-wrap: nowrap; align-items: baseline; gap: 2px; white-space: nowrap; }
	.eff {
		font: 500 11.5px/1 var(--f-label); letter-spacing: 0.14em; text-transform: uppercase; color: var(--dim);
		border-bottom: 1px solid transparent; padding: 4px 6px 5px; white-space: nowrap;
		transition: color var(--t-fast), border-color var(--t-fast);
	}
	.eff.on, .eff[aria-pressed='true'] { color: var(--tx); border-bottom-color: var(--tx2); }
	button.eff:hover:not(:disabled) { color: var(--tx); }
	button.eff:hover:not(:disabled):not([aria-pressed='true']) { border-bottom-color: var(--ln2); }
	button.eff:disabled { cursor: default; }
	.eff.all { margin-left: 6px; padding-left: 10px; border-left: 1px solid var(--ln); }
	.eff.none { text-transform: none; letter-spacing: 0.02em; font: 400 12px var(--f-ui); border-bottom: 1px dashed var(--ln2); }
</style>
