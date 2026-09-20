<script lang="ts">
	import AsyncField from './AsyncField.svelte';
	import {
		BURN_SEGMENT_NAME,
		ENFORCEMENT,
		SELECTION_FOOT,
		anomalyCount,
		budgetReading,
		burnSegments,
		estimateOnly,
		etaReading,
		joinedPhases,
		ratioReading
	} from './burn';
	import { BURN_SEGMENT_WEIGHT } from './burn';
	import { tokens } from './bank';
	import type { Resource } from './resource.svelte';
	import type { StatusBundle, TokenLedger } from './daemon';

	let {
		status,
		ledger,
		planCap
	}: {
		/** The fifth read: the whole observability bundle in one request. */
		status: Resource<StatusBundle>;
		/** The ledger read; its failure never blanks the plate. */
		ledger: Resource<TokenLedger>;
		/** The fold's declared plan cap, from the plan read the page already holds. */
		planCap: number | null;
	} = $props();

	/** Where a count behind the productive figure came from, in operator words. */
	function sourcesOf(ledgerData: TokenLedger): string {
		const names: Record<string, string> = {
			harness: 'the harness',
			provider: 'the provider',
			gateway: 'the gateway',
			tokenizer: 'the tokenizer',
			estimate: 'labelled estimates'
		};
		const kinds = new Set(
			(ledgerData.totals.provenance.productive ?? []).map((row) => row.split(':', 1)[0])
		);
		const named = [...kinds].map((kind) => names[kind] ?? kind);
		if (named.length === 0) return '';
		if (named.length === 1) return `counted by ${named[0]}`;
		return `counted by ${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}`;
	}

</script>

<!--
  Run's burn instruments: one readout plate under the structural state bar,
  with the member lists rendered beside the field they select. Every cell's
  gloss is where its provenance lives, and every honest absence gets its own
  sentence — never a zero, never a ratio of nothing.
