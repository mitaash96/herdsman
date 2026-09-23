<script lang="ts">
	import type { DigestEntry } from './daemon';
	import { grouped, latestOnly, typeCounts, type DigestWindow } from './digest';
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

<div class="digest-controls">
	<label for="digest-window">Window</label>
	<select id="digest-window" value={window} onchange={(event) => onwindow(event.currentTarget.value as DigestWindow)}>
		<option value="since">Since last visit</option>
		<option value="day">Last 24 h</option>
		<option value="week">Last 7 days</option>
	</select>
	<button type="button" onclick={onmark}>Mark as read</button>
</div>
<p>From <time datetime={since}>{new Date(since).toLocaleString()}</time>{#if firstVisit && window === 'since'} · First visit: last 24 h{/if}</p>
{#if loading && !entries}<p role="status">Reading changes…</p>{/if}
{#if error}<p role="alert">Stale · {error}. Last read kept. <button type="button" onclick={() => onwindow(window)}>Read again</button></p>{/if}
{#if entries && latestOnly(entries)}<p role="status">Showing the latest 200 changes; older ones are not in this view.</p>{/if}
{#if entries && !entries.length}<p>Nothing changed since {new Date(since).toLocaleString()}.</p>{/if}
{#each groups as [plan, lines] (plan)}
	<section aria-label={`Changes in ${plan}`}>
		<h3>{plan}</h3>
		{#if long}<button type="button" aria-expanded={expanded.includes(plan)} onclick={() => toggle(plan)}>{typeCounts(lines)} · {expanded.includes(plan) ? 'Collapse' : 'Expand'}</button>{/if}
		{#if !long || expanded.includes(plan)}
			<ol>
				{#each lines as line, index (`${line.at}:${line.type}:${index}`)}
					<li>
						{#if index === 0 || new Date(lines[index - 1].at).toDateString() !== new Date(line.at).toDateString()}
							<p class="day">{new Date(line.at).toLocaleDateString()}</p>
						{/if}
						<p><time datetime={line.at}>{new Date(line.at).toLocaleTimeString()}</time> · {line.type.replaceAll('_', ' ')} · {line.checkpoint_id ?? line.initiative_id ?? line.attempt_id ?? plan}</p>
						<p>{line.summary}</p>
						{#if line.outcome}<p>Outcome: {line.outcome} · Rules: {line.rule_ids?.join(', ') || '—'}</p>{/if}
						{#if line.source_run}<p>Source: <a href={`/run?plan=${encodeURIComponent(line.source_run)}`}>{line.source_run}</a> · {line.leaf_ids?.length ?? 0} leaves</p>{/if}
						{#if line.type === 'memory_attention_recorded'}<p>Batch: {line.leaf_ids?.length ?? 0} leaves · {Object.values(line.statuses ?? {}).join(', ')}</p>{/if}
						<a href={line.link.path}>Open in Run → {plan}</a>
					</li>
				{/each}
			</ol>
		{/if}
	</section>
{/each}
<style>
	.digest-controls { display: flex; gap: .75rem; flex-wrap: wrap; align-items: baseline; }
	button, select { font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); padding: .4rem .7rem; cursor: pointer; }
	section { margin-top: 1.5rem; border-top: 1px solid var(--rule); }
	h3 { font-size: 1rem; }
	ol { list-style: none; padding: 0; }
	li { padding: .7rem 0; border-top: 1px solid var(--rule); overflow-wrap: anywhere; }
	li p { margin: .25rem 0; }
	.day { font-weight: 500; margin-top: 1rem; }
</style>
