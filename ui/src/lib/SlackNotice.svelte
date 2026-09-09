<script lang="ts">
	import type { View } from './views';

	let { view }: { view: View } = $props();
	const gate = $derived(view.gate);
</script>

<!--
  A view whose substrate has not landed is a member seated in the structure that
  carries no load: present, named, and visibly slack. It shows the gate and
  nothing else. It never stands in for the view's content.
-->
<section class="slack member" data-state="slack" aria-labelledby="slack-heading">
	<p class="label rule-label"><span>Member state</span><span class="rule"></span><span>Slack</span></p>

	<svg class="cord" viewBox="0 0 320 96" role="img" aria-hidden="true">
		<!-- Two seated nodes; the cord between them hangs because nothing pulls it. -->
		<path d="M20 22 Q160 84 300 22" fill="none" stroke="currentColor" stroke-width="1.25"
			stroke-dasharray="3 4" />
		<circle cx="20" cy="22" r="4.5" fill="none" stroke="currentColor" stroke-width="1.25" />
		<circle cx="300" cy="22" r="4.5" fill="none" stroke="currentColor" stroke-width="1.25" />
	</svg>

	<h2 id="slack-heading" class="slack-title">No load</h2>

	<p class="prose">
		{view.name} is a member of this structure, but nothing loads it yet. Its substrate has
		not landed, so there is nothing here to read and nothing to act on.
	</p>

	{#if gate}
		<dl class="readout plate">
			<div><dt class="label">Needs</dt><dd class="value">{gate.needs}</dd></div>
			<div><dt class="label">Owned by</dt><dd class="value">{gate.unit}</dd></div>
			{#if gate.also}
				<div class="wide"><dt class="label">Also</dt><dd>{gate.also}</dd></div>
			{/if}
		</dl>
	{/if}
</section>

<style>
	.slack {
		max-width: 46rem;
		color: var(--ash);
	}
	.rule-label {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin: 0 0 2.5rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
	}
	.cord {
		display: block;
		width: 100%;
		max-width: 28rem;
		height: auto;
		margin-bottom: 1.75rem;
	}
	.slack-title {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 70, 'wght' 620;
		font-weight: 620;
		text-transform: uppercase;
		letter-spacing: -0.01em;
		font-size: 2rem;
		line-height: 1;
		margin: 0 0 0.75rem;
		color: var(--ink-2);
	}
	.readout {
		--cut: 12px;
		margin: 2rem 0 0;
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
		gap: 1px;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		background: var(--plate);
		padding: 0.75rem 1rem;
	}
	.readout .wide {
		grid-column: 1 / -1;
	}
	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		color: var(--ink-2);
	}
	dd.value {
		color: var(--ink);
	}
</style>
