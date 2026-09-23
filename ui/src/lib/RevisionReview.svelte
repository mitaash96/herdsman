<script lang="ts">
	import type { CheckpointReport, Plan, PlanGraph, RecalibrationReport } from './daemon';
	import { bandsOf, downstreamSentence, formatCap, type RevisionBandView } from './revision';
	import type { Resource } from './resource.svelte';

	let { report, graph, plan, reviews, onselect }: {
		report: RecalibrationReport;
		graph: PlanGraph;
		plan: Resource<Plan> | null;
		reviews: Resource<CheckpointReport> | null;
		onselect: (id: string) => void;
	} = $props();

	let opened = $state<Record<string, boolean>>({});
	const bands = $derived(bandsOf(report, graph, plan?.data ?? null, reviews?.data ?? null));
	const revision = $derived(report.revision);
	const impact = $derived(report.impact);

	function toggle(band: RevisionBandView) {
		opened = { ...opened, [band.key]: !(opened[band.key] ?? band.open) };
	}
</script>

<section class="revision-review" aria-labelledby="gate-revision-title">
	<p class="label rule-label" id="gate-revision-title" tabindex="-1">
		<span>Revision</span><span class="rule"></span><span>v{revision.from_version} → v{revision.to_version}</span>
	</p>
	<dl class="readout counts">
		{#each ['unchanged', 'edited', 'split', 'merged', 'new', 'removed'] as kind}
			<div><dt class="label">{kind}</dt><dd class="value">{revision.counts[kind as keyof typeof revision.counts] ?? 0}</dd></div>
		{/each}
	</dl>
	<p class="gloss count-note">Every category is counted, so a zero here means none — not unread.</p>
	<p class="prose quiet">{revision.derivation}</p>
	<p class="prose quiet revision-note">Unchanged means <strong>same brief, same scope, same claims, same gates</strong>; it does not mean nothing about the node moved. The old graph is not served, so this register shows only the facts the daemon carries.</p>

	{#each bands as band (band.key)}
		<section class="band" data-band={band.key}>
			<div class="band-head">
				<button class="disclosure" type="button" aria-expanded={opened[band.key] ?? band.open} onclick={() => toggle(band)}>
					<span class="label">{band.label}</span><span class="rule"></span><span aria-hidden="true">{(opened[band.key] ?? band.open) ? '−' : '+'}</span>
				</button>
			</div>
			{#if opened[band.key] ?? band.open}
				{#if band.rows.length > 0}
					<ul class="entries">
						{#each band.rows as row (row.node.old_ids.join(',') + ':' + row.node.new_ids.join(','))}
							<li class="entry revision-row member" data-state={row.state}>
								{#if row.selectableId}
									<button class="row-button" type="button" onclick={() => onselect(row.selectableId!)}>
										<span class="entry-head"><span class="mark">{row.node.change}</span><span class="who">{row.name ?? row.selectableId}</span><span class="cardinality">{row.cardinality ?? ''}</span></span>
										<span class="lineage">{row.lineage}</span>
									</button>
								{:else}
									<div class="row-button static"><span class="entry-head"><span class="mark">{row.node.change}</span><span class="who">{row.name ?? row.lineage}</span><span class="cardinality">{row.cardinality ?? ''}</span></span><span class="lineage">{row.lineage}</span></div>
								{/if}
								{#if row.markers.length}<p class="entry-meta markers">{#each row.markers as marker, index}<span class="chip">{marker}</span>{index < row.markers.length - 1 ? ' ' : ''}{/each}</p>{/if}
								{#if row.attempts}<p class="entry-meta">{row.attempts}</p>{/if}
								{#if row.cap}<p class="entry-meta">{row.cap}</p>{/if}
								{#if row.fixedReasons.length}<p class="entry-meta">{row.fixedReasons.join(' ')}</p>{/if}
								<p class="entry-brief">{row.message}</p>
							</li>
						{/each}
					</ul>
				{/if}
				<p class="prose quiet foot">{band.foot}</p>
			{/if}
		</section>
	{/each}

	{#if revision.ambiguous.length}
		<p class="label rule-label sub"><span>Ambiguous</span><span class="rule"></span><span>{revision.ambiguous.length}</span></p>
		<p class="prose quiet">{revision.ambiguous.length} digest{revision.ambiguous.length === 1 ? '' : 's'} could not be reconciled. The nodes they name remain honest new plus removed rather than a guessed split: {#each revision.ambiguous as digest, index}<abbr title={digest} aria-label={digest}>{digest.slice(0, 8)}</abbr>{index < revision.ambiguous.length - 1 ? ', ' : ''}{/each}.</p>
	{/if}

	<section class="downstream">
		<p class="label rule-label"><span>Downstream</span><span class="rule"></span><span>{impact.downstream.length ? `${impact.downstream.length} in build order` : 'nothing below'}</span></p>
		<p class="prose quiet">{downstreamSentence(impact)} These nodes are not themselves revised; they inherit what changed above them.</p>
		{#if impact.stranded.length}
			<p class="lead member" data-state="failed" role="alert">Recorded work is missing.</p>
			<p class="prose">{impact.stranded.join(', ')} recorded attempts no longer exist under any id this plan carries. A revision the fold accepts can never do this, so this is a fault in the record rather than a consequence of the revision. Do not approve on this reading.</p>
		{/if}
		{#if impact.dropped.length}<p class="prose quiet dropped">{impact.dropped.join(', ')} left the live plan, including every source of a merge. Their attempts and checkpoints move to the plan's retired record — they are not deleted, and their old ids can never be given to anything else. A renumbered node is not dropped: it carried its record to its new id.</p>{/if}
		{#if impact.downstream.length}
			<ol class="impact-list">{#each impact.downstream as node (node.initiative_id)}<li><button type="button" class="pick-id" onclick={() => onselect(node.initiative_id)}>{node.initiative_id}</button> · {node.state} · {node.attempts ? `${node.attempts} attempts` : 'no attempts'}</li>{/each}</ol>
		{/if}
		<p class="label rule-label sub"><span>Allowances</span><span class="rule"></span><span>{impact.allowance_resets.length ? `${impact.allowance_resets.length} fresh` : 'none'}</span></p>
		{#if impact.allowance_resets.length}{#each impact.allowance_resets as allowance (allowance.initiative_id)}<p class="prose allowance"><strong>{allowance.initiative_id}</strong> — {allowance.source_status === 'proven' ? `fresh allowance. ${allowance.source_ids.join(', ')} spent ${allowance.consumed_attempts ?? 0} attempt${allowance.consumed_attempts === 1 ? '' : 's'} before this revision.` : allowance.source_status === 'new' ? 'a new allocation, not a reset: no surviving node dropped any of its claims.' : `${allowance.source_status === 'candidates' ? 'fresh allowance. One of' : 'fresh allowance. It mixes claims from'} ${allowance.candidate_source_ids.join(', ')}; this build will not name which source.`}</p>{/each}{:else}<p class="prose quiet">No node gets a fresh attempt allowance in this revision.</p>{/if}
	</section>
</section>

<style>
	.revision-review { display: block; }
	abbr { text-decoration:underline dotted var(--rule-strong); text-underline-offset:.2rem; }
	.rule-label { display:flex; align-items:baseline; gap:.6rem; margin:0 0 .9rem; }
	.rule { flex:1; height:1px; background:var(--rule); }
	.readout { display:flex; flex-wrap:wrap; gap:1px; background:var(--rule); border:1px solid var(--rule); }
	.readout > div { flex:1 1 6rem; min-width:4.5rem; background:var(--plate); padding:.6rem .7rem; }
	.readout dt { margin-bottom:.2rem; } .readout dd { margin:0; color:var(--member-ink,var(--ink)); }
	.gloss { margin:.35rem 0 0; font-size:.625rem; letter-spacing:.04em; color:var(--ink-2); }
	.count-note { margin-top:.4rem; }
	.revision-note { max-width:68ch; margin-top:.8rem; }
	.band { margin-top:1.5rem; }
	.band-head { margin-bottom:.55rem; }
	.disclosure { display:flex; align-items:baseline; gap:.6rem; width:100%; padding:0; border:0; background:none; color:var(--ink); text-align:left; font:inherit; cursor:pointer; }
	.disclosure:hover { color:var(--red); }
	.disclosure .rule { flex:1; }
	.entries { list-style:none; padding:0; margin:0; }
	.revision-row { border-top:1px solid var(--rule); padding:.7rem 0 .75rem; }
	.row-button { display:block; width:100%; padding:0; border:0; background:none; color:inherit; text-align:left; font:inherit; cursor:pointer; }
	.row-button:hover .who { color:var(--red); }
	.row-button.static { cursor:default; }
	.entry-head { display:flex; align-items:baseline; gap:.55rem; min-width:0; }
	.mark, .cardinality, .lineage, .entry-meta { font-size:.625rem; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-2); }
	.mark { flex:none; } .who { min-width:0; overflow-wrap:anywhere; font-weight:600; color:var(--ink); }
	.cardinality { margin-left:auto; flex:none; }
	.lineage { margin-top:.25rem; overflow-wrap:anywhere; }
	.entry-meta { margin:.35rem 0 0; text-transform:none; letter-spacing:.02em; }
	.markers { display:flex; flex-wrap:wrap; gap:.35rem; }
	.chip { border:1px solid var(--rule-strong); padding:.08rem .25rem; font-size:.56rem; letter-spacing:.08em; }
	.entry-brief { margin:.35rem 0 0; max-width:68ch; color:var(--ink-2); font-size:.75rem; line-height:1.45; }
	.member[data-state='failed'] .mark, .member[data-state='failed'] .who { color:var(--red); }
	.member[data-state='slack'] .who { color:var(--ink-2); text-decoration:underline dashed var(--rule-strong); text-underline-offset:.2rem; }
	.foot { margin:.55rem 0 0; }
	.sub { margin-top:1.1rem; }
	.impact-list { margin:.6rem 0 0 1.25rem; padding:0; }
	.impact-list li { padding:.35rem 0; border-top:1px solid var(--rule); }
	.pick-id { border:0; padding:0; background:none; color:var(--ink); font:inherit; text-decoration:underline var(--rule-strong); text-underline-offset:.2rem; cursor:pointer; }
	.pick-id:hover { color:var(--red); }
	.lead { margin:.6rem 0 0; font-weight:600; }
	.allowance { border-top:1px solid var(--rule); padding-top:.4rem; }
	@media (max-width: 48rem) { .readout > div { flex-basis:calc(33.333% - 1px); } .entry-head { flex-wrap:wrap; } .cardinality { margin-left:0; } }
</style>
