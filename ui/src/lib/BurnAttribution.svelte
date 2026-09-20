<script lang="ts">
	import { categoryString } from './burn';
	import type { TokenLedger } from './daemon';
	import type { Resource } from './resource.svelte';

	let { ledger }: { ledger: Resource<TokenLedger> } = $props();
</script>

<section class="attribution" aria-label="Token attribution">
	<p class="label rule-label">
		<span>Attribution</span><span class="rule"></span>
	</p>
	{#if ledger.data == null}
		<p class="prose quiet member" data-state="slack">
			{ledger.phase === 'error'
				? `The ledger did not answer: ${ledger.error?.message ?? 'unknown failure.'}`
				: 'The ledger has not answered yet.'}
			{#if ledger.phase === 'error'}
				<button class="act" type="button" onclick={() => void ledger.load()}>Read again</button>
			{/if}
		</p>
	{:else}
		<p class="prose quiet">{ledger.data.accounted_derivation}.</p>
		<p class="prose quiet">{categoryString(ledger.data.by_category) || 'every category is at zero'}</p>
	{/if}
</section>

<style>
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 1.75rem 0 0.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.prose.quiet {
		font-size: 0.8125rem;
		color: var(--ink-2);
		max-width: 46rem;
	}
	.attribution p {
		margin: 0.35rem 0 0;
	}
	.act {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink);
		background: transparent;
		border: 1px solid var(--rule-strong);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
		margin-left: 0.75rem;
	}
	.act:hover {
		border-color: var(--red);
		color: var(--red);
	}
</style>
