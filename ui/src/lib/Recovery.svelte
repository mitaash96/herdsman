<script lang="ts">
	import AsyncField from './AsyncField.svelte';
	import Button from './Button.svelte';
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
	let reconciled = $state<RecoveryReport | null>(null);
	let heading = $state<HTMLHeadingElement | null>(null);
	let armedButton = $state<HTMLButtonElement | null>(null);

	const report = $derived(resource.data);
	/* Keep the resume response visible when the SSE-triggered refresh correctly
	   observes that there is nothing stale left to report. */
	const displayedReport = $derived(sending === 'done' && reconciled ? reconciled : report);
	const rows = $derived(staleRows(displayedReport));
	const hasProbe = $derived(displayedReport !== null && Object.keys(displayedReport.outcomes).length > 0);
	const recoveryLabel = $derived(
		displayedReport === null
			? 'unknown'
			: hasProbe
				? 'probe complete'
				: rows.length > 0
					? `${rows.length} unprobed`
					: 'nothing stale'
	);
	const shouldShow = $derived(resource.phase === 'error' || resource.stale || rows.length > 0 || hasProbe || sending === 'done' || sending === 'failed');

	function jump() {
		heading?.focus();
		heading?.scrollIntoView({ block: 'start', behavior: 'auto' });
	}

	function arm() {
		armed = true;
		reconciled = null;
		sending = 'idle';
		queueMicrotask(() => armedButton?.focus());
	}

	function disarm() {
		armed = false;
		reconciled = null;
		assumeMissing = false;
		sending = 'idle';
	}

	async function reconcile(force = false) {
		if (sending === 'sending') return;
		sending = 'sending';
		try {
			const next = await daemon.resume(planId, { assumeMissing: force });
			resource.data = next;
			reconciled = next;
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
	<section class="recovery pane-in" aria-labelledby="recovery-title">
		<h2 id="recovery-title" tabindex="-1" bind:this={heading} class="sr-only">Recovery reconciliation</h2>
		<div id="recovery-label" class="rule-h" tabindex="-1"><span class="lbl">Recovery</span><span class="r lbl">{recoveryLabel}</span></div>
		<AsyncField {resource} reading="the recovery report" {onretry}>
			{#snippet children(value: RecoveryReport)}
				{@const shown = sending === 'done' && reconciled ? reconciled : value}
				{@const currentRows = staleRows(shown)}
				{#if currentRows.length === 0 && Object.keys(shown.outcomes).length === 0}
					<p class="empty-line">Nothing on this plan is stale. Every attempt is one this daemon is tracking.</p>
				{:else}
					{#if currentRows.length > 0}
						<table class="tbl">
							<caption class="sr-only">Attempts this daemon does not own</caption>
							<thead><tr><th>Member</th><th class="c-attempt">Attempt</th><th class="c-pane">Pane</th><th class="c-tree">Worktree</th><th>Outcome</th></tr></thead>
							<tbody>
								{#each currentRows as row (row.initiative_id + row.attempt_id)}
									<tr class="click" onclick={() => onselect(row.initiative_id)}>
										<td><button class="chip" type="button" onclick={(e) => { e.stopPropagation(); onselect(row.initiative_id); }}>{row.initiative_id}</button></td>
										<td class="mono c-attempt">{row.attempt_id}</td>
										<td class="mono c-pane">{row.pane_ref ?? '—'}</td>
										<td class="mono c-tree">{row.worktree_ref ?? '—'}</td>
										<td><span class="state" data-tone={OUTCOME_STATE[row.outcome] === 'failed' ? 'failed' : OUTCOME_STATE[row.outcome] === 'seated' ? 'settled' : 'waiting'}>{row.outcome === 'unprobed' || row.outcome === 'not reported' ? '—' : OUTCOME_WORD[row.outcome] ?? row.outcome}</span></td>
									</tr>
								{/each}
							</tbody>
						</table>
						{#if !hasProbe}
							<p class="hint pad">Whether these panes are still alive is unknown until something probes them. Nothing here has been changed.</p>
						{/if}
					{/if}
					{#if armed}
						<div class="panel">
							<div class="rule-h"><span class="lbl">Armed</span><span class="r lbl">Reconcile</span></div>
							{#each reconcileLines(currentRows.length) as line, index (index)}<p class="prose" class:lead-line={index === 0}>{line}</p>{/each}
							<dl class="contrast">{#each FOUR_WAY as [label, text] (label)}<div><dt class="lbl">{label}</dt><dd class="muted">{text}</dd></div>{/each}</dl>
							<p class="hint">If herdr cannot be reached, this is refused and nothing is written. Sending this twice is safe, except a checkpoint waiting for review remains listed until a reviewer decides.</p>
							{#if sending === 'failed'}<p class="state" data-tone="failed" role="alert">Not done: {outcome}</p>{/if}
							<div class="btnrow">
								<Button icon="play" kind="primary" busy={sending === 'sending'} onclick={() => void reconcile(false)}>Resume</Button>
								<Button icon="x" disabled={sending === 'sending'} onclick={disarm}>Cancel</Button>
							</div>
						</div>
						{#if sending === 'failed'}
							<div class="panel">
								<div class="rule-h"><span class="lbl">Probe refused</span></div>
								<p class="prose">Herdsman cannot reach herdr. Discarding these records a failure against each one, on your word that the panes are gone. An attempt whose checkpoint already landed still finishes under the settlement policy; completed work is not thrown away. If herdr is only temporarily unreachable, reconnecting and resuming normally can recover work that this closes without collecting.</p>
								<div class="btnrow">
									<Button icon="trash-2" kind="danger" onclick={() => void reconcile(true)}>Discard as missing</Button>
									<Button icon="x" onclick={disarm}>Cancel</Button>
								</div>
							</div>
						{/if}
					{:else if currentRows.length > 0}
						<div class="btnrow pad"><Button icon="play" kind="primary" onclick={arm}>Resume</Button></div>
					{/if}
				{/if}
				{#if sending === 'done'}<p class="pad"><span class="state" data-tone="pass" role="status">{outcome}</span></p>{/if}
				{#if hasProbe}
					<div class="notes">
						{#each outcomeSentences(shown.outcomes) as [word, sentence] (word)}<p class="prose"><strong>{word}</strong> — {sentence}</p>{/each}
						{#if unlistedOutcomes(shown).length}<p class="hint">The probe also returned {unlistedOutcomes(shown).length} outcome{unlistedOutcomes(shown).length === 1 ? '' : 's'} without a stale attempt row; they are reported here rather than attached to an invented attempt.</p>{/if}
						{#if shown.orphaned_panes.length || shown.orphaned_worktrees.length}
							<div class="orphan-grid">
								<div><div class="rule-h"><span class="lbl">Orphaned panes</span><span class="r count">{shown.orphaned_panes.length}</span></div><ul class="evidence">{#each shown.orphaned_panes as pane (pane)}<li class="mono">{pane}</li>{/each}</ul></div>
								<div><div class="rule-h"><span class="lbl">Orphaned worktrees</span><span class="r count">{shown.orphaned_worktrees.length}</span></div><ul class="evidence">{#each shown.orphaned_worktrees as worktree (worktree)}<li class="mono">{worktree}</li>{/each}</ul></div>
							</div>
						{:else}
							<p class="hint">The probe found no unclaimed Herdsman-owned pane or worktree.</p>
						{/if}
					</div>
				{/if}
			{/snippet}
		</AsyncField>
	</section>
{:else}
	<p class="empty-line">Nothing on this plan is stale. Every attempt is one this daemon is tracking.</p>
{/if}

<style>
	.recovery { padding: 20px 24px 40px; }
	.pad { padding: 12px 0 0; }
	.mono { overflow-wrap: anywhere; }
	.tbl td { vertical-align: top; }
	.chip { background: none; cursor: pointer; }
	.chip:hover { color: var(--tx); border-color: var(--dim); }
	.panel { margin-top: 18px; padding: 16px; border: 1px solid var(--ln2); background: var(--p1); display: grid; gap: 10px; }
	.panel .btnrow { margin-top: 4px; }
	.lead-line { color: var(--tx); }
	.contrast { display: grid; gap: 8px; }
	.contrast div { border-top: 1px solid var(--ln); padding-top: 8px; }
	.contrast dd { margin: 2px 0 0; }
	.notes { margin-top: 18px; display: grid; gap: 10px; }
	.evidence { list-style: none; }
	.evidence li { border-bottom: 1px solid var(--ln); padding: 7px 0; overflow-wrap: anywhere; }
	.orphan-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
	@media (max-width: 1023px) { .c-attempt, .c-tree { display: none; } }
	@media (max-width: 767px) { .c-pane { display: none; } .orphan-grid { grid-template-columns: 1fr; } }
</style>
