<script lang="ts">
	import AsyncField from './AsyncField.svelte';
	import {
		BURN_SEGMENT_NAME,
		ENFORCEMENT,
		SELECTION_FOOT,
		anomalyCount,
		budgetReading,
		burnSegments,
		categoryString,
		ceilingsOf,
		estimateOnly,
		etaReading,
		groupAnomalies,
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
		planCap,
		selected,
		onselect
	}: {
		/** The fifth read: the whole observability bundle in one request. */
		status: Resource<StatusBundle>;
		/** The ledger read; its failure never blanks the plate. */
		ledger: Resource<TokenLedger>;
		/** The fold's declared plan cap, from the plan read the page already holds. */
		planCap: number | null;
		selected: string | null;
		onselect: (id: string) => void;
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

	function select(id: string): void {
		onselect(id);
		document.getElementById(`row-${id}`)?.focus();
	}
</script>

<!--
  Run's burn instruments: one readout plate under the structural state bar,
  two bare-button lists beneath it, and nothing drawn on the field. Every
  cell's gloss is where its provenance lives, and every honest absence gets
  its own sentence — never a zero, never a ratio of nothing.
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
		{@const ceilings = ceilingsOf(burn.remaining_initiative_caps)}
		{@const groups = groupAnomalies(anomalies)}
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
				<p class="gloss">
					{#if ratio.value === null}
						{ratio.gloss}
					{:else}
						orchestration against measured productive work; the target is 20%
					{/if}
				</p>
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
				<p class="gloss">
					{#if planCap !== null}
						{ENFORCEMENT}
					{:else}
						{budget.gloss}
					{/if}
				</p>
			</div>
			<div class="wide">
				<dt class="label">Attribution</dt>
				<dd>
					{#if ledgerData == null}
						<span class="member" data-state="slack">
							{ledger.phase === 'error'
								? `The ledger did not answer: ${ledger.error?.message ?? 'unknown failure.'}`
								: 'The ledger has not answered yet.'}
						</span>
						{#if ledger.phase === 'error'}
							<button class="act" type="button" onclick={() => void ledger.load()}>
								Read again
							</button>
						{/if}
					{:else}
						<span class="derivation">{ledgerData.accounted_derivation}.</span>
						<span class="categories">
							{categoryString(ledgerData.by_category) || 'every category is at zero'}
						</span>
					{/if}
				</dd>
			</div>
		</dl>

		<p class="prose quiet foot">{SELECTION_FOOT}</p>

		{#if ceilings.rows.length > 0}
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
							onclick={() => select(row.id)}
						>
							<span class="mark">{row.id}</span>
							<span class="member" data-state={row.state}>{row.value}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}

		{#if groups.length > 0}
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
						{#if group.ids.length > 0}
							<p class="bare-ids">
								{#each group.ids as id (id)}
									<button
										class="pick id"
										type="button"
										tabindex={id === selected ? 0 : -1}
										onclick={() => select(id)}
									>
										<span class="mark">{id}</span>
									</button>
								{/each}
							</p>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
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

	.derivation {
		display: block;
		color: var(--ink-2);
	}
	.derivation + .categories {
		display: block;
		margin-top: 0.3rem;
		overflow-wrap: anywhere;
	}

	.foot {
		margin: 0.75rem 0 0;
		max-width: 46rem;
	}
	.prose.quiet {
		font-size: 0.8125rem;
		color: var(--ink-2);
	}
	.list-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 1.5rem 0 0.5rem;
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
	.bare li + li {
		margin-top: 0.25rem;
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
