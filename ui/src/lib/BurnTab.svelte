<script lang="ts">
	/*
	  Run → Burn (DS §9.x, views.md §2.2): the budget meter against the declared cap,
	  per-member bars in harness hue, and one provenance sentence. Merges the old
	  plate, lists and attribution; the pure readings stay in `burn.ts`.
	*/
	import AsyncField from './AsyncField.svelte';
	import Button from './Button.svelte';
	import {
		BURN_SEGMENT_NAME,
		ESTIMATE_ONLY,
		NO_CAP,
		SELECTION_FOOT,
		budgetReading,
		burnSegments,
		categoryString,
		ceilingsOf,
		estimateOnly,
		groupAnomalies,
		joinedPhases,
		ratioReading
	} from './burn';
	import { tokens } from './bank';
	import { harnessHue } from './marks';
	import Mark from './Mark.svelte';
	import type { Resource } from './resource.svelte';
	import type { StatusBundle, TokenLedger, TokenMeasurement } from './daemon';

	let {
		status,
		ledger,
		planCap,
		members,
		selected,
		onselect
	}: {
		status: Resource<StatusBundle>;
		ledger: Resource<TokenLedger>;
		/** The fold's declared plan cap. */
		planCap: number | null;
		/** Field order; carries the harness (line hue) and model (mark) per member. */
		members: { id: string; name: string; harness: string; model: string }[];
		selected: string | null;
		onselect: (id: string) => void;
	} = $props();

	const N = 180; // one hairline per share of the meter
	const lines = (share: number) => Math.round(share * N);

	/* Per member: one value per piece of work at the highest phase, never a sum of alternatives. */
	const RANK = { actual: 3, preflight: 2, estimate: 1 } as const;
	function perMember(entries: TokenMeasurement[]): Map<string, number> {
		const best = new Map<string, TokenMeasurement>();
		for (const e of entries) {
			if (!e.initiative_id) continue;
			const key = `${e.initiative_id}|${e.semantic_work_id ?? e.entry_id}`;
			const held = best.get(key);
			if (!held || RANK[e.phase] > RANK[held.phase]) best.set(key, e);
		}
		const out = new Map<string, number>();
		for (const e of best.values()) out.set(e.initiative_id!, (out.get(e.initiative_id!) ?? 0) + e.input_tokens + e.output_tokens);
		return out;
	}
</script>

