<script lang="ts">
	import type { Snippet } from 'svelte';
	import MarkdownReader from './MarkdownReader.svelte';
	import Button from './Button.svelte';
	import { ago } from './bank';
	import { count, ISSUE_WORD, STATUS_WORD } from './shelf';
	import {
		MEMORY_STATUSES, conflictCounterparts, filterLeaves, leafClaim, leafProvenance,
		memorySizeReadout, parseEvidence, type LeafRow, type MemoryRead, type MemoryShelfStatus
	} from './memory';
	import type { AssetStatus } from './daemon';

	/*
	  The Memory tab (DS §9.21 list + §9.18 reader). Three parts, one model:
	  `list` is the 340px column of leaves, `reader` is the selected leaf, and
	  `readout` is what the reader shows before one is chosen. Validity stays the
	  daemon's verdict; this only draws it.
	*/
	let { mode = 'list', rows, reads, selected, onselect, status = 'current', query = '',
		planIds = [], budget = null, capabilityError = '', reading = false,
		onretry, actions, conflictAssets = [], conflictError = '' }: {
		mode?: 'list' | 'reader' | 'readout'; rows: LeafRow[]; reads: Record<string, MemoryRead>;
		selected: string | null; onselect: (ref: string | null) => void;
		status?: MemoryShelfStatus; query?: string; planIds?: string[]; budget?: number | null;
		capabilityError?: string; reading?: boolean; onretry: () => void;
		/** The icon bar + head for the selected leaf: (heading, rule label). */
		actions?: Snippet<[string, string]>;
		conflictAssets?: import('./daemon').Asset[]; conflictError?: string;
	} = $props();
	const listed = $derived(filterLeaves(rows, status, query));
	const row = $derived(rows.find((leaf) => leaf.ref === selected));
	const doc = $derived(selected === null ? undefined : reads[selected]);
	// The leaf is validated as a one-ref set, so a retired leaf reports itself as an
	// archived reference; its Retired state already says that, above.
	const ownIssues = $derived(
		(doc?.issues ?? []).filter((issue) => !(issue.code === 'reference-retired' && issue.ref === selected))
	);
	const size = $derived(memorySizeReadout(rows, reads));
	const provenance = $derived(leafProvenance(doc?.asset?.fields ?? {}, planIds));
	const counterparts = $derived(conflictCounterparts(doc?.asset, conflictAssets));
	const claim = $derived(leafClaim(doc?.asset));
	const budgetLine = $derived(budget !== null ? `Memory budget ${count(budget)} tokens.`
		: `Memory budget unknown${capabilityError.includes('memory capability declaration is missing')
			? ' — no memory capability declaration (.herdsman/memory.json)' : capabilityError ? ' — capabilities unread' : ''}.`);
	// Relative time is presentation only; validity remains the daemon's status.
	const now = Date.now();

	/** Status read as a state word + line form (never colour alone). */
	const TONE: Record<AssetStatus, string> = { active: 'settled', stale: 'waiting', conflicted: 'failed', retired: 'idle' };
	const LINE: Record<AssetStatus, string> = { active: 'var(--tx2)', stale: 'var(--dim)', conflicted: 'var(--l656)', retired: 'var(--fnt)' };

	const rove = $derived(listed.some((leaf) => leaf.ref === selected) ? selected : (listed[0]?.ref ?? null));
</script>

