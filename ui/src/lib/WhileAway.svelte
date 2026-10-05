<script lang="ts">
	/* Home's third tab: what changed while the operator was away. */
	import type { DigestEntry } from './daemon';
	import { grouped, latestOnly, typeCounts, type DigestWindow } from './digest';
	import Button from './Button.svelte';
	import Icon from './Icon.svelte';

	let { entries, since, window, onwindow, onmark, loading, error, firstVisit }: {
		entries: DigestEntry[] | null; since: string; window: DigestWindow;
		onwindow: (next: DigestWindow) => void; onmark: () => void;
		loading: boolean; error: string | null; firstVisit: boolean;
	} = $props();
	let expanded = $state<string[]>([]);
	const groups = $derived(grouped(entries ?? []));
	const long = $derived((entries?.length ?? 0) > 25);
	function toggle(id: string) { expanded = expanded.includes(id) ? expanded.filter((name) => name !== id) : [...expanded, id]; }
</script>

<div class="wa">
	<div class="bar">
		<select class="sel" aria-label="Window" value={window} onchange={(event) => onwindow(event.currentTarget.value as DigestWindow)}>
			<option value="since">Since last visit</option>
			<option value="day">Last 24 h</option>
			<option value="week">Last 7 days</option>
		</select>
		<span class="mono muted">from <time datetime={since}>{new Date(since).toLocaleString()}</time>{#if firstVisit && window === 'since'} · first visit: last 24 h{/if}</span>
		<Button small icon="check-check" onclick={onmark}>Mark as read</Button>
	</div>
	{#if loading && !entries}<p class="empty-line" role="status">Reading changes…</p>{/if}
	{#if error}
		<p class="alert" role="alert"><span class="state" data-tone="failed">Stale</span><span class="mono">{error}</span><span class="muted">Last read kept.</span>
			<Button small icon="rotate-ccw" onclick={() => onwindow(window)}>Read again</Button></p>
	{/if}
	{#if entries && latestOnly(entries)}<p class="hint note" role="status">Showing the latest 200 changes; older ones are not in this view.</p>{/if}
	{#if entries && !entries.length}<p class="empty-line">Nothing changed since {new Date(since).toLocaleString()}.</p>{/if}
	{#each groups as [plan, lines] (plan)}
		<section aria-label={`Changes in ${plan}`}>
			<div class="rule-h">
				<span class="lbl mono-id">{plan}</span>
				{#if long}
					<span class="r"><button type="button" class="lbl more" aria-expanded={expanded.includes(plan)} onclick={() => toggle(plan)}>{typeCounts(lines)} · {expanded.includes(plan) ? 'Collapse' : 'Expand'}</button></span>
				{/if}
			</div>
			{#if !long || expanded.includes(plan)}
				<ol>
					{#each lines as line, index (`${line.at}:${line.type}:${index}`)}
						<li>
							{#if index === 0 || new Date(lines[index - 1].at).toDateString() !== new Date(line.at).toDateString()}
								<p class="lbl day">{new Date(line.at).toLocaleDateString()}</p>
							{/if}
							<div class="ln">
								<time class="mono muted" datetime={line.at}>{new Date(line.at).toLocaleTimeString()}</time>
								<span class="body">
									<span class="lbl">{line.type.replaceAll('_', ' ')} · {line.checkpoint_id ?? line.initiative_id ?? line.attempt_id ?? plan}</span>
									<span class="tx">{line.summary}</span>
									{#if line.outcome}<span class="mono muted">Outcome {line.outcome} · rules {line.rule_ids?.join(', ') || '—'}</span>{/if}
									{#if line.source_run}<span class="mono muted">Source <a href={`/run?plan=${encodeURIComponent(line.source_run)}`}>{line.source_run}</a> · {line.leaf_ids?.length ?? 0} leaves</span>{/if}
									{#if line.type === 'memory_attention_recorded'}<span class="mono muted">Batch {line.leaf_ids?.length ?? 0} leaves · {Object.values(line.statuses ?? {}).join(', ')}</span>{/if}
								</span>
								<a class="ib sm" href={line.link.path} aria-label="Open in Run, {plan}" title="Open in Run"><Icon name="arrow-up-right" size={14} /></a>
							</div>
						</li>
					{/each}
				</ol>
			{/if}
		</section>
	{/each}
</div>

<style>
	.wa { padding: 0 24px 40px; }
	.bar { display: flex; align-items: center; gap: 14px; padding: 14px 0; flex-wrap: wrap; }
	.bar .sel { width: auto; min-width: 170px; }
	.bar :global(.btn) { margin-left: auto; }
	.alert { display: flex; align-items: center; gap: 12px; padding: 8px 0; flex-wrap: wrap; }
	.note { padding: 6px 0; }
	section { margin-top: 18px; }
	.mono-id { color: var(--tx); font-family: var(--f-mono); letter-spacing: 0.02em; text-transform: none; }
	.more { cursor: pointer; }
	.more:hover { color: var(--tx); }
	ol { list-style: none; padding: 0; }
	li { overflow-wrap: anywhere; }
	.day { padding: 10px 0 4px; }
	.ln { display: grid; grid-template-columns: 84px minmax(0, 1fr) auto; gap: 14px; align-items: start; padding: 8px 0; border-bottom: 1px solid var(--ln); }
	.ln time { font-size: 11.5px; padding-top: 2px; }
	.body { display: grid; gap: 3px; min-width: 0; }
	.tx { color: var(--tx2); }
	a { color: var(--tx); }
</style>
