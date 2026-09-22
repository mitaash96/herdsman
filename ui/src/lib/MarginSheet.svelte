<script lang="ts">
	import type { Snippet } from 'svelte';

	export type MarginSection = {
		id: string;
		label: string;
		count?: string | number;
		state?: 'slack' | 'balanced' | 'loaded' | 'seated' | 'failed';
		hidden?: boolean;
	};

	/*
	  The sheet's one-hero frame (direction A · Margin): the drawing takes the
	  left column, the view's readouts ride the right one like the notes column
	  on a drawing, and every secondary section becomes an entry in the index
	  under them that opens in the one DrawerSeat.

	    <MarginSheet {sections} bind:open>
	      {#snippet caption()} … {/snippet}  the hero's ridden rule-label + its switch
	      {#snippet hero()} … {/snippet}     THE drawing
	      {#snippet margin()} … {/snippet}   readouts, most important (red) first
	    </MarginSheet>

	  caption is optional; hero and margin are required. `sections` is a
	  MarginSection[] ({ id, label, count?, state?, hidden? }) and `open` is a
	  bindable string | null — both optional: a view with no secondary sections
	  renders no index and never opens the seat. The margin is sticky; a docked
	  seat is absorbed by the right column, so it can never cover the hero.
	  Below 60rem it stacks: caption, hero, then the margin as a wrapping row
	  of readout cells, then the index.
	*/
	let {
		sections = [],
		open = $bindable(null),
		caption,
		hero,
		margin
	}: {
		sections?: MarginSection[];
		open?: string | null;
		caption?: Snippet;
		hero: Snippet;
		margin: Snippet;
	} = $props();

	const visible = $derived(sections.filter((section) => !section.hidden));
</script>

<div class="ms">
	{#if caption}
		<div class="cap">{@render caption()}</div>
	{/if}

	<div class="hero">{@render hero()}</div>

	<div class="margin">
		{@render margin()}

		{#if visible.length > 0}
			<nav class="index" aria-label="Index">
				<p class="label ruled"><span>Index</span><span class="rule"></span></p>
				{#each visible as section (section.id)}
					<button
						type="button"
						class="ix"
						aria-expanded={open === section.id}
						aria-controls="seat"
						onclick={() => (open = section.id)}
					>
						<span class="ix-name">{section.label}</span>
						<span class="rule"></span>
						{#if section.count !== undefined}
							<span class="ix-n" class:member={section.state !== undefined} data-state={section.state}>{section.count}</span>
						{/if}
					</button>
				{/each}
			</nav>
		{/if}
	</div>
</div>

<style>
	.ms {
		--margin-w: 18rem;
		display: grid;
		/* The right column absorbs a docked seat: `--seat-w` minus the sheet's own
		   right padding (.sheet 2.5rem + .sheet-inner 2.75rem) reaches from the
		   seat's leading edge back to this column, so the column is never
		   narrower than the seat's claim and the seat lands off the hero. */
		grid-template-columns: minmax(0, 1fr) max(var(--margin-w), var(--seat-w, 0px) - 5.25rem);
		gap: 2.5rem;
	}
	.cap {
		grid-column: 1 / -1;
		min-width: 0;
	}
	.hero {
		min-width: 0;
	}
	/* The notes column: it stays on screen while the drawing scrolls past. */
	.margin {
		position: sticky;
		top: 1.5rem;
		max-height: calc(100dvh - 3rem);
		overflow: auto;
		min-width: 0;
	}

	/* The margin is one line of readings at a time, and a gloss is a note:
	   kept, but cut at two lines. Below 60rem it is the view's own wrapping
	   row of cells instead, so this stops at the stack. */
	@media (min-width: 60rem) {
		.margin :global(dl.readout) {
			display: grid;
			grid-template-columns: minmax(0, 1fr);
		}
	}
	.margin :global(.gloss) {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
	}

	/* --- the index: a ruled label over one bare button per section ---------- */
	.index {
		margin-top: 1.5rem;
		/* room inside the scroll box for the open entry's ring */
		padding: 0 4px;
	}
	.ruled {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 0.35rem;
	}
	.ruled .rule,
	.ix .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.ix {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		width: 100%;
		font: inherit;
		font-size: 0.75rem;
		color: var(--ink);
		background: none;
		border: 0;
		padding: 0.4rem 0;
		text-align: left;
		cursor: pointer;
	}
	.ix:hover {
		color: var(--red);
	}
	/* Open is location, drawn the way location is drawn everywhere: the
	   carbon ring, never red — a section carries no load. */
	.ix[aria-expanded='true'] {
		box-shadow: 0 0 0 3px var(--plate), 0 0 0 4px var(--member-line);
	}
	.ix-n {
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--ink);
	}

	/* Below 60rem the margin drops under the hero as one wrapping row of
	   readout cells — the view's own row layout, unforced. */
	@media (max-width: 60rem) {
		.ms {
			grid-template-columns: minmax(0, 1fr);
			gap: 1.75rem;
		}
		.margin {
			position: static;
			max-height: none;
			overflow: visible;
		}
	}
</style>
