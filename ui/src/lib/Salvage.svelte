<script lang="ts">
	import { Resource } from './resource.svelte';
	import { daemon, DaemonError, type MemoryCapabilityReport, type MemoryLeaf, type Plan } from './daemon';
	import { salvageAttribution, salvageAvailability, salvageAuthor, salvageEvidence, salvageWhatIsSent, SALVAGE_CAPS, SALVAGE_COST, SALVAGE_SENDING, type CapabilitiesPhase } from './memory';

	let { plan, onchanged }: { plan: Plan | null; onchanged: () => void } = $props();
	let capabilities = $state<Resource<MemoryCapabilityReport> | null>(null);
	let armed = $state<string | null>(null);
	let sending = $state(false);
	let landed = $state<MemoryLeaf[] | null>(null);
	let actionId = $state<string | null>(null);
	$effect(() => {
		if (capabilities) return;
		const resource = new Resource<MemoryCapabilityReport>((signal) => daemon.memoryCapabilities(signal));
		capabilities = resource;
		void resource.load();
	});

	const capPhase = $derived<CapabilitiesPhase>(
		capabilities?.phase === 'loading'
			? { phase: 'reading' }
			: capabilities?.phase === 'error'
			? capabilities.error?.kind === 'unreachable'
				? { phase: 'failed', detail: capabilities.error.message }
				: { phase: 'missing', detail: capabilities.error?.message ?? 'the capability read failed' }
			: capabilities?.data
			? { phase: 'read', authorName: capabilities.data.author?.model ?? null }
			: { phase: 'unread' }
	);
	const evidence = $derived(plan ? salvageEvidence(plan) : null);
	const availability = $derived(plan ? salvageAvailability(plan, capPhase) : { available: false, rules: ['The plan projection has not answered, so preserved failure evidence is unread.'] });
	const receipt = $derived(
		plan?.memory_receipts.filter((entry) => entry.operation === 'salvage' && entry.source_run === plan.id).at(-1) ?? null
	);

	function arm() {
		armed = crypto.randomUUID();
		actionId = armed;
		landed = null;
	}
	function disarm() {
		armed = null;
		actionId = null;
	}
	async function confirm() {
		if (!armed || !plan || !availability.available || sending) return;
		sending = true;
		try {
			const result = await daemon.salvage(plan.id, armed);
			landed = result.leaves;
			armed = null;
			actionId = null;
			onchanged();
		} catch (cause) {
			landed = null;
			const message = cause instanceof DaemonError ? cause.message : 'The salvage request was refused.';
			capabilities?.markStale();
			lastFailure = message;
		} finally {
			sending = false;
		}
	}
	let lastFailure = $state<string | null>(null);
</script>

<section class="salvage">
	<p class="label rule-label"><span>Salvage</span><span class="rule"></span><span class="member" data-state={availability.available ? 'balanced' : 'slack'}>{availability.available ? 'available' : 'unavailable'}</span></p>
	<p class="prose quiet">Authors project memory leaves from this run’s preserved failure evidence. This is the one control on this page that spends model tokens.</p>
	{#if capabilities?.phase === 'loading'}
		<p class="prose quiet" aria-busy="true">Reading the configured memory author…</p>
	{:else if capPhase.phase === 'unread'}
		<p class="prose quiet member" data-state="slack">The configured memory author is unread. <button class="act" type="button" onclick={() => void capabilities?.load()}>Read again</button></p>
	{:else if availability.rules.length > 0}
		{#each availability.rules as rule}<p class="prose quiet member" data-state="slack">{rule}</p>{/each}
	{/if}
	{#if availability.available && !armed && !landed}
		<button class="act plate" type="button" onclick={arm}>Salvage</button>
		<p class="act-gloss">model-consuming authoring from this run’s preserved evidence</p>
	{/if}
	{#if armed && plan && evidence}
		<div class="panel plate">
			<p class="label rule-label"><span>Armed</span><span class="rule"></span><span class="member" data-state="loaded">Salvage</span></p>
			{#if capPhase.phase === 'read' && capabilities?.data?.author}
				<p class="prose panel-line lead-line">{salvageAuthor(capabilities.data.author.model, capabilities.data.author.binary, capabilities.data.author.timeout)}</p>
			{/if}
			<p class="prose panel-line">{salvageWhatIsSent(evidence)}</p>
			<p class="prose panel-line">{SALVAGE_CAPS}</p>
			<p class="prose panel-line">{SALVAGE_COST}</p>
			{#if sending}<p class="prose quiet" aria-busy="true">{SALVAGE_SENDING}</p>{/if}
			<p class="confirmrow"><button class="act plate" type="button" onclick={() => void confirm()} disabled={sending}>{sending ? 'Authoring…' : 'Confirm salvage'}</button><button class="act plate" type="button" onclick={disarm} disabled={sending}>Cancel</button></p>
		</div>
	{/if}
	{#if lastFailure}<p class="member outcome standalone" data-state="failed" role="alert">Not done: {lastFailure}</p>{/if}
	{#if landed}
		<div class="panel plate" role="status">
			<p class="label rule-label"><span>Salvaged</span><span class="rule"></span><span class="member" data-state="seated">{landed.length} {landed.length === 1 ? 'project leaf' : 'project leaves'}</span></p>
			{#each landed as leaf (leaf.id)}<p class="prose"><code>{leaf.id}@{leaf.version}</code> · {leaf.subject} · {leaf.claim}</p>{/each}
			{#if receipt}<p class="prose quiet">{salvageAttribution(receipt.tokens)}</p>{/if}
		</div>
	{/if}
</section>

<style>
	.salvage { margin-top: 1.75rem; }
	.rule-label { display: flex; align-items: baseline; gap: 0.6rem; margin: 0 0 0.9rem; }
	.rule { flex: 1; height: 1px; background: var(--rule); align-self: center; }
	.rule-label > span:last-child { flex: none; max-width: 55%; overflow-wrap: anywhere; text-align: right; }
	.prose { margin: 0 0 0.75rem; max-width: 68ch; color: var(--ink-2); }
	.quiet { font-size: 0.8125rem; }
	.panel { margin-top: 1rem; padding: 1rem; }
	.confirmrow { display: flex; gap: 0.5rem; margin: 1rem 0 0; }
	.act-gloss { display: inline-block; margin: 0.5rem 0 0 0.75rem; color: var(--ink-2); font-size: 0.75rem; }
	button { font: inherit; }
	@media (max-width: 48rem) { .confirmrow { flex-direction: column; align-items: stretch; } }
</style>
