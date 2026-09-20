<script lang="ts">
	import { daemon, DaemonError, type Plan } from './daemon';
	import { refusalMessage } from './revision';
	import type { Resource } from './resource.svelte';

	let { planId, version, plan, onrevised }: {
		planId: string;
		version: number;
		plan: Resource<Plan> | null;
		onrevised: () => void;
	} = $props();

	type Phase = 'idle' | 'armed' | 'sending' | 'done' | 'failed';
	let phase = $state<Phase>('idle');
	let reason = $state('');
	let actionId = $state<string | null>(null);
	let message = $state('');
	let confirmEl = $state<HTMLButtonElement | null>(null);
	let startedAt = $state<number | null>(null);
	let slow = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	function arm() {
		actionId = crypto.randomUUID();
		phase = 'armed';
		message = '';
		queueMicrotask(() => confirmEl?.focus());
	}
	function disarm() {
		if (phase === 'sending') return;
		phase = 'idle';
		actionId = null;
		message = '';
		slow = false;
		if (timer) clearTimeout(timer);
	}
	async function confirm() {
		if (phase !== 'armed') return;
		phase = 'sending';
		startedAt = Date.now();
		slow = false;
		timer = setTimeout(() => { slow = true; }, 20_000);
		try {
			const result = await daemon.recalibrate(planId, { reason: reason.trim() || null, action_id: actionId ?? undefined });
			phase = 'done';
			message = result.to_version === version + 1
				? `Revision ${result.to_version} is recorded. Nothing runs until you approve it.`
				: `That request was already answered. This is revision ${result.to_version} — the planner was not called a second time.`;
			actionId = null;
			onrevised();
			queueMicrotask(() => document.getElementById('gate-revision-title')?.focus());
		} catch (cause) {
			const error = cause instanceof DaemonError ? cause : new DaemonError('bad_response', 'The daemon refused this request.');
			const kind = refusalMessage(error.status, error.message);
			phase = 'failed';
			const raced = kind === 'refusal' && error.message.toLowerCase().includes('plan changed');
			message = raced
				? 'The plan changed while the planner was thinking, so the daemon refused the answer rather than record a revision of a plan that had already moved. Nothing was appended. Read the plan as it is now, then ask again.'
				: error.status === 400
					? `${error.message} Nothing was appended: the plan is still at revision ${version} and its approval is unchanged.`
					: error.message;
			if (raced) onrevised();
			// Preserve action_id for network/5xx loss so a retry is idempotent.
			if (error.status !== null && error.status >= 400 && error.status < 500) actionId = null;
		} finally {
			if (timer) clearTimeout(timer);
		}
	}
</script>

<section class="recalibrate" aria-labelledby="recalibrate-title">
	<p class="label rule-label" id="recalibrate-title" tabindex="-1"><span>Revise</span><span class="rule"></span><span>planner</span></p>
	{#if phase === 'done'}
		<p class="prose member outcome" data-state="seated" role="status">{message} The comparison is above.</p>
	{:else if phase === 'failed'}
		<p class="lead member" data-state="failed" role="alert">Not revised.</p>
		<p class="prose">{message}</p>
		<p class="actions"><button class="act" type="button" onclick={disarm}>Try again</button></p>
	{:else if phase === 'armed'}
		{#if plan?.data?.planner}
			<p class="prose">This plan was decomposed by <strong>{plan.data.planner.harness} · {plan.data.planner.model}</strong>. The daemon calls the project's configured planner default if one is set, and this plan's planner otherwise.</p>
		{:else}
			<p class="prose">This plan's planner is unread. The daemon calls the project's configured planner default if one is set, and this plan's planner otherwise.</p>
		{/if}
		<p class="prose">This calls the planner. It receives the plan's remaining work, residual claims and bounded failure evidence, plus the fixed nodes this sheet can see and any attempt the daemon is still settling, by digest only. Completed work and approved checkpoints come back unchanged; running members are not interrupted. If it answers, this plan gains revision {version + 1}, approval returns to pending, and nothing further runs until you approve the new revision. This spends planner tokens whether or not you approve the result.</p>
		<p class="prose quiet">Your reason rides along as one line. No transcript, earlier revision or memory goes with it. This request carries an id, so asking again after a lost answer returns the revision it already made rather than calling the planner twice.</p>
		<p class="actions"><button class="act" type="button" bind:this={confirmEl} onclick={() => void confirm()}>Confirm — call the planner</button><button class="act" type="button" onclick={disarm}>Cancel</button></p>
	{:else if phase === 'sending'}
		<p class="prose member" data-state="balanced" aria-busy="true">Calling the planner…</p>
		{#if slow}<p class="prose quiet">The planner has up to two minutes. Leaving this sheet does not cancel it — the revision appears when it answers.</p>{/if}
	{:else}
		<label class="label" for="recalibration-reason">Why this plan is wrong</label>
		<textarea id="recalibration-reason" rows="2" bind:value={reason} maxlength="400" aria-describedby="recalibration-help"></textarea>
		<p id="recalibration-help" class="prose quiet">Optional. One line reaches the planner, and it is recorded on the revision for audit.</p>
		<p class="actions"><button class="act" type="button" onclick={arm}>Revise the plan</button></p>
	{/if}
</section>

<style>
	.recalibrate { margin-top:1.75rem; }
	.rule-label { display:flex; align-items:baseline; gap:.6rem; margin:0 0 .9rem; }
	.rule { flex:1; height:1px; background:var(--rule); }
	textarea { display:block; width:100%; box-sizing:border-box; margin-top:.45rem; resize:vertical; border:1px solid var(--rule-strong); background:var(--plate); color:var(--ink); font:inherit; padding:.6rem .7rem; }
	textarea:focus { outline:2px solid var(--red); outline-offset:2px; }
	.prose { max-width:68ch; } .quiet { color:var(--ink-2); font-size:.75rem; }
	.actions { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:.75rem; }
	.outcome { margin-top:.7rem; } .lead { margin:.65rem 0 0; font-weight:600; }
</style>
