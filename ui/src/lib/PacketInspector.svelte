<script lang="ts">
	/*
	  Unit R7 — the packet inspector, inside R2's drawer. Its design brief is
	  `.impeccable/surfaces/r7-design-brief.md`; R6's rules for arming and
	  refusal and R4's rules for the reading width are inherited, not relitigated.

	  What one attempt was actually handed, read where the attempt is. The
	  sections come from the plan the drawer already holds (`Attempt.
	  packet_snapshot` rides `GET /plans/{id}`), so this section adds no read of
	  its own — and nothing in it fills in asynchronously, which is what keeps
	  the reading width's hand re-pin honest. The comparison comes from the
	  daemon's diff route, read on demand: the definition of "changed" is the
	  daemon's, and a browser-side deep-equal would be a second one.

	  The selected attempt and the armed comparison reset on a different member
	  and on nothing else — keying reset on anything the operator's own writes
	  can move would wipe their place on the re-read their write triggered.
	*/
	import { tick, untrack } from 'svelte';
	import AsyncField from './AsyncField.svelte';
	import Button from './Button.svelte';
	import {
	daemon,
	type Attempt,
	type Initiative,
	type Kitchen,
	type MemoryStatus,
	type PacketDiff,
	type Plan,
	type PacketSection
} from './daemon';
	import { Resource } from './resource.svelte';
	import { count } from './shelf';
	import {
		codeLike,
		commonProvenance,
		comparableAttempts,
		comparisonPair,
		defaultAttempt,
		diffList,
		diffVerdict,
		provenanceMarker,
		sectionBody,
		sectionRows,
		sectionTokens,
		sourceSentence,
		totalsAgree
	} from './packet';
	import {
		carriedLeaves,
		classDisagreement,
		currentVerdict,
		DELIVERY_VS_PACKET,
		FENCE_GLOSS,
		MODE_SENTENCES,
		NO_LEAVES,
		PAIRING_REFUSED,
		receiptsFor,
		RECEIPT_ESTIMATES,
		STATUS_UNREAD,
		CARRIED_HEADER,
		memoryRun
	} from './memory';

	let {
		planId,
		id,
		initiative,
		plan,
		expanded,
		onexpand,
		memoryStatus,
		kitchen,
		historical = false
	}: {
		planId: string;
		/** The member the drawer is open for. The reset key. */
		id: string;
		/** From the folded plan. */
		initiative: Initiative | null;
		plan: Pick<Plan, 'memory_receipts'> | null;
		/** The shared reading-width boolean: one sheet, one width. */
		expanded: boolean;
		/** Expand through the drawer, with this section as the re-pin anchor. */
		onexpand: (next: boolean, anchor?: HTMLElement | null) => void;
		memoryStatus: Resource<MemoryStatus> | null;
		kitchen: Resource<Kitchen> | null;
		historical?: boolean;
	} = $props();

	/** Collapsed, a list this long stops and says how much it is holding back. */
	const CAP = 5;

	const attempts = $derived<Attempt[]>(initiative?.attempts ?? []);
	const attemptNumber = $derived.by(() => {
		const byId = new Map(attempts.map((attempt, at) => [attempt.id, at + 1]));
		return (attempt: Attempt) => byId.get(attempt.id) ?? 0;
	});
	const attemptWord = (attempt: Attempt): string =>
		attempt.origin === 'retry' ? 'retry' : 'run';

	/* The selected attempt resets on a different member and on nothing else. */
	let chosenId = $state<string | null>(null);
	$effect(() => {
		void id;
		chosenId = null;
	});
	/* No snapshot anywhere still selects the latest attempt: the missing
	   state is a fact about an attempt — it names the total that was
	   recorded — so it needs an attempt to be about. */
	const selected = $derived<Attempt | null>(
		chosenId
			? (attempts.find((attempt) => attempt.id === chosenId) ?? null)
			: (defaultAttempt(attempts) ?? attempts[attempts.length - 1] ?? null)
	);
	const snapshot = $derived(selected?.packet_snapshot ?? null);
	const agree = $derived(snapshot === null || totalsAgree(snapshot));
	const shared = $derived(snapshot === null ? null : commonProvenance(snapshot));
	const memory = $derived(snapshot ? memoryRun(snapshot) : null);
	const memoryStart = $derived(
		memory ? snapshot?.sections.findIndex((section) => section === memory.sections[0]) ?? -1 : -1
	);
	const memoryEnd = $derived(memoryStart < 0 ? -1 : memoryStart + (memory?.sections.length ?? 0) - 1);
	const memoryPair = $derived(selected ? carriedLeaves(selected) : null);
	const memoryReceipts = $derived(selected && plan ? receiptsFor(selected, plan) : []);

	/* Selecting a different attempt disarms a comparison: the pair is defined
	   by the selected attempt, so a stale pair is not a state this section
	   holds. A snapshot-less selection offers no comparison at all. */
	let compareWithId = $state<string>('');
	let diff = $state<Resource<PacketDiff> | null>(null);
	$effect(() => {
		void id;
		compareWithId = '';
		/* Disposing the previous read must not become a dependency of this
		   effect: an effect that reads the value it (or its sibling) writes
		   re-triggers on its own write, and the armed comparison would wipe
		   itself the moment it armed — the trap H1 and L1 both recorded. */
		untrack(() => diff?.dispose());
		diff = null;
	});
	const comparable = $derived(comparableAttempts(selected, attempts));
	const pair = $derived.by(() => {
		if (!selected || !snapshot || compareWithId === '') return null;
		const other = attempts.find((attempt) => attempt.id === compareWithId);
		return other && other.packet_snapshot && other.id !== selected.id
			? comparisonPair(selected, other, attempts)
			: null;
	});
	/* One Resource per pair, keyed on the pair's two ids: a live re-read that
	   returns the same pair must not tear the read down and re-arm it, and a
	   genuinely new pair disposes the old one. `armedKeys` is a plain let — an
	   effect that wrote a value it also read would re-trigger itself on its
	   own write (the trap H1 and L1 both recorded). */
	let armedKeys = '';
	$effect(() => {
		const key = pair ? `${pair.before.id}>${pair.after.id}` : '';
		if (key === armedKeys) return;
		armedKeys = key;
		const previous = untrack(() => diff);
		diff = pair
			? new Resource<PacketDiff>((signal) =>
					daemon.packetDiff(planId, pair.before.id, pair.after.id, signal)
				)
			: null;
		previous?.dispose();
		if (diff) void diff.load();
	});

	/* Something is actually held back at sheet width: a list past the cap, or
	   prose long enough to clamp. Either way the reader is owed the control
	   the clamp note points at. */
	const holding = $derived(
		(snapshot?.sections ?? []).some((section) => {
			const body = sectionBody(section.value);
			return (
				(body.kind === 'string' && body.text.length > CLAMP_AT) ||
				(body.kind === 'list' && body.items.length > CAP) ||
				(body.kind === 'objectList' && body.items.length > CAP)
			);
		})
	);

	/* A prose value this long is clamped at sheet width and says so. The bound
	   is the daemon's own context-brief cap, not a measured layout. */
	// ponytail: a length heuristic stands in for a measured overflow; a
	// resize-aware clamp would need a ResizeObserver for one sentence.
	const CLAMP_AT = 280;

	let rootEl = $state<HTMLElement | null>(null);
	let headEl = $state<HTMLElement | null>(null);
	let listEl = $state<HTMLElement | null>(null);

	/** The attempt plates' entry point: select and bring the section in view. */
	export async function selectAttempt(attemptId: string): Promise<void> {
		chosenId = attemptId;
		await tick();
		headEl?.scrollIntoView({ block: 'start' });
	}

	/* Arming a comparison inserts the plate above the list and pushes it down,
	   so the list's top is re-pinned the same way R4 re-pins the reader:
	   measure, insert, restore. The scroll box is the drawer's own `.body`. */
	async function armWith(otherId: string): Promise<void> {
		const box = listEl?.closest('.dk-body');
		const before =
			box && listEl
				? listEl.getBoundingClientRect().top - box.getBoundingClientRect().top
				: null;
		compareWithId = otherId;
		await tick();
		if (before === null || !(box instanceof HTMLElement) || !listEl) return;
		const after = listEl.getBoundingClientRect().top - box.getBoundingClientRect().top;
		box.scrollTop += after - before;
	}

	const currentClass = $derived.by(() => {
		if (!selected || kitchen?.data === null || kitchen?.data === undefined) return null;
		return kitchen.data.adapters.find((adapter) => adapter.name === selected.assignment.harness)
			?.capabilities.memory ?? null;
	});
	const receiptPairs = (receipt: (typeof memoryReceipts)[number]['receipt']) =>
		receipt.leaf_ids.length === receipt.leaf_versions.length
			? receipt.leaf_ids.map((id, index) => `${id}@${receipt.leaf_versions[index]}`)
			: [...receipt.leaf_ids, ...receipt.leaf_versions.map((version) => `@${version}`)];
	const shownAt = (value: string): string => {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
	};
	const objectCell = (value: unknown): string =>
		typeof value === 'string' ? value : JSON.stringify(value);
