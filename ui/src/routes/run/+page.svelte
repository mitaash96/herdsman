<script lang="ts">
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import AsyncField from '$lib/AsyncField.svelte';
	import type { Resource } from '$lib/resource.svelte';
	import type { PlanGraph } from '$lib/daemon';

	const plan = getContext<{
		readonly resource: Resource<PlanGraph> | null;
		readonly id: string | null;
		reload: () => void;
	}>('plan');

	let entered = $state('');

	const ready = $derived(entered.trim().length > 0);

	function address(event: SubmitEvent) {
		event.preventDefault();
		const id = entered.trim();
		if (id) void goto(`/run?plan=${encodeURIComponent(id)}`);
	}
</script>

{#if !plan.id}
	<!-- Unavailable action, stated as such: there is no plan picker because the
	     daemon exposes no collection route to build one from. -->
	<section class="addressing">
		<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>Not addressed</span></p>
		<p class="prose">
			A plan is addressed by id. The daemon has no <code>GET /plans</code> route, so this
			build cannot list the plans on disk and offer you a picker; it can only open the one
			you name. Home (H1) is where a real plan index belongs, once that route exists.
		</p>
		<form onsubmit={address}>
			<label class="label" for="plan-id">Plan id</label>
			<div class="row">
				<input class="plate" id="plan-id" bind:value={entered} spellcheck="false"
					autocomplete="off" aria-describedby="plan-id-help" />
				<button class="plate" type="submit" disabled={!ready}>Open</button>
			</div>
		</form>
		<p id="plan-id-help" class="req">
			A plan id is required; there is nothing to open without one.
		</p>
		<p class="prose quiet">
			No plan on disk yet? <code>uv run python ui/dev/seed_plan.py</code> writes a real
			Sprint 2 plan into the project's event store and prints its id.
		</p>
	</section>
{:else if plan.resource}
	<AsyncField resource={plan.resource} reading="the plan projection" onretry={plan.reload}>
		{#snippet children(graph: PlanGraph)}
			{#if graph.nodes.length === 0}
				<section>
					<p class="label rule-label"><span>Plan</span><span class="rule"></span><span>No initiatives</span></p>
					<p class="prose">
						Plan <strong>{graph.plan_id}</strong> exists at revision {graph.version}, and
						its planner proposed no initiatives. There is no structure to load.
					</p>
				</section>
			{:else}
				<section>
					<p class="label rule-label"><span>Sheet</span><span class="rule"></span><span>Not drawn</span></p>
					<p class="prose">
						The daemon is answering and plan <strong>{graph.plan_id}</strong> reads
						cleanly at revision {graph.version}. Its {graph.nodes.length} initiatives are
						not drawn here: the Run spine — the proposed, running and settled DAG, the
						critical path and the contention lanes — is unit R1's, built in its own
						session on this shell.
					</p>
					<p class="prose quiet">
						This page exists to prove the shell reads real daemon state and renders every
						state it can be in. Reload with the daemon stopped to see the broken load
						path; stop it after a successful read to see values preserved and marked
						stale.
					</p>
				</section>
			{/if}
		{/snippet}
	</AsyncField>
{/if}

<style>
	.rule-label {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin: 0 0 1.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
	}
	.addressing {
		max-width: 46rem;
	}
	form {
		margin: 2rem 0 2.25rem;
	}
	.row {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.4rem;
		max-width: 28rem;
	}
	input {
		--cut: 10px;
		font: inherit;
		flex: 1;
		min-width: 0;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	input:focus-visible {
		border-color: var(--red);
	}
	button {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink);
		background: transparent;
		border: 1px solid var(--rule-strong);
		padding: 0.4rem 1rem;
		cursor: pointer;
	}
	button:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}
	button:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	.req {
		margin: 0.4rem 0 0;
		max-width: 28rem;
		font-size: 0.625rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.quiet {
		font-size: 0.8125rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}
	strong {
		color: var(--ink);
		font-weight: 500;
	}
</style>
