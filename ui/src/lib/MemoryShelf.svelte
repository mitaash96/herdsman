<script lang="ts">
	import type { Snippet } from 'svelte';
	import Markdown from './Markdown.svelte';
	import { ago } from './bank';
	import { count, STATUS_WORD, statusState } from './shelf';
	import {
		MEMORY_STATUSES, conflictCounterparts, filterLeaves, leafClaim, leafProvenance,
		memorySizeReadout, parseEvidence, type LeafRow, type MemoryRead, type MemoryShelfStatus
	} from './memory';
	let { mode = 'hero', rows, reads, selected, onselect, status = $bindable('current'),
		query = $bindable(''), planIds = [], budget = null, capabilityError = '', reading = false,
		onretry, actions, conflictAssets = [], conflictError = '' }: {
		mode?: 'hero' | 'margin'; rows: LeafRow[]; reads: Record<string, MemoryRead>;
		selected: string | null; onselect: (ref: string | null) => void;
		status?: MemoryShelfStatus; query?: string; planIds?: string[]; budget?: number | null;
		capabilityError?: string; reading?: boolean; onretry: () => void;
		actions?: Snippet;
		conflictAssets?: import('./daemon').Asset[]; conflictError?: string;
	} = $props();
	const listed = $derived(filterLeaves(rows, status, query));
	const row = $derived(rows.find((leaf) => leaf.ref === selected));
	const doc = $derived(selected === null ? undefined : reads[selected]);
	const size = $derived(memorySizeReadout(rows, reads));
	const provenance = $derived(leafProvenance(doc?.asset?.fields ?? {}, planIds));
	const counterparts = $derived(conflictCounterparts(doc?.asset, conflictAssets));
	const claim = $derived(leafClaim(doc?.asset));
	// Relative time is presentation only; validity remains the daemon's status.
	const now = Date.now();
</script>