</script>

<section class="pk" bind:this={rootEl}>
	{#if attempts.length === 0}
		<p class="dk-note full">A packet is compiled when an attempt is reserved; there is nothing received to read yet.</p>
	{:else}
		<div class="side" bind:this={headEl}>
			<div class="rule-h"><span class="lbl">Packet</span><span class="r"><span class="count">{snapshot ? `${count(snapshot.total_tokens)} tokens` : '—'}</span></span></div>
			<div class="field-l">
				<label class="lbl" for="packet-attempt">Attempt</label>
				<select class="sel" id="packet-attempt" value={selected?.id ?? ''} onchange={(event) => (chosenId = event.currentTarget.value)} disabled={attempts.length === 1}>
					{#each attempts as attempt, at (attempt.id)}
						<option value={attempt.id}>Attempt {at + 1} · {attemptWord(attempt)} · {attempt.packet_snapshot ? `${count(attempt.packet_snapshot.total_tokens)} tokens` : 'no receipt'}</option>
					{/each}
				</select>
			</div>

			{#if selected && snapshot}
				<dl class="kv">
					<dt>Total</dt><dd class="mono">{count(snapshot.total_tokens)}</dd>
					<dt>Measured</dt><dd>{shared?.phase ?? 'mixed'}</dd>
					<dt>Source</dt><dd class="ell" title={shared?.source ? `${sourceSentence(shared.source)} · ${shared.provenance}` : undefined}>{shared?.source ?? 'mixed'}</dd>
				</dl>
				{#if !agree}
					<p class="dk-note"><span class="state" data-tone="failed">Disagree</span> Sections sum to a different figure than the {count(snapshot.total_tokens)} recorded; neither replaces the other.</p>
				{/if}

				{#if historical && attempts.length > 1}
					<p class="dk-note">Packet comparison is served only for the run as it stands.</p>
				{:else if comparable.length > 0}
					<div class="field-l">
						<label class="lbl" for="packet-compare">Compare with</label>
						<select class="sel" id="packet-compare" bind:value={compareWithId} onchange={(event) => void armWith((event.currentTarget as HTMLSelectElement).value)}>
							<option value="">None</option>
							{#each comparable as attempt (attempt.id)}
								<option value={attempt.id}>Attempt {attemptNumber(attempt)} · {attemptWord(attempt)} · {count(attempt.packet_snapshot?.total_tokens ?? 0)} tokens</option>
							{/each}
						</select>
					</div>
				{:else if attempts.length > 1}
					<p class="dk-note">No other attempt carries a section receipt to compare against.</p>
				{/if}

				{#if !historical && pair && diff}
					<AsyncField resource={diff} reading="the comparison" onretry={() => void diff?.load()}>
						{#snippet children(data: PacketDiff)}
							<div class="rule-h sp"><span class="lbl">Attempt {attemptNumber(pair.before)} → {attemptNumber(pair.after)}</span><span class="r"><span class="count">{data.token_delta === 0 ? 'balanced' : `${data.token_delta > 0 ? '+' : ''}${count(data.token_delta)}`}</span></span></div>
							<dl class="kv">
								<dt>Changed</dt><dd class="paths">{#each diffList(data.changed_sections).items as name (name)}<code>{name}</code>{/each}</dd>
								<dt>Added</dt><dd class="paths">{#each diffList(data.added_sections).items as name (name)}<code>{name}</code>{/each}</dd>
								<dt>Removed</dt><dd class="paths">{#each diffList(data.removed_sections).items as name (name)}<code>{name}</code>{/each}</dd>
							</dl>
							<p class="dk-note">{count(data.before_tokens)} → {count(data.after_tokens)} tokens. {data.derivation}</p>
						{/snippet}
					</AsyncField>
				{/if}
			{/if}
		</div>

		<div class="main">
			{#if selected && snapshot}
				<div class="rows" bind:this={listEl}>
					{#each sectionRows(snapshot) as section, sectionIndex (section.name)}
						{#if memory && sectionIndex === memoryStart}
							<div class="fence">
								<div class="rule-h"><span class="lbl">Memory · {selected?.memory_mode}</span><span class="r"><span class="count">{memoryPair === null ? 'unread' : memoryPair.length === 0 ? 'no leaves' : `${memoryPair.length} ${memoryPair.length === 1 ? 'leaf' : 'leaves'} · ${count(memory.tokens)}`}</span></span></div>
								<p class="dk-note">{MODE_SENTENCES[selected?.memory_mode ?? 'legacy']}</p>
								{#if memoryPair && memoryPair.length === 0}<p class="dk-note">{NO_LEAVES}</p>{/if}
								{#if memoryPair === null}<p class="dk-note">{PAIRING_REFUSED}</p>{/if}
								{#if currentClass && selected && classDisagreement(currentClass, selected.memory_mode)}<p class="dk-note">{classDisagreement(currentClass, selected.memory_mode)}</p>{/if}
							</div>
						{:else if !memory && sectionIndex === 0 && snapshot.sections.some((item) => item.name === 'memory' || item.name.startsWith('memory_'))}
							<p class="dk-note">The memory sections are not contiguous in this packet, so what memory cost is not stated here.</p>
						{/if}
						{@const body = sectionBody(section.value)}
						{@const marker = diff?.data ? diffVerdict(diff.data, section.name) : null}
						<div class="row">
							<div class="rule-h">
								<span class="rn"><code>{section.name}</code></span>
								<span class="r">{#if marker}<span class="chip">{marker}</span>{/if} <span class="count">{sectionTokens(section)}</span></span>
							</div>
							{#if body.kind === 'string'}
								<p class="val" class:clamped={!expanded && body.text.length > CLAMP_AT}>{body.text}</p>
								{#if !expanded && body.text.length > CLAMP_AT}<p class="dk-note">Clamped — maximize the dock to read the whole.</p>{/if}
							{:else if body.kind === 'null'}
								<p class="dk-note">No value — compiled and carries nothing.</p>
							{:else if body.kind === 'list'}
								<ul class="dk-list">
									{#each expanded ? body.items : body.items.slice(0, CAP) as item, at (at)}
										<li>{#if codeLike(item)}<code>{item}</code>{:else}{item}{/if}</li>
									{/each}
								</ul>
								{#if !expanded && body.items.length > CAP}<p class="dk-note">{count(body.items.length - CAP)} more — maximize the dock.</p>{/if}
							{:else if body.kind === 'object'}
								<dl class="kv">
									{#each Object.entries(body.value) as [key, value] (key)}<dt>{key}</dt><dd class="ell">{objectCell(value)}</dd>{/each}
								</dl>
							{:else if body.kind === 'objectList'}
								{#each expanded ? body.items : body.items.slice(0, CAP) as item, at (at)}
									<dl class="kv nested">
										{#each Object.entries(item) as [key, value] (key)}
											<dt>{key}</dt>
											<dd class="paths">{#if Array.isArray(value)}{#each value as entry (JSON.stringify(entry))}<code>{objectCell(entry)}</code>{/each}{:else}{objectCell(value)}{/if}</dd>
										{/each}
									</dl>
								{/each}
								{#if !expanded && body.items.length > CAP}<p class="dk-note">{count(body.items.length - CAP)} more — maximize the dock.</p>{/if}
							{:else if body.kind === 'empty'}
								<p class="dk-note">None declared</p>
							{:else}
								<p class="dk-note">Shape not known to this build; shown as served.</p>
								<pre>{body.json}</pre>
							{/if}
						</div>
						{#if memory && sectionIndex === memoryEnd}
							<div class="fence">
								{#each memoryPair ?? [] as leaf (leaf.id)}<p class="dk-note"><code>{leaf.id}@{leaf.version}</code> — carried into this packet</p>{/each}
								{#if memoryReceipts.length > 0}
									<div class="rule-h sp"><span class="lbl">Drawn since</span></div>
									{#each expanded ? memoryReceipts : memoryReceipts.slice(0, CAP) as row (row.receipt.at + row.receipt.operation)}
										<div class="receipt">
											<div class="rule-h"><span class="lbl">{row.receipt.operation}</span><span class="r"><span class="count">{count(row.receipt.tokens)} tokens</span></span></div>
											<p><code>{receiptPairs(row.receipt).join(' · ') || 'no leaves named'}</code></p>
											<p class="dk-note">{row.receipt.provenance} · {shownAt(row.receipt.at)} · {row.runScoped ? 'run-scoped; a pull receipt names no attempt' : `attempt ${attemptNumber(selected!)}`}</p>
											{#if row.receipt.operation === 'inline' && selected?.memory_mode === 'legacy'}<p class="dk-note">Recorded as inline; this attempt's mode was legacy.</p>{/if}
										</div>
									{/each}
									{#if !expanded && memoryReceipts.length > CAP}<p class="dk-note">{count(memoryReceipts.length - CAP)} more — maximize the dock.</p>{/if}
									<p class="dk-note">{RECEIPT_ESTIMATES} {DELIVERY_VS_PACKET}</p>
								{/if}
								{#if memoryPair && memoryPair.length > 0}
									<div class="rule-h sp"><span class="lbl">Still true?</span></div>
									{#if memoryStatus?.phase === 'loading'}<p class="dk-note" aria-busy="true">Reading whether these leaves are still current…</p>
									{:else if memoryStatus?.phase === 'error' && !memoryStatus.data}<p class="dk-note">{STATUS_UNREAD} <Button icon="refresh-cw" small onclick={() => void memoryStatus?.load()}>Read again</Button></p>
									{:else if memoryStatus?.data}
										{#if memoryStatus.stale}<p class="dk-note">Last confirmed earlier; receipts may be newer. <Button icon="refresh-cw" small onclick={() => void memoryStatus?.load()}>Read again</Button></p>{/if}
										<p class="dk-note">{CARRIED_HEADER}</p>
										{#each memoryPair as leaf (leaf.id)}{@const verdict = currentVerdict(memoryStatus.data, leaf.id, leaf.version)}{#if verdict}<p class="dk-note">{verdict.line}</p>{/if}{/each}
									{:else}<p class="dk-note">{STATUS_UNREAD} <Button icon="refresh-cw" small onclick={() => void memoryStatus?.load()}>Read again</Button></p>{/if}
								{/if}
							</div>
						{/if}
					{/each}
				</div>
			{:else if selected}
				<p class="dk-note">This attempt recorded {count(selected.packet_tokens)} tokens of packet but no section receipt, so what was in it cannot be read.</p>
			{/if}
		</div>
	{/if}
</section>

<style>
	.pk { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(0, 2.2fr); gap: 32px; align-items: start; }
	.full { grid-column: 1 / -1; }
	.side { display: grid; gap: 14px; min-width: 0; }
	.side .rule-h { margin-bottom: 0; }
	.main { min-width: 0; }
	.sp { margin-top: 14px; }
	.rows { display: grid; gap: 14px; }
	.row .rule-h { margin-bottom: 6px; }
	.rn { color: var(--tx2); }
	.val { color: var(--tx2); overflow-wrap: anywhere; max-width: 80ch; }
	.val.clamped { display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.fence { border-left: 1px solid var(--ln2); padding: 2px 0 2px 12px; display: grid; gap: 6px; }
	.fence .rule-h { margin-bottom: 4px; }
	.receipt { padding: 6px 0; display: grid; gap: 2px; }
	.receipt .rule-h { margin-bottom: 2px; }
	.paths { flex-wrap: wrap; }
	.paths :global(code), .fence code { overflow-wrap: anywhere; }
	.ell { overflow-wrap: anywhere; }
	.nested { margin-bottom: 8px; }
	pre { font: 400 12px/1.5 var(--f-mono); color: var(--tx2); white-space: pre-wrap; overflow-wrap: anywhere; }
	@media (max-width: 1100px) { .pk { grid-template-columns: minmax(0, 1fr); gap: 20px; } }
</style>
