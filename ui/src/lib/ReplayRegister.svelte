<script lang="ts">
	import type { Plan, PolicyDecisionRecorded } from './daemon';
	import { ruleName, type ReplayStop } from './replay';

	let { stops, index, plan, onstop, onselect }: {
		stops: ReplayStop[];
		index: number;
		plan: Plan;
		onstop: (index: number) => void;
		onselect: (id: string) => void;
	} = $props();

	const when = (value: string): string => {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
	};
	const outcome: Record<PolicyDecisionRecorded['outcome'], { word: string; state: string }> = {
		approved: { word: 'Approved', state: 'seated' },
		stopped: { word: 'Stopped', state: 'failed' },
		escalated: { word: 'Escalated', state: 'slack' }
	};
	const initiatives = $derived(plan.initiatives);
	const initiativeName = (id: string): string => initiatives[id]?.spec.name ?? id;
	const decisions = $derived(plan.policy_decisions ?? []);
</script>

<section class="registers">
	<section class="register">
		<p class="label rule-label"><span>Recorded stops</span><span class="rule"></span><span>{stops.length}</span></p>
		<p class="prose quiet">These are the dated records this projection keeps. The run's event log holds more, and a record with no date of its own takes effect at the next stop at or after it.</p>
		<div class="rows" role="listbox" aria-label="Replay stops">
			{#each stops as stop, at (stop.at)}
				<button class:selected={at === index} class="stop-row" type="button" role="option" aria-selected={at === index} onclick={() => onstop(at)}>
					<span class="instant">{when(stop.at)}</span>
					<span>{stop.label}</span>
				</button>
			{/each}
		</div>
	</section>

	<section class="register">
		<p class="label rule-label"><span>Automatic decisions</span><span class="rule"></span><span>{decisions.length}</span></p>
		{#if decisions.length === 0}
			<p class="prose quiet">No automatic decision had been recorded by this moment.</p>
		{:else}
			<div class="rows">
				{#each decisions as decision (decision.seq)}
					{@const result = outcome[decision.outcome]}
					<article class="decision-row">
						<button class="subject" type="button" onclick={() => onselect(decision.initiative_id)}>
							<strong>{initiativeName(decision.initiative_id)}</strong> <code>{decision.initiative_id}</code>
						</button>
						<span class="member outcome" data-state={result.state}>{result.word}</span>
						<span class="rules">
							{#each decision.rule_ids as id, at (id + at)}
								<span class="rule-name">{ruleName(id) ?? id}</span>{#if ruleName(id) === null}<span class="quiet">recorded by a newer daemon; this build has no name for it</span>{/if}
							{/each}
						</span>
						<span class="instant">{when(decision.at)}</span>
						<q>{decision.reason || 'no reason was recorded'}</q>
					</article>
				{/each}
			</div>
		{/if}
	</section>
</section>

<style>
	.registers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2rem; margin-top: 3rem; }
	.rule-label { display: flex; align-items: baseline; gap: 0.75rem; margin: 0 0 1rem; }
	.rule-label .rule { flex: 1; height: 1px; background: var(--rule); }
	.rows { border-top: 1px solid var(--rule-strong); }
	.stop-row, .decision-row { display: grid; gap: 0.4rem; width: 100%; padding: 0.7rem 0; border: 0; border-bottom: 1px solid var(--rule); background: transparent; color: var(--ink-2); text-align: left; font: inherit; }
	.stop-row { grid-template-columns: 11rem 1fr; cursor: pointer; }
	.stop-row.selected { color: var(--ink); border-left: 3px solid var(--ink); padding-left: 0.5rem; }
	.decision-row { grid-template-columns: 1fr auto; }
	.subject { border: 0; padding: 0; background: none; color: var(--ink); text-align: left; font: inherit; cursor: pointer; }
	.outcome { white-space: nowrap; }
	.rules { display: flex; flex-wrap: wrap; gap: 0.35rem; font-size: 0.75rem; }
	.rule-name { border-bottom: 1px solid var(--rule-strong); }
	.instant { font-size: 0.75rem; color: var(--ink-2); }
	q { grid-column: 1 / -1; color: var(--ink-2); }
	@media (max-width: 60rem) { .registers { grid-template-columns: 1fr; } }
</style>