{#if mode === 'margin'}
	<dl class="plate readout" aria-label="Memory readout">
		{#each MEMORY_STATUSES as state (state)}
			<div class="member" data-state={statusState(state)}><dt class="label">{STATUS_WORD[state]}</dt><dd class="value">{count(rows.filter((leaf) => leaf.status === state).length)}</dd></div>
		{/each}
		<div><dt class="label">Total tokens</dt><dd class="value">{count(rows.reduce((sum, leaf) => sum + leaf.tokens, 0))}</dd></div>
		<div class="member" data-state={size.over > 0 ? 'slack' : 'seated'}><dt class="label">Size warnings</dt><dd class="value">{size.unread > 0 ? '—' : count(size.over)}</dd><p class="gloss">{count(size.over)} {size.over === 1 ? 'leaf' : 'leaves'} over budget{#if size.unread > 0}; {count(size.unread)} unread{/if}</p></div>
	</dl>
	<p class="prose small quiet" title={capabilityError || undefined}>{#if budget !== null}Memory budget: {count(budget)} tokens.{:else}Memory budget unknown{#if capabilityError.includes('memory capability declaration is missing')}{' — '}no memory capability declaration (.herdsman/memory.json){:else if capabilityError}{' — '}capabilities unread{/if}.{/if} Size findings use the Library warning budget.</p>
{:else if selected === null}
	<div class="filters">
		<div class="chips" role="group" aria-label="Memory status">
			{#each MEMORY_STATUSES as state (state)}<button type="button" class="chip" aria-pressed={status === state || (status === 'current' && state !== 'retired')} onclick={() => (status = state)}>{STATUS_WORD[state]}</button>{/each}
			<button type="button" class="chip" aria-pressed={status === 'all'} onclick={() => (status = 'all')}>All</button>
		</div>
		<span class="pick"><label class="label" for="memory-find">Find</label><input id="memory-find" class="plate" type="search" placeholder="subject or ref" bind:value={query} /></span>
	</div>
	{#if rows.length === 0}
		<p class="prose">No memory leaves in this project yet. Leaves are recorded through operator answers, promotion and salvage.</p>
	{:else if listed.length === 0}
		<p class="prose">No memory leaves match this filter. {count(rows.length)} are on disk; widen the filter to reach them.</p>
	{:else}
		<ul class="rail" aria-label="Memory leaves">
			{#each listed as leaf (leaf.ref)}
				<li><button type="button" class="entry member" data-state={statusState(leaf.status)} onclick={() => onselect(leaf.ref)}>
					<span class="entry-name">{leaf.subject}</span>
					<span class="entry-dims"><span>{leaf.ref}</span><span class="state">{STATUS_WORD[leaf.status]}</span><span>{count(leaf.tokens)} tok</span></span>
					{#if reads[leaf.ref]?.issues?.some((issue) => issue.code === 'context-size')}<span class="quiet small">Over the context budget</span>{/if}
				</button></li>
			{/each}
		</ul>
	{/if}
{:else}
	<button type="button" class="plate ghost" onclick={() => onselect(null)}>Memory list</button>
	{#if !row}
		<p class="prose" role="status"><code>{selected}</code> is not on the memory shelf this read returned.</p>
	{:else}
		<article class="doc-block" aria-label="Selected memory leaf">
			<h2 class="doc-title">{claim ?? row.subject}</h2>
			<p class="dims">{row.ref} · <span class="state member" data-state={statusState(row.status)}>{STATUS_WORD[row.status]}</span> · {count(row.tokens)} tokens</p>
			{#if actions && doc?.asset}{@render actions()}{/if}
			{#if claim === null && !reading}<p class="prose quiet small">The daemon returned the subject, but no claim field.</p>{/if}
			{#if provenance.length > 0}<p class="dims provenance">
				{#each provenance as field, i (field.key)}{#if i > 0}{' · '}{/if}<span class="quiet">{field.label}</span>{' '}{#if field.href}<a href={field.href}>{field.value}</a>{:else if field.key === 'at'}<time datetime={field.value} title={field.value}>{new Date(field.value).toLocaleString()}</time> ({ago(field.value, now)}){:else}{field.value}{/if}{/each}
			</p>{/if}
			{#if row.status === 'stale' || row.status === 'conflicted'}<p class="prose quiet">Not distributed to agents while {row.status}.</p>{/if}
			{#if !doc || (reading && !doc.asset && !doc.error)}<p class="prose" aria-busy="true">Reading the leaf and its findings…</p>
			{:else}
				{#if doc.asset}<Markdown source={doc.asset.body} empty="This leaf has no diagnosis body." />{:else}<p class="prose" role="alert">Leaf unread: {doc.error}</p><button type="button" class="plate ghost" onclick={onretry}>Read again</button>{/if}
				<h3 class="rule-label label"><span>Evidence</span><span class="rule"></span></h3>
				{#if doc.asset && doc.asset.references.length > 0}<ul class="evidence">{#each doc.asset.references as evidence, i (i)}{@const ref = parseEvidence(evidence)}<li><!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll long evidence paths.) -->
					<div class="evidence-ref" tabindex="0" role="region" aria-label="Evidence path and hash; scroll horizontally"><code>{ref.path}</code>{#if ref.hash}<span class="hash" title={ref.hash}>@{ref.shortHash}</span>{/if}</div></li>{/each}</ul>
				{:else if doc.asset}<p class="prose quiet">The daemon returned no evidence references.</p>{/if}
				{#if doc.issues === null}<p class="prose" role="alert">Findings unread: {doc.issuesError}</p><button type="button" class="plate ghost" onclick={onretry}>Read again</button>
				{:else if doc.issues.length > 0}<p class="label rule-label"><span>Findings</span><span class="rule"></span></p><ul class="findings">{#each doc.issues as issue, i (i)}<li class="finding member" data-state={issue.severity === 'error' ? 'failed' : 'slack'}><span class="label">{issue.code}</span><span class="finding-ref">{issue.ref}</span><span class="finding-msg">{issue.message}</span>{#if issue.detail !== ''}<span class="finding-detail">{#if issue.code === 'context-size'}<span class="label">Largest</span>{/if}{issue.detail}</span>{/if}</li>{/each}</ul>{/if}
				{#if row.status === 'conflicted' && conflictError}<p class="prose" role="alert">Counterparts unread: {conflictError}</p>{/if}
				{#if counterparts.length > 0}<p class="label rule-label"><span>Conflicts with</span><span class="rule"></span></p><ul class="incoming">{#each counterparts as ref (ref)}<li><button type="button" class="bare" onclick={() => onselect(ref)}>{ref}</button>{#if leafClaim(conflictAssets.find((leaf) => leaf.ref === ref))}<p class="prose quiet small counterpart-claim">{leafClaim(conflictAssets.find((leaf) => leaf.ref === ref))}</p>{/if}</li>{/each}</ul>{/if}
			{/if}
		</article>
	{/if}
{/if}

<style>
	.filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; margin: 0 0 1rem; }
	.chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
	.chip { font: inherit; font-size: 0.625rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); background: transparent; border: 0; border-bottom: 1px solid transparent; padding: 0.2rem 0.5rem 0.25rem; cursor: pointer; }
	.chip:hover, .bare:hover, .entry:hover .entry-name { color: var(--red); }
	.chip[aria-pressed='true'] { color: var(--ink); border-bottom-color: var(--member-line); }
	.pick { display: flex; flex: 1 1 12rem; min-width: 0; flex-direction: column; gap: 0.3rem; }
	input { --cut: 10px; width: 100%; box-sizing: border-box; min-width: 0; font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); padding: 0.45rem 0.7rem; }
	.rail, .incoming { list-style: none; margin: 0; padding: 0; }
	.rail > li { border-bottom: 1px solid var(--rule); }
	.entry { display: flex; flex-direction: column; gap: 0.25rem; width: 100%; font: inherit; text-align: left; color: var(--ink); background: transparent; border: 0; padding: 0.6rem 0; cursor: pointer; }
	.entry-name { font-weight: 500; overflow-wrap: break-word; }
	.entry[data-state='slack'] .entry-name, .entry[data-state='failed'] .entry-name { color: var(--ink-2); }
	.entry-dims { display: flex; flex-wrap: wrap; gap: 0.25rem 0.75rem; font-size: 0.625rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-2); }
	.state { border-bottom: 1px dashed var(--ash); }
	.member[data-state='seated'] .state, .state.member[data-state='seated'] { border-bottom-style: solid; border-bottom-color: var(--member-line); }
	.readout { --cut: 12px; display: flex; flex-wrap: wrap; gap: 1px; margin: 0 0 1rem; background: var(--rule); border: 1px solid var(--rule); }
	.readout > div { flex: 1 1 9rem; min-width: 0; background: var(--plate); padding: 0.75rem 1rem; }
	dt { margin-bottom: 0.25rem; } dd { margin: 0; color: var(--member-ink, var(--ink)); }
	.gloss { margin: 0.3rem 0 0; font-size: 0.625rem; letter-spacing: 0.06em; line-height: 1.5; color: var(--ink-2); }
	.doc-block { min-width: 0; padding-top: 1.5rem; padding-bottom: 2rem; }
	.doc-title { margin: 0 0 0.75rem; font: inherit; font-size: 1.125rem; font-weight: 500; overflow-wrap: break-word; }
	.dims { margin: 0 0 1rem; line-height: 1.6; overflow-wrap: break-word; }
	.provenance { font-size: 0.8125rem; }
	.rule-label { display: flex; align-items: baseline; gap: 0.75rem; margin: 1.75rem 0 0.75rem; font-size: 0.625rem; font-weight: 500; }
	.rule { flex: 1; height: 1px; background: var(--rule); align-self: center; }
	.evidence { list-style: none; padding: 0; margin: 0 0 1rem; }
	.evidence-ref { max-width: 100%; overflow-x: auto; white-space: nowrap; padding: 0.35rem 0; }
	code { background: var(--plate); border: 1px solid var(--rule); padding: 0.05em 0.4em; white-space: nowrap; }
	.hash { font-size: 0.75rem; color: var(--ink-2); }
	.findings { list-style: none; margin: 0; padding: 0; }
	.finding { display: grid; gap: 0.1rem; padding: 0.5rem 0; border-top: 1px solid var(--rule); color: var(--ink-2); }
	.finding .label { color: var(--member-ink); justify-self: start; }
	.finding[data-state='slack'] .label { color: var(--ink-2); border-bottom: 1px dashed var(--ash); }
	.finding-ref { color: var(--ink); overflow-wrap: anywhere; }
	.finding-msg, .finding-detail { overflow-wrap: anywhere; }
	.finding-detail { font-size: 0.75rem; color: var(--ink-2); }
	.finding-detail .label { margin-right: 0.35rem; }
	.quiet { color: var(--ink-2); } .small { font-size: 0.75rem; }
	.incoming { display: flex; flex-wrap: wrap; gap: 0.2rem 1rem; }
	.counterpart-claim { margin: 0.25rem 0 0; }
	.bare { font: inherit; color: var(--ink); background: transparent; border: 0; padding: 0; cursor: pointer; overflow-wrap: anywhere; }
	.ghost { --cut: 9px; font: inherit; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink); background: transparent; border: 1px solid var(--rule-strong); padding: 0.35rem 0.85rem; cursor: pointer; }
	.ghost:hover { color: var(--red); border-color: var(--red); }
</style>
