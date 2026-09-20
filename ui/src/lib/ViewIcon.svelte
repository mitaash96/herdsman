<script lang="ts">
	/**
	 * The seat a view takes on the strut's member.
	 *
	 * Four drawn glyphs, one per view, in the drawing's own hand: 1.5px members
	 * on a 16px box, no fill but the nodes of a graph, no curve that is not a
	 * node or a fastener. They are the seat itself — the ring they replace was
	 * identical for every view and said only "an item"; these say which, and
	 * each draws its view's own subject: the fleet as plates, one plan as its
	 * graph, the library as a sheet held in a set, the kitchen as the settings
	 * of the local machine.
	 *
	 * Colour and dash come from the member state vocabulary through
	 * `currentColor`, so a glyph never carries a state of its own.
	 */
	import type { View } from './views';

	let { id }: { id: View['id'] } = $props();
</script>

<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
	{#if id === 'home'}
		<!-- The fleet: every plate on the board at once. -->
		<rect x="2.5" y="2.5" width="5" height="5" />
		<rect x="8.5" y="2.5" width="5" height="5" />
		<rect x="2.5" y="8.5" width="5" height="5" />
		<rect x="8.5" y="8.5" width="5" height="5" />
	{:else if id === 'run'}
		<!-- One plan: a brief, and the initiatives it forks into. Drawn falling,
		     the way the plan runs, so it is a graph and not a share mark. -->
		<circle cx="8" cy="3.25" r="1.75" class="node" />
		<path d="M8 5v2" />
		<path d="M4 10.5 8 7l4 3.5" />
		<circle cx="4" cy="12.25" r="1.75" class="node" />
		<circle cx="12" cy="12.25" r="1.75" class="node" />
	{:else if id === 'library'}
		<!-- A sheet held in a set: the asset, and the copies it is taken from. -->
		<path d="M5.5 5.5v-3h8v8h-3" />
		<rect x="2.5" y="5.5" width="8" height="8" />
	{:else}
		<!-- The kitchen: the local machine's own settings, seated on their runs. -->
		<path d="M2 5.5h12" />
		<rect x="4.5" y="4" width="3" height="3" />
		<path d="M2 11h12" />
		<rect x="8.5" y="9.5" width="3" height="3" />
	{/if}
</svg>

<style>
	svg {
		width: 16px;
		height: 16px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
	}
	/* A graph's nodes are seats, and a seat is drawn filled — at 3px across an
	   outlined one is a smudge. */
	.node {
		fill: currentColor;
		stroke: none;
	}
</style>
