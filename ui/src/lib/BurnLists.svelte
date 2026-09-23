<script lang="ts">
	import type { StatusBundle } from './daemon';
	import { ceilingsOf, groupAnomalies } from './burn';

	let {
		bundle,
		selected,
		onselect
	}: {
		bundle: StatusBundle;
		selected: string | null;
		onselect: (id: string) => void;
	} = $props();

	const ceilings = $derived(ceilingsOf(bundle.burn_down.remaining_initiative_caps));
	const groups = $derived(groupAnomalies(bundle.anomalies));
</script>

{#if ceilings.rows.length > 0}
	<section class="instrument-list" aria-label="Budget ceilings">
		<p class="label list-label">
			<span>Ceilings</span><span class="rule"></span>
			<span>{ceilings.declared} of {ceilings.total} members</span>
		</p>
		<ul class="bare">
			{#each ceilings.rows as row (row.id)}
				<li>
					<button
						class="pick"
						type="button"
						tabindex={row.id === selected ? 0 : -1}
						onclick={() => onselect(row.id)}
					>
						<span class="mark">{row.id}</span>
						<span class="member" data-state={row.state}>{row.value}</span>
					</button>
				</li>
			{/each}
		</ul>
	</section>
{/if}

{#if groups.length > 0}
	<section class="instrument-list" aria-label="Burn findings">
		<p class="label list-label">
			<span>Findings</span><span class="rule"></span>
			<span>{groups.length} {groups.length === 1 ? 'kind' : 'kinds'}</span>
		</p>
		<ul class="bare">
			{#each groups as group (group.code)}
				<li>
					<span
						class="grouplabel"
						data-state={group.code === 'missing-usage' ? 'slack' : 'failed'}
					>
						{group.label} · {group.count}
					</span>
					<p class="prose quiet">{group.text}</p>
					{#if group.plan}
						<p class="bare-ids">
							<span class="plan-row">
								<span class="label">Plan</span>
								<span class="member" data-state="failed">plan cap</span>
							</span>
						</p>
					{/if}
					{#if group.ids.length > 0}
						<p class="bare-ids">
							{#each group.ids as id (id)}
								<button
									class="pick id"
									type="button"
									tabindex={id === selected ? 0 : -1}
									onclick={() => onselect(id)}
								>
									<span class="mark">{id}</span>
								</button>
							{/each}
						</p>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.instrument-list + .instrument-list {
		margin-top: 1.5rem;
	}
	.list-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 0.5rem;
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.list-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.bare {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.bare > li + li {
		margin-top: 0.25rem;
	}
	.instrument-list[aria-label='Burn findings'] .bare > li + li {
		margin-top: 1.25rem;
	}
	/* Bare rows, the schedule's own idiom: font inherits, no border, red on
	   hover. There is no repeated operable edge down a list. */
	.pick {
		font: inherit;
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		text-align: left;
		background: none;
		border: 0;
		padding: 0.15rem 0;
		color: inherit;
		cursor: pointer;
	}
	.pick .mark {
		font-weight: 500;
		color: var(--ink);
	}
	.pick.id .mark {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.pick:hover .mark {
		color: var(--red);
	}
	.grouplabel {
		display: inline-block;
		font-weight: 500;
	}
	.bare-ids {
		margin: 0.25rem 0 0;
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.9rem;
	}
	.plan-row {
		display: inline-flex;
		align-items: baseline;
		gap: 0.6rem;
	}
	.plan-row .label {
		font-size: 0.625rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
</style>
