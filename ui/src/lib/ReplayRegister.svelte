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
	const outcome: Record<PolicyDecisionRecorded['outcome'], { word: string; tone: string }> = {
		approved: { word: 'Approved', tone: 'pass' },
		stopped: { word: 'Stopped', tone: 'failed' },
		escalated: { word: 'Escalated', tone: 'needs' }
	};
	const initiatives = $derived(plan.initiatives);
	const initiativeName = (id: string): string => initiatives[id]?.spec.name ?? id;
	const decisions = $derived(plan.policy_decisions ?? []);
</script>

<section class="registers pane-in">
	<section>
		<div class="rule-h"><span class="lbl">Recorded stops</span><span class="r count">{stops.length}</span></div>
		<table class="tbl">
			<thead><tr><th>Moment</th><th>Record</th></tr></thead>
			<tbody>
				{#each stops as stop, at (stop.at)}
					<tr class="click" aria-current={at === index ? 'true' : undefined} onclick={() => onstop(at)}>
						<td class="mono">{when(stop.at)}</td>
						<td>{stop.label}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section>
		<div class="rule-h"><span class="lbl">Automatic decisions</span><span class="r count">{decisions.length}</span></div>
		{#if decisions.length === 0}
			<p class="empty-line">No automatic decision had been recorded by this moment.</p>
		{:else}
			<table class="tbl">
				<thead><tr><th>Member</th><th>Outcome</th><th>Rule</th><th>Reason</th></tr></thead>
				<tbody>
					{#each decisions as decision (decision.seq)}
						{@const result = outcome[decision.outcome]}
						<tr class="click" onclick={() => onselect(decision.initiative_id)}>
							<td><div class="cell"><span class="mono">{decision.initiative_id}</span>{initiativeName(decision.initiative_id)}</div></td>
							<td><span class="state" data-tone={result.tone}>{result.word}</span></td>
							<td>{#each decision.rule_ids as id, at (id + at)}<span class="rule-name">{ruleName(id) ?? id}</span>{#if ruleName(id) === null}<span class="hint"> recorded by a newer daemon</span>{/if}{' '}{/each}<span class="mono muted">{when(decision.at)}</span></td>
							<td class="muted">{decision.reason || 'no reason was recorded'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
</section>

<style>
	.registers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 32px; padding: 20px 24px 40px; align-content: start; }
	.tbl td { vertical-align: top; }
	@media (max-width: 1279px) { .registers { grid-template-columns: 1fr; } }
</style>
