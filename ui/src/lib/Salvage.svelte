<script lang="ts">
	import Button from './Button.svelte';
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

<section class="salvage pane-in">
	<div class="rule-h"><span class="lbl">Salvage</span><span class="r"><span class="state" data-tone={availability.available ? 'ready' : 'waiting'}>{availability.available ? 'Available' : 'Unavailable'}</span></span></div>
	<p class="prose">Authors project memory leaves from this run’s preserved failure evidence. This is the one control on this page that spends model tokens.</p>
	{#if capabilities?.phase === 'loading'}
		<p class="muted" aria-busy="true">Reading the configured memory author…</p>
	{:else if capPhase.phase === 'unread'}
		<p class="muted">The configured memory author is unread.</p>
		<div class="btnrow"><Button icon="rotate-ccw" small onclick={() => void capabilities?.load()}>Read again</Button></div>
	{:else if availability.rules.length > 0}
		{#each availability.rules as rule}<p class="muted">{rule}</p>{/each}
	{/if}
	{#if availability.available && !armed && !landed}
		<div class="btnrow"><Button icon="life-buoy" kind="primary" title="Model-consuming authoring from this run’s preserved evidence" onclick={arm}>Salvage</Button></div>
	{/if}
	{#if armed && plan && evidence}
		<div class="panel">
			<div class="rule-h"><span class="lbl">Armed</span><span class="r lbl">Salvage</span></div>
			{#if capPhase.phase === 'read' && capabilities?.data?.author}
				<p class="prose lead-line">{salvageAuthor(capabilities.data.author.model, capabilities.data.author.binary, capabilities.data.author.timeout)}</p>
			{/if}
			<p class="prose">{salvageWhatIsSent(evidence)}</p>
			<p class="prose">{SALVAGE_CAPS}</p>
			<p class="prose">{SALVAGE_COST}</p>
			{#if sending}<p class="muted" aria-busy="true">{SALVAGE_SENDING}</p>{/if}
			<div class="btnrow">
				<Button icon="life-buoy" kind="primary" busy={sending} onclick={() => void confirm()}>Confirm salvage</Button>
				<Button icon="x" disabled={sending} onclick={disarm}>Cancel</Button>
			</div>
		</div>
	{/if}
	{#if lastFailure}<p class="state" data-tone="failed" role="alert">Not done: {lastFailure}</p>{/if}
	{#if landed}
		<div class="panel" role="status">
			<div class="rule-h"><span class="lbl">Salvaged</span><span class="r"><span class="state" data-tone="pass">{landed.length} {landed.length === 1 ? 'project leaf' : 'project leaves'}</span></span></div>
			{#each landed as leaf (leaf.id)}<p class="prose"><span class="mono">{leaf.id}@{leaf.version}</span> · {leaf.subject} · {leaf.claim}</p>{/each}
			{#if receipt}<p class="hint">{salvageAttribution(receipt.tokens)}</p>{/if}
		</div>
	{/if}
</section>

<style>
	.salvage { padding: 20px 24px 40px; display: grid; gap: 10px; align-content: start; }
	.panel { padding: 16px; border: 1px solid var(--ln2); background: var(--p1); display: grid; gap: 10px; }
	.lead-line { color: var(--tx); }
	.btnrow { margin-top: 4px; }
</style>
