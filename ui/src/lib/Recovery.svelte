<script lang="ts">
	import AsyncField from './AsyncField.svelte';
	import { daemon, DaemonError, type RecoveryReport } from './daemon';
	import type { Resource } from './resource.svelte';
	import {
		FOUR_WAY,
		OUTCOME_STATE,
		OUTCOME_WORD,
		outcomeSentences,
		reconcileLines,
		staleRows,
		summarize,
		unlistedOutcomes
	} from './recovery';

	let {
		planId,
		resource,
		onretry,
		onselect
	}: {
		planId: string;
		resource: Resource<RecoveryReport>;
		onretry: () => void;
		onselect: (id: string) => void;
	} = $props();

	let armed = $state(false);
	let assumeMissing = $state(false);
	let sending = $state<'idle' | 'sending' | 'done' | 'failed'>('idle');
	let outcome = $state('');
	let heading = $state<HTMLHeadingElement | null>(null);
	let armedButton = $state<HTMLButtonElement | null>(null);

	const report = $derived(resource.data);
	const rows = $derived(staleRows(report));
	const hasProbe = $derived(report !== null && Object.keys(report.outcomes).length > 0);
	const recoveryLabel = $derived(report === null ? 'unknown' : hasProbe ? 'probe complete' : `${rows.length} unprobed`);
	const shouldShow = $derived(resource.phase === 'error' || resource.stale || rows.length > 0 || hasProbe || sending === 'done' || sending === 'failed');

	function jump() {
		heading?.focus();
		heading?.scrollIntoView({ block: 'start', behavior: 'auto' });
	}

	function arm() {
		armed = true;
		sending = 'idle';
		queueMicrotask(() => armedButton?.focus());
	}

	function disarm() {
		armed = false;
		assumeMissing = false;
		sending = 'idle';
	}

	async function reconcile(force = false) {
		if (sending === 'sending') return;
		sending = 'sending';
		try {
			const next = await daemon.resume(planId, { assumeMissing: force });
			resource.data = next;
			resource.stale = false;
			resource.phase = 'ready';
			outcome = summarize(next.outcomes);
			sending = 'done';
			armed = false;
			assumeMissing = false;
			queueMicrotask(jump);
		} catch (cause) {
			sending = 'failed';
			outcome = cause instanceof DaemonError ? cause.message : 'The reconciliation request was refused.';
		}
	}
</script>