-->
<AsyncField resource={status} reading="the burn instruments" onretry={() => void status.load()}>
	{#snippet children(bundle: StatusBundle)}
		{@const burn = bundle.burn_down}
		{@const anomalies = bundle.anomalies}
		{@const ledgerData = ledger.data}
		{@const ratio = ratioReading(bundle.overhead)}
		{@const budget = budgetReading(planCap, burn.remaining_plan_cap)}
		{@const segments = budget.drawMember
			? burnSegments(burn.productive_tokens, burn.orchestration_tokens, planCap)
			: null}
		{@const etaRead = etaReading(bundle.eta)}
		{@const phases =
			ledgerData == null
				? null
				: joinedPhases(
						(['actual', 'preflight', 'estimate'] as const).filter(
							(phase) => ledgerData.totals[phase] > 0
						)
					)}

		<p class="label rule-label">
			<span>Burn</span><span class="rule"></span>
			<span class="phase">{phases ?? 'unread'}</span>
		</p>

		<dl class="readout plate">
			<div>
				<dt class="label">Accounted</dt>
				<dd
					class="value member"
					data-state={phases === null ? 'slack' : phases.includes('measured') ? 'seated' : 'balanced'}
				>
					{tokens(burn.accounted_tokens)}
				</dd>
				<p class="gloss">
					{#if ledgerData == null}
						unread — the ledger did not answer
					{:else if estimateOnly(ledgerData.totals)}
						{phases}; every figure here is an estimate Herdsman made, not a count any
						harness or provider reported
					{:else if phases}
						{phases} counts, selected per piece of work
					{/if}
				</p>
			</div>
			<div>
				<dt class="label">Productive / orchestration</dt>
				<dd class="value">
					{tokens(burn.productive_tokens)} / {tokens(burn.orchestration_tokens)}
				</dd>
				<p class="gloss">
					{#if ledgerData == null}
						unread — the ledger did not answer
					{:else if burn.productive_tokens === 0}
						nothing has been measured as productive yet, so the split carries no ratio
					{:else}
						{sourcesOf(ledgerData)}; the target's denominator, never an estimate
					{/if}
				</p>
			</div>
			<div>
				<dt class="label">Overhead</dt>
				<dd class="value member" data-state={ratio.state}>{ratio.value ?? '—'}</dd>
				<p class="gloss">{ratio.gloss}</p>
			</div>
			<div>
				<dt class="label">Budget</dt>
				<dd
					class="value member"
					data-state={budget.value === null
						? 'slack'
						: anomalies.some((a) => a.code === 'exhausted-budget')
							? 'failed'
							: 'seated'}
				>
					{budget.value ?? '—'}
				</dd>
				<p class="gloss">
					{#if budget.value === null}
						{budget.gloss}
					{:else}
						the declared ceiling, enforced when an attempt starts
					{/if}
				</p>
			</div>
			<div>
				<dt class="label">Finishes</dt>
				<dd class="value member" data-state={etaRead.state}>{etaRead.value ?? '—'}</dd>
				<p class="gloss">{etaRead.gloss}</p>
			</div>
			<div>
				<dt class="label">Anomalies</dt>
				<dd class="value member" data-state={anomalyCount(anomalies) === 0 ? 'seated' : 'failed'}>
					{anomalyCount(anomalies)}
				</dd>
				<p class="gloss">
					{#if anomalyCount(anomalies) === 0}
						the ledger found nothing to flag
					{:else}
						deterministic burn findings, listed below
					{/if}
				</p>
			</div>
			<div class="wide">
				<dt class="label">Against the cap</dt>
				<dd>
					{#if segments === null}
						<span class="member" data-state="slack">{budget.gloss}</span>
					{:else if segments.length === 0}
						Nothing has been spent against this ceiling yet — it is whole, not unlimited.
					{:else}
						<span
							class="member-line"
							role="img"
							aria-label="burn against the declared cap: {BURN_SEGMENT_NAME.productive}, {BURN_SEGMENT_NAME.orchestration}, {BURN_SEGMENT_NAME.headroom}"
						>
							{#each segments as segment (segment.kind)}
								<span
									class="seg"
									data-kind={segment.kind}
									data-weight={BURN_SEGMENT_WEIGHT[segment.kind]}
									style="flex-grow: {segment.share}"
								></span>
							{/each}
							<span class="tail" aria-hidden="true"></span>
						</span>
					{/if}
				</dd>
			</div>
			<div class="wide notes">
				<dt class="label">Notes</dt>
				<dd>
					<p class="gloss">
						{#if planCap !== null}{ENFORCEMENT}{:else}{budget.gloss}{/if}
					</p>
					{#if ratio.absence}<p class="prose quiet absence">{ratio.absence}</p>{/if}
					{#if etaRead.absence}<p class="prose quiet absence">{etaRead.absence}</p>{/if}
					<p class="gloss">{SELECTION_FOOT}</p>
				</dd>
			</div>
		</dl>
	{/snippet}
</AsyncField>

<style>
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.phase {
		font-variant-numeric: tabular-nums;
	}

	/* The same readout block R1's plate uses — one system, two readings. */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		flex: 1 1 11rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.75rem 1rem;
	}
	.readout .wide {
		flex-basis: 100%;
	}
	/* Six instruments stay one band on the desktop sheet; the existing
	   11rem basis otherwise wraps the sixth at the sheet's 68.5rem content
	   width. Narrow sheets keep the readable three-column wrap below 60rem. */
	@media (min-width: 60rem) {
		.readout > div:not(.wide) {
			flex-basis: calc((100% - 5px) / 6);
		}
	}
	dt {
		margin-bottom: 0.25rem;
	}
	@media (min-width: 60rem) {
		.readout > div:not(.wide) dt {
			min-height: 2.25rem;
		}
	}
	dd {
		margin: 0;
		color: var(--member-ink, var(--ink));
		font-variant-numeric: tabular-nums;
	}
	/* A provenance sentence is the shared contract's requirement, not
	   decoration: R2's finish review caught a gloss hidden at narrow width
	   deleting it. This plate's glosses stay visible at every width — a
	   deliberate, stated narrowing of the shared rule for one condition. */
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	/* Ash is a graphics value: it draws slack and never sets text. */
	.member[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}

	/* The burn member: the Load Bank's vocabulary against the declared cap. */
	.member-line {
		display: flex;
		align-items: center;
		width: 100%;
	}
	.member-line .seg {
		flex-basis: 0;
		min-width: 2px;
	}
	.member-line .seg[data-kind='productive'] {
		height: 2.5px;
		background: var(--seat);
	}
	.member-line .seg[data-kind='orchestration'] {
		height: 2.5px;
		background: var(--red);
	}
	/* Headroom: ash, dashed 3/3, at the hairline weight. Ash draws here and
	   never sets type. */
	.member-line .seg[data-kind='headroom'] {
		height: 1px;
		background: repeating-linear-gradient(
			90deg,
			var(--ash) 0 3px,
			transparent 3px 6px
		);
	}
	.member-line .tail {
		flex: none;
		width: 0.75rem;
		height: 1px;
		background: var(--member-line);
	}

	.prose.quiet {
		font-size: 0.8125rem;
		color: var(--ink-2);
	}
	.readout > .notes {
		padding-block: 0.25rem;
	}
	.notes dt {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
		border: 0;
	}
	.notes .gloss {
		max-width: none;
		margin: 0.35rem 0 0;
	}
	.notes .gloss:first-child {
		margin-top: 0;
	}
	.notes .absence {
		margin: 0.35rem 0 0;
	}

</style>
