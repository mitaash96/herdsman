<script lang="ts">
	import type { ReplayStop } from './replay';
	import Button from './Button.svelte';

	/** The 40px replay bar between subnav and stage (views.md §2.1): stops scrubber and return-to-live. */
	let { stops, index, historical, onindex, onreturn, stale = false }: {
		stops: ReplayStop[];
		index: number;
		historical: boolean;
		onindex: (index: number) => void;
		onreturn: () => void;
		stale?: boolean;
	} = $props();

	const shown = (value: string): string => {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
	};
	const valueText = (stop: ReplayStop, at: number): string =>
		`Stop ${at + 1} of ${stops.length}. ${shown(stop.at)}. ${stop.label}.`;
</script>

{#if historical && stops.length > 0}
	<section class="replay-bar" aria-label="Historical replay">
		<span class="state" data-tone="violet">Replay</span>
		<span class="mono bound">{shown(stops[index].at)}</span>
		<input
			type="range"
			min="0"
			max={stops.length - 1}
			step="1"
			value={index}
			aria-label="Replay position"
			aria-valuetext={valueText(stops[index], index)}
			oninput={(event) => onindex(Number((event.currentTarget as HTMLInputElement).value))}
		/>
		<span class="mono muted counter">{index + 1} / {stops.length}</span>
		{#if stale}<span class="state" data-tone="waiting" role="status" title="The previous stop remains on screen while this stop answers.">Reading</span>{/if}
		<Button icon="arrow-up-right" small onclick={onreturn}>Return to live</Button>
	</section>
{/if}

<style>
	.replay-bar {
		display: flex; align-items: center; gap: 16px; height: 40px; padding: 0 24px; background: var(--p1);
		border-bottom: 1px solid var(--ln);
	}
	.bound { white-space: nowrap; color: var(--tx2); }
	.counter { white-space: nowrap; }
	input[type='range'] { flex: 1; min-width: 5rem; accent-color: var(--tx); }
</style>