{#if shouldShow}
	<section class="recovery" aria-labelledby="recovery-title">
		<h2 id="recovery-title" tabindex="-1" bind:this={heading} class="sr">Recovery reconciliation</h2>
		<p id="recovery-label" class="label rule-label" tabindex="-1"><span>Recovery</span><span class="rule"></span><span>{recoveryLabel}</span></p>
		<AsyncField {resource} reading="the recovery report" {onretry}>
			{#snippet children(value: RecoveryReport)}
				{@const currentRows = staleRows(value)}
				{#if currentRows.length === 0 && Object.keys(value.outcomes).length === 0}
					<p class="prose quiet">Nothing on this plan is stale now. Every attempt is one this daemon is tracking. There is nothing to reconcile.</p>
				{:else}
					<p class="prose lead">{currentRows.length} attempt{currentRows.length === 1 ? '' : 's'} on this plan were started by a daemon that is no longer tracking them, which is what a daemon restart leaves behind. Each recorded pane and worktree is shown below; whether either is still alive is unknown until something probes them. Nothing here has been changed.</p>
					<div class="tablewrap">
						<table>
							<caption class="sr">Attempts this daemon does not own</caption>
							<thead><tr><th>Member</th><th>Attempt</th><th>Pane</th><th>Worktree</th><th>Outcome</th></tr></thead>
							<tbody>
								{#each currentRows as row (row.initiative_id + row.attempt_id)}
									<tr>
										<th scope="row"><button class="pick" type="button" onclick={() => onselect(row.initiative_id)}>{row.initiative_id}</button></th>
										<td><code>{row.attempt_id}</code></td>
										<td>{row.pane_ref ?? 'no pane was recorded'}</td>
										<td>{row.worktree_ref ?? 'no worktree was recorded'}</td>
										<td><span class="member outcome-mark" data-state={OUTCOME_STATE[row.outcome] ?? 'slack'}>{row.outcome === 'unprobed' || row.outcome === 'not reported' ? '—' : OUTCOME_WORD[row.outcome] ?? row.outcome}</span></td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#if !hasProbe}
						<p class="prose quiet">Whether these panes are still alive is unknown until something probes them. The read-only report classifies nothing as surviving or missing.</p>
					{/if}
					{#if armed}
						<div class="panel plate">
							<p class="label rule-label"><span>Armed</span><span class="rule"></span><span class="member" data-state="loaded">Reconcile</span></p>
							{#each reconcileLines(currentRows.length) as line, index (index)}<p class="prose panel-line" class:lead-line={index === 0}>{line}</p>{/each}
							{#each FOUR_WAY as [label, text] (label)}<p class="prose panel-line"><strong>{label}</strong> — {text}</p>{/each}
							<p class="prose quiet">If herdr cannot be reached, this is refused and nothing is written. Sending this twice is safe, except a checkpoint waiting for review remains listed until a reviewer decides.</p>
							{#if sending === 'failed'}<p class="prose member" data-state="failed" role="alert">Not done: {outcome}</p>{/if}
							<p class="confirmrow"><button class="act plate" bind:this={armedButton} type="button" disabled={sending === 'sending'} onclick={() => void reconcile(false)}>{sending === 'sending' ? 'Reconciling…' : 'Confirm reconcile'}</button><button class="act plate" type="button" disabled={sending === 'sending'} onclick={disarm}>Cancel</button></p>
						</div>
						{#if sending === 'failed'}
							<div class="panel plate"><p class="label rule-label"><span>Probe refused</span><span class="rule"></span></p><p class="prose">Herdsman cannot reach herdr. Closing these without probing records a failure against each one, on your word that the panes are gone. An attempt whose checkpoint already landed still finishes under the settlement policy; completed work is not thrown away. If herdr is only temporarily unreachable, reconnecting and reconciling normally can recover work that this closes without collecting.</p><p class="confirmrow"><button class="act plate" type="button" onclick={() => void reconcile(true)}>Confirm close as missing</button><button class="act plate" type="button" onclick={disarm}>Cancel</button></p></div>
						{/if}
					{:else}
						<p class="confirmrow"><button class="act plate" type="button" onclick={arm}>Reconcile</button></p>
					{/if}
				{/if}
				{#if sending === 'done'}<p class="member outcome" data-state="seated" role="status">{outcome}</p>{/if}
				{#if hasProbe}
					{#each outcomeSentences(value.outcomes) as [word, sentence] (word)}<p class="prose outcome-note"><strong>{word}</strong> — {sentence}</p>{/each}
					{#if unlistedOutcomes(value).length}<p class="prose quiet">The probe also returned {unlistedOutcomes(value).length} outcome{unlistedOutcomes(value).length === 1 ? '' : 's'} without a stale attempt row; those outcomes are reported here rather than attached to an invented attempt.</p>{/if}
					{#if value.orphaned_panes.length || value.orphaned_worktrees.length}
						<p class="prose quiet">Live Herdsman-owned resources that no recorded attempt claims — what a daemon death leaves behind after its plan stopped referring to them. Nothing here has been removed, and this surface removes nothing.</p>
						<div class="orphan-grid">
							<div><p class="label rule-label"><span>Orphaned panes</span><span class="rule"></span><span>{value.orphaned_panes.length}</span></p><ul class="evidence">{#each value.orphaned_panes as pane (pane)}<li><code>{pane}</code></li>{/each}</ul></div>
							<div><p class="label rule-label"><span>Orphaned worktrees</span><span class="rule"></span><span>{value.orphaned_worktrees.length}</span></p><ul class="evidence">{#each value.orphaned_worktrees as worktree (worktree)}<li><code>{worktree}</code></li>{/each}</ul></div>
						</div>
					{:else}
						<p class="prose quiet">The probe found no unclaimed Herdsman-owned pane or worktree.</p>
					{/if}
				{/if}
				<p class="label rule-label"><span>Holding the whole plan</span><span class="rule"></span><span>Not available</span></p><p class="prose quiet">Nothing in this product holds or cancels a whole plan in one write. Holding and cancelling are decided one member at a time, in each member’s own drawer.</p>
			{/snippet}
		</AsyncField>
	</section>
{/if}

<style>
	.recovery { margin: 1.75rem 0; }
	.sr { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
	.rule-label { display:flex; align-items:baseline; gap:.75rem; margin:0 0 .9rem; }
	.rule { flex:1; height:1px; background:var(--rule); }
	.prose { max-width:68ch; color:var(--ink-2); margin:0; }
	.quiet { font-size:.8125rem; }
	.lead { margin-bottom:1rem; }
	.tablewrap { overflow:auto; max-width:100%; }
	table { width:100%; border-collapse:collapse; table-layout:fixed; }
	th,td { padding:.6rem .45rem; text-align:left; vertical-align:top; border-bottom:1px solid var(--rule); overflow-wrap:anywhere; }
	th { font-size:.625rem; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-2); }
	.pick { border:0; background:none; color:var(--ink); font:inherit; cursor:pointer; padding:0; }
	.outcome-mark { display:inline-block; padding:0 .15rem; border-bottom:1px solid currentColor; }
	.outcome-mark[data-state='balanced'] { border-bottom-style:dotted; }
	.outcome-mark[data-state='failed'] { border-bottom:3px double currentColor; }
	.outcome-mark[data-state='slack'] { color:var(--ink-2); border-bottom:1px dashed var(--ash); }
	.panel { margin-top:1rem; padding:1rem; border:1px solid var(--rule-strong); background:var(--plate); }
	.panel-line + .panel-line { margin-top:.55rem; }
	.lead-line { color:var(--ink); }
	.confirmrow { display:flex; flex-wrap:wrap; gap:.6rem; margin:1rem 0 0; align-items:center; }
	.act { font:inherit; font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; border:1px solid var(--rule-strong); background:transparent; color:var(--ink); padding:.4rem .8rem; cursor:pointer; }
	.act:hover:not(:disabled) { border-color:var(--red); color:var(--red); }
	.outcome { display:block; margin-top:1rem; }
	.outcome-note { margin-top:.8rem; }
	.outcome-note strong { color:var(--ink); text-transform:uppercase; letter-spacing:.08em; font-size:.7rem; }
	.evidence { list-style:none; margin:.5rem 0 0; padding:0; }
	.evidence li { border-bottom:1px solid var(--rule); padding:.45rem 0; overflow-wrap:anywhere; }
	.evidence code { color:var(--ink); }
	.orphan-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:1.5rem; margin-top:1rem; }
	@media (max-width:60rem) { th:nth-child(2), td:nth-child(2), th:nth-child(4), td:nth-child(4) { display:none; } }
	@media (max-width:48rem) { th:nth-child(3), td:nth-child(3) { display:none; } .orphan-grid { grid-template-columns:1fr; } }
</style>
