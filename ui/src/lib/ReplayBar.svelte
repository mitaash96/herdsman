<script lang="ts">
	import type { ReplayStop } from './replay';

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
		return Number.isNaN(date.getTime()) ? value : `${date.toLocaleString()} (your time)`;
	};
	const valueText = (stop: ReplayStop, at: number): string =>
		`Stop ${at + 1} of ${stops.length}. ${shown(stop.at)}. ${stop.label}.`;
</script>

{#if historical && stops.length > 0}
	<section class="replay-bar" aria-label="Historical replay">
		<div class="bar-main">
			<strong class="mark">REPLAY</strong>
			<span class="bound">{shown(stops[index].at)}</span>
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
			<span class="counter">Stop {index + 1} of {stops.length}</span>
			<button class="return" type="button" onclick={onreturn}>Return to live</button>
		</div>
		<p class="orientation">
			Lanes, depth, the critical path and contention are this revision's structure; they do not move with the bound. What moves is each member's state.
		</p>
		{#if stale}<p class="stale" role="status">The previous stop remains on screen while this stop answers.</p>{/if}
	</section>
{/if}

<style>
	.replay-bar { position: sticky; top: 0; z-index: 2; padding: 0.75rem 1rem; border-block: 1px solid var(--rule-strong); background: var(--plate); }
	.bar-main { display: flex; align-items: center; gap: 0.75rem; min-height: 2rem; }
	.mark, .counter { white-space: nowrap; letter-spacing: 0.12em; font-size: 0.7rem; }
	.bound { white-space: nowrap; font-size: 0.75rem; }
	input[type='range'] { flex: 1; min-width: 5rem; accent-color: var(--ink); }
	.return { font: inherit; color: var(--ink); background: transparent; border: 1px solid var(--rule-strong); padding: 0.35rem 0.65rem; white-space: nowrap; cursor: pointer; }
	.return:hover { border-color: var(--ink); }
	.orientation, .stale { margin: 0.5rem 0 0; font-size: 0.75rem; line-height: 1.45; color: var(--ink-2); }
	.stale { color: var(--ink); }
	@media (max-width: 60rem) { .bar-main { flex-wrap: wrap; } input[type='range'] { order: 3; flex-basis: 100%; } .return { order: 4; } }
</style>
