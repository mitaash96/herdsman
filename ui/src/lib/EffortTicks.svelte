<script lang="ts">
	import { effortTicks } from './tones';
	/** Four hairlines; the level and below stand tall (DS §9.11). The word lives beside or in a tooltip. */
	let { effort, ladder }: { effort: string | null | undefined; ladder?: readonly string[] } = $props();
	const n = $derived(effortTicks(effort, ladder));
</script>

<span class="effort" aria-hidden="true">{#each [1, 2, 3, 4] as i (i)}<i class:on={i <= n}></i>{/each}</span>