<AsyncField resource={status} reading="the burn instruments" onretry={() => void status.load()}>
	{#snippet children(bundle: StatusBundle)}
		{@const burn = bundle.burn_down}
		{@const data = ledger.data}
		{@const budget = budgetReading(planCap, burn.remaining_plan_cap)}
		{@const segments = burnSegments(burn.productive_tokens, burn.orchestration_tokens, planCap)}
		{@const ratio = ratioReading(bundle.overhead)}
		{@const spent = data ? perMember(data.entries) : null}
		{@const peak = spent ? Math.max(1, ...spent.values()) : 1}
		{@const phases = data ? joinedPhases((['actual', 'preflight', 'estimate'] as const).filter((p) => data.totals[p] > 0)) : null}
		{@const headroom = planCap === null ? null : Math.max(0, planCap - burn.productive_tokens - burn.orchestration_tokens)}
		{@const ceilings = ceilingsOf(burn.remaining_initiative_caps)}
		{@const findings = groupAnomalies(bundle.anomalies)}

		<div class="burn pane-in">
			<section aria-label="Budget">
				<div class="rule-h">
					<span class="lbl">Budget · {tokens(burn.accounted_tokens)} accounted{planCap !== null ? ` of ${tokens(planCap)} declared` : ' · no cap declared'}</span>
					<span class="r lbl">{phases ?? 'unread'}</span>
				</div>
				<div
					class="meter"
					role="img"
					aria-label="Tokens against the declared cap: {BURN_SEGMENT_NAME.productive} {tokens(burn.productive_tokens)}, {BURN_SEGMENT_NAME.orchestration} {tokens(burn.orchestration_tokens)}{headroom !== null ? `, ${BURN_SEGMENT_NAME.headroom} ${tokens(headroom)}` : ''}"
				>
					{#if segments === null}
						{@const total = burn.productive_tokens + burn.orchestration_tokens}
						{#each { length: total > 0 ? lines(burn.productive_tokens / total) : 0 } as _, i (`p${i}`)}<i style:background="var(--tx)"></i>{/each}
						{#each { length: total > 0 ? lines(burn.orchestration_tokens / total) : 0 } as _, i (`o${i}`)}<i style:background="var(--l615)"></i>{/each}
					{:else}
						{#each segments as seg (seg.kind)}
							{#each { length: lines(seg.share) } as _, i (`${seg.kind}${i}`)}<i style:background={seg.kind === 'productive' ? 'var(--tx)' : seg.kind === 'orchestration' ? 'var(--l615)' : 'var(--ln2)'}></i>{/each}
						{/each}
					{/if}
				</div>
				<div class="legend mono muted">
					<span><b style="color: var(--tx)">{tokens(burn.productive_tokens)}</b> productive</span>
					<span><b style="color: var(--l615)">{tokens(burn.orchestration_tokens)}</b> orchestration</span>
					<span>{headroom === null ? 'headroom —' : `${tokens(headroom)} headroom`}</span>
					<span>overhead {ratio.value ?? '—'}</span>
				</div>
				<p class="prose prov">
					{#if data == null}
						The ledger has not answered{ledger.phase === 'error' ? `: ${ledger.error?.message ?? 'unknown failure'}` : ''}, so the provenance of these figures is unread.
						{#if ledger.phase === 'error'}<Button icon="rotate-ccw" small onclick={() => void ledger.load()}>Read again</Button>{/if}
					{:else}
						{data.accounted_derivation}. {estimateOnly(data.totals) ? ESTIMATE_ONLY : SELECTION_FOOT}
						{categoryString(data.by_category) ? `By category: ${categoryString(data.by_category)}.` : ''}
					{/if}
					{#if planCap === null}{NO_CAP}{/if}
				</p>
			</section>

			<section aria-label="By member">
				<div class="rule-h"><span class="lbl">By member</span><span class="r count">{spent ? spent.size : '—'}</span></div>
				{#if spent === null}
					<p class="muted">Per-member spend is unread: the ledger did not answer.</p>
				{:else if spent.size === 0}
					<p class="muted">No member has a measured or estimated cost yet.</p>
				{:else}
					<ul class="bars">
						{#each members.filter((m) => spent.has(m.id)) as m (m.id)}
							{@const v = spent.get(m.id) ?? 0}
							<li>
								<button type="button" class="row" class:on={selected === m.id} aria-current={selected === m.id ? 'true' : undefined} onclick={() => onselect(m.id)}>
									<span class="mono id">{m.id}</span>
									<span class="ln"><i style:background={harnessHue(m.harness)} style:width="{Math.max(1, (v / peak) * 100)}%"></i></span>
									<Mark model={m.model} size={14} />
									<span class="mono val">{tokens(v)}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			{#if findings.length > 0 || ceilings.rows.length > 0}
				<section class="wide" aria-label="Findings">
					<div class="rule-h"><span class="lbl">Findings</span><span class="r count">{findings.length + ceilings.rows.length}</span></div>
					<table class="tbl">
						<tbody>
							{#each findings as g (g.code)}
								<tr>
									<td class="kind"><span class="state" data-tone={g.code === 'missing-usage' ? 'waiting' : 'failed'}>{g.label} · {g.count}</span></td>
									<td class="prose">{g.text}</td>
									<td class="ids"><div>
										{#each g.ids as id (id)}<button type="button" class="chip" onclick={() => onselect(id)}>{id}</button>{/each}
										{#if g.plan}<span class="chip">plan cap</span>{/if}
									</div></td>
								</tr>
							{/each}
							{#each ceilings.rows as c (c.id)}
								<tr>
									<td class="kind"><span class="state" data-tone={c.state === 'failed' ? 'failed' : 'settled'}>Ceiling</span></td>
									<td class="prose">{c.value}</td>
									<td class="ids"><div><button type="button" class="chip" onclick={() => onselect(c.id)}>{c.id}</button></div></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</section>
			{/if}
		</div>
	{/snippet}
</AsyncField>

<style>
	.burn { display: grid; grid-template-columns: 1.2fr 1fr; gap: 40px; padding: 24px; align-content: start; }
	.wide { grid-column: 1 / -1; }
	.meter { display: flex; gap: 2px; height: 28px; align-items: flex-end; margin: 10px 0 8px; overflow: hidden; }
	.meter :global(i) { width: 1px; height: 100%; flex: none; }
	.legend { display: flex; flex-wrap: wrap; gap: 6px 22px; }
	.prov { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
	.bars { list-style: none; display: grid; }
	.row {
		display: grid; grid-template-columns: 38px minmax(0, 1fr) 14px 60px; gap: 10px; align-items: center; width: 100%;
		padding: 5px 4px; text-align: left; background: none; border: 0; cursor: pointer; transition: background var(--t-fast);
	}
	.row:hover, .row.on { background: var(--p2); }
	.ln { display: block; height: 2px; }
	.ln i { display: block; height: 2px; }
	.val { text-align: right; }
	.tbl td { vertical-align: top; }
	.kind { white-space: nowrap; width: 1%; }
	.ids div { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
	.chip { background: none; cursor: pointer; }
	.chip:hover { color: var(--tx); border-color: var(--dim); }
	@media (max-width: 1023px) { .burn { grid-template-columns: minmax(0, 1fr); } }
</style>