{#snippet issueList(items: import('./daemon').LibraryIssue[])}
	{#each items as issue, i (issue.code + issue.ref + i)}
		<div class="stmt find" data-tone={issue.severity === 'error' ? 'failed' : 'waiting'}>
			<span class="lbl">{ISSUE_WORD[issue.code] ?? issue.code}</span>
			<p class="mono ref">{issue.ref}</p>
			<p>{issue.message}</p>
			{#if issue.detail !== ''}<p class="hint">{#if issue.code === 'context-size'}Largest · {/if}{issue.detail}</p>{/if}
		</div>
	{/each}
{/snippet}

{#if mode === 'readout'}
	<div class="pane-in readout" aria-label="Memory readout">
		<div class="tele">
			{#each MEMORY_STATUSES as state (state)}
				<div><span class="lbl">{STATUS_WORD[state]}</span><span class="num">{count(rows.filter((leaf) => leaf.status === state).length)}</span></div>
			{/each}
			<div><span class="lbl">Tokens</span><span class="num">{count(rows.reduce((sum, leaf) => sum + leaf.tokens, 0))}</span></div>
			<div><span class="lbl">Over budget</span><span class="num" class:bad={size.over > 0}>{size.unread > 0 ? '—' : count(size.over)}</span></div>
		</div>
		<p class="hint" title={capabilityError || undefined}>{budgetLine}{size.unread > 0 ? ` ${count(size.unread)} ${size.unread === 1 ? 'leaf' : 'leaves'} unread.` : ''}</p>
	</div>
{:else if mode === 'list'}
	{#if rows.length === 0}
		<p class="empty-line">No memory leaves in this project yet.</p>
	{:else if listed.length === 0}
		<p class="empty-line">No leaf matches this filter. {count(rows.length)} on disk.</p>
	{:else}
		<ul class="rail" aria-label="Memory leaves">
			{#each listed as leaf (leaf.ref)}
				<li><button type="button" class="asset" data-asset={leaf.ref} style="--c:{LINE[leaf.status]}" aria-current={selected === leaf.ref ? 'true' : undefined}
					tabindex={rove === leaf.ref ? 0 : -1} onclick={() => onselect(leaf.ref)}>
					<i class="em" class:dash={leaf.status === 'stale' || leaf.status === 'retired'}></i>
					<span class="body"><span class="nm ellipsis">{leaf.subject || leaf.ref}</span>
						<span class="sub ellipsis">{leaf.ref} · {count(leaf.tokens)} tok{#if reads[leaf.ref]?.issues?.some((issue) => issue.code === 'context-size')} · over budget{/if}</span></span>
					<span class="state" data-tone={TONE[leaf.status]}>{STATUS_WORD[leaf.status]}</span>
				</button></li>
			{/each}
		</ul>
	{/if}
{:else if selected !== null}
	{#if !row}
		<p class="empty-line" role="status"><span class="mono">{selected}</span> is not on the memory shelf this read returned.</p>
	{:else}
		<article class="leaf" aria-label="Selected memory leaf">
			{#if actions && doc?.asset}
				{@render actions(claim ?? row.subject, `Memory · ${row.ref.replace('memory-leaf/', '')}`)}
			{:else}
				<div class="head"><div class="t"><span class="lbl">Memory · {row.ref.replace('memory-leaf/', '')}</span><h2 class="h-sec">{claim ?? row.subject}</h2></div></div>
			{/if}
			<div class="scroll">
				<div class="front">
					<div><span class="lbl">Status</span><span class="state" data-tone={TONE[row.status]}>{STATUS_WORD[row.status]}</span></div>
					<div><span class="lbl">Ref</span><span class="mono">{row.ref}</span></div>
					<div><span class="lbl">Tokens</span><span class="mono">{count(row.tokens)}</span></div>
					{#each provenance as field (field.key)}
						<div><span class="lbl">{field.label}</span>{#if field.href}<a class="mono" href={field.href}>{field.value}</a>{:else if field.key === 'at'}<span class="mono" title={field.value}>{ago(field.value, now)}</span>{:else}<span class="mono">{field.value}</span>{/if}</div>
					{/each}
				</div>
				{#if row.status === 'stale' || row.status === 'conflicted'}<p class="hint pad">Not distributed to agents while {row.status}.</p>{/if}
				{#if claim === null && !reading && doc?.asset}<p class="hint pad">The daemon returned the subject, but no claim field.</p>{/if}
				{#if !doc || (reading && !doc.asset && !doc.error)}
					<div class="pad" aria-busy="true"><span class="lbl">Reading the leaf and its findings…</span></div>
				{:else}
					{#if doc.asset}
						<MarkdownReader source={doc.asset.body} empty="This leaf has no diagnosis body." />
					{:else}
						<div class="pad stmt" data-tone="failed" role="alert"><b>Leaf unread</b><p class="mono">{doc.error}</p><div class="btnrow"><Button icon="rotate-ccw" small onclick={onretry}>Read again</Button></div></div>
					{/if}
					<div class="pad">
						<div class="rule-h"><span class="lbl">Evidence</span></div>
						{#if doc.asset && doc.asset.references.length > 0}
							<ul class="evidence">{#each doc.asset.references as evidence, i (i)}{@const ref = parseEvidence(evidence)}<li><!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll long evidence paths.) -->
								<div class="evidence-ref" tabindex="0" role="region" aria-label="Evidence path and hash; scroll horizontally"><code>{ref.path}</code>{#if ref.hash}<span class="mono hint" title={ref.hash}> @{ref.shortHash}</span>{/if}</div></li>{/each}</ul>
						{:else if doc.asset}<p class="hint">The daemon returned no evidence references.</p>{/if}
						{#if doc.issues === null}
							<div class="stmt" data-tone="failed" role="alert"><b>Findings unread</b><p class="mono">{doc.issuesError}</p><div class="btnrow"><Button icon="rotate-ccw" small onclick={onretry}>Read again</Button></div></div>
						{:else if ownIssues.length > 0}
							<div class="rule-h gap"><span class="lbl">Findings</span></div>{@render issueList(ownIssues)}
						{/if}
						{#if row.status === 'conflicted' && conflictError}<div class="stmt" data-tone="failed" role="alert"><b>Counterparts unread</b><p class="mono">{conflictError}</p></div>{/if}
						{#if counterparts.length > 0}
							<div class="rule-h gap"><span class="lbl">Conflicts with</span></div>
							<ul class="cp">{#each counterparts as ref (ref)}<li><button type="button" class="bare mono" onclick={() => onselect(ref)}>{ref}</button>{#if leafClaim(conflictAssets.find((leaf) => leaf.ref === ref))}<p class="hint">{leafClaim(conflictAssets.find((leaf) => leaf.ref === ref))}</p>{/if}</li>{/each}</ul>
						{/if}
					</div>
				{/if}
			</div>
		</article>
	{/if}
{/if}

<style>
	.rail { list-style: none; margin: 0; padding: 0; }
	.asset { display: grid; grid-template-columns: 2px minmax(0, 1fr) auto; gap: 0 14px; align-items: center; width: 100%; text-align: left; padding: 9px 20px 9px 24px; cursor: pointer; transition: background var(--t-fast); }
	.asset:hover { background: var(--p1); }
	.asset[aria-current] { background: var(--p2); }
	.asset[aria-current] .em { box-shadow: 3px 0 0 var(--c); }
	.em { height: 30px; background: var(--c); }
	.em.dash { background: repeating-linear-gradient(var(--c) 0 4px, transparent 4px 7px); }
	.body { display: grid; gap: 2px; min-width: 0; }
	.nm { font: 500 14px var(--f-ui); color: var(--tx); }
	.sub { font: 400 11.5px var(--f-mono); color: var(--dim); }
	.leaf { display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; height: 100%; }
	.head { padding: 16px 28px 12px; }
	.t { display: grid; gap: 4px; }
	.scroll { overflow: auto; min-height: 0; padding-bottom: 40px; }
	.front { display: flex; flex-wrap: wrap; gap: 8px 24px; padding: 14px 28px; border-bottom: 1px solid var(--ln); }
	.front > div { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
	.front a { color: var(--tx); text-decoration: underline dashed var(--ln2); text-underline-offset: 4px; }
	.pad { padding: 14px 28px 0; }
	.readout { padding: 24px; display: grid; gap: 14px; align-content: start; }
	.readout .tele { gap: 0; }
	.readout .tele > div:first-child { border-left: 0; padding-left: 0; }
	.readout .num.bad { color: var(--l656); }
	.evidence { list-style: none; padding: 0; margin: 0 0 8px; }
	.evidence-ref { max-width: 100%; overflow-x: auto; white-space: nowrap; padding: 6px 0; }
	.evidence-ref code { font: 400 12px var(--f-mono); color: var(--tx); }
	.find { margin-bottom: 12px; display: grid; gap: 3px; }
	.find .ref { color: var(--tx); overflow-wrap: anywhere; }
	.gap { margin-top: 20px; }
	.cp { list-style: none; padding: 0; display: grid; gap: 8px; }
	.bare { color: var(--tx); text-align: left; overflow-wrap: anywhere; }
	.bare:hover { text-decoration: underline dashed var(--ln2); text-underline-offset: 4px; }
</style>
