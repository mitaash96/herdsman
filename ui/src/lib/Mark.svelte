<script lang="ts">
	import Icon from './Icon.svelte';
	import { MARKS, harnessMark, modelMark, type MarkArt } from './marks';

	/** One of `harness` or `model`; an unknown model draws the cpu icon, an unknown harness nothing. */
	let { harness, model, size = 16 }: { harness?: string | null; model?: string | null; size?: number } = $props();

	const id = $derived(harness !== undefined ? harnessMark(harness) : modelMark(model));
	const name = $derived((harness ?? model) || 'unknown');
	const art = $derived<MarkArt | null>(id ? MARKS[id] : null);
</script>

{#if art}
	<svg class="mk" width={size} height={size} viewBox={art.viewBox} role="img" aria-label={name}
		shape-rendering={art.crisp ? 'crispEdges' : undefined}
	><title>{name}</title>{@html art.body}</svg>
{:else if model !== undefined}
	<span class="mk unk" role="img" aria-label={name} title={name}><Icon name="cpu" size={size} /></span>
{/if}

<style>
	.mk { flex: none; display: inline-block; vertical-align: middle; }
	.unk { color: var(--dim); display: inline-grid; }
</style>
