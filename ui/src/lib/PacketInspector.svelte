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
		const box = listEl?.closest('.body');
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

<section bind:this={rootEl}>
	<p class="label rule-label" bind:this={headEl}>
		<span>Packet</span><span class="rule"></span>
		<span class="member" data-state={attempts.length === 0 ? 'slack' : 'seated'}>
			{attempts.length === 0
				? 'No attempt yet'
				: snapshot
					? `${count(snapshot.total_tokens)} tokens`
					: 'None recorded'}
		</span>
	</p>

	{#if attempts.length === 0}
		<!-- Said once, and only the packet-specific half: the Attempts section
		     twelve pixels up already says there is no worktree, pane or usage. -->
		<p class="prose quiet">
			A packet is compiled when an attempt is reserved, so there is nothing received
			to read yet.
		</p>
	{:else}
		{#if attempts.length > 1}
			<p class="field">
				<label class="label" for="packet-attempt">Attempt</label>
				<span class="pick">
					<select
						class="plate"
						id="packet-attempt"
						bind:value={chosenId}
					>
						{#each attempts as attempt, at (attempt.id)}
							<option value={attempt.id}>
								Attempt {at + 1} · {attemptWord(attempt)} ·
								{attempt.packet_snapshot
									? `${count(attempt.packet_snapshot.total_tokens)} tokens`
									: 'no packet receipt'}
							</option>
						{/each}
					</select>
				</span>
			</p>
		{:else if attempts[0]}
			<p class="prose quiet">Reading Attempt 1 — the only attempt this member has.</p>
		{/if}

		{#if selected && snapshot}
			<dl class="readout plate band">
				<div>
					<dt class="label">Total</dt>
					<dd class="value">{count(snapshot.total_tokens)}</dd>
					<p class="gloss">the sum of every section below; nothing is padded</p>
				</div>
				<div>
					<dt class="label">Measured</dt>
					<dd class="value member" data-state="balanced">{shared?.phase ?? 'mixed'}</dd>
					<p class="gloss">before the attempt ran, on the packet as compiled</p>
				</div>
				<div>
					<dt class="label">Source</dt>
					<dd class="value member" data-state="balanced">
						{shared?.source ?? 'mixed'}
					</dd>
					<p class="gloss">
						{#if shared?.source}
							{sourceSentence(shared.source)} · {shared.provenance}
						{:else}
							the sections below disagree on how they were measured
						{/if}
					</p>
				</div>
			</dl>
			<p class="prose quiet">
				These sections are the packet as it was received — frozen exactly as this plan
				version froze them when the attempt was reserved. The current brief, contract
				and assets live in the plan and the Library and may have moved since; nothing
				here re-reads them.
			</p>
			<p class="prose quiet">
				Preflight measures what was sent. Nothing had been generated yet, so these
				sections carry no output figure — that is not an output of zero. What this
				attempt actually spent is a separate figure, reported by its checkpoint and
				read in the attempt above.
			</p>
			<p class="prose quiet">
				{#if shared?.source}
					Every section below was measured this way; one measured differently says so on
					its own row. Each figure is what that section added to the packet in this order.
				{:else}
					The sections below were not all measured the same way; each one that differs
					says so on its own row.
				{/if}
			</p>
			{#if !agree}
				<p class="prose quiet member" data-state="failed">
					The sections below sum to a different figure than the {count(snapshot.total_tokens)}
					recorded above. Neither figure has been replaced by the other: the total is the
					record, and the disagreement means the daemon that wrote this is newer or broken.
				</p>
			{/if}

			{#if historical && attempts.length > 1}
				<p class="prose quiet">Comparing two packets is served only for the run as it stands. Each attempt's own recorded packet is here; the comparison is not.</p>
			{:else if snapshot && comparable.length > 0}
				<p class="field">
					<label class="label" for="packet-compare">Compare with</label>
					<span class="pick">
						<select class="plate" id="packet-compare" bind:value={compareWithId} onchange={(event) => void armWith((event.currentTarget as HTMLSelectElement).value)}>
							<option value="">None</option>
							{#each comparable as attempt (attempt.id)}
								<option value={attempt.id}>
									Attempt {attemptNumber(attempt)} · {attemptWord(attempt)} ·
									{count(attempt.packet_snapshot?.total_tokens ?? 0)} tokens
								</option>
							{/each}
						</select>
					</span>
				</p>
			{:else if snapshot && attempts.length > 1}
				<p class="prose quiet">
					No other attempt of this member carries a section receipt, so there is
					nothing to compare against.
				</p>
			{/if}

			{#if !historical && pair && diff}
				<AsyncField resource={diff} reading="the comparison" onretry={() => void diff?.load()}>
					{#snippet children(data: PacketDiff)}
						{@const beforeNumber = attemptNumber(pair.before)}
						{@const afterNumber = attemptNumber(pair.after)}
						<div class="diffplate">
							<p class="label rule-label">
								<span>Comparison</span><span class="rule"></span>
								<span
									class="member"
									data-state={data.token_delta === 0 ? 'seated' : 'balanced'}
								>
									{data.token_delta === 0
										? 'balanced'
										: `${data.token_delta > 0 ? '+' : ''}${count(data.token_delta)} tokens`}
								</span>
							</p>
							<p class="prose">
								Attempt {beforeNumber} → Attempt {afterNumber}:
								{count(data.before_tokens)} → {count(data.after_tokens)}
							</p>
							<ol class="compare">
								<li>
									<span class="diffname">Changed</span>
									<span class="paths">
										{#each diffList(data.changed_sections).items as name (name)}<code>{name}</code>{/each}
									</span>
								</li>
								<li>
									<span class="diffname">Added</span>
									<span class="paths">
										{#each diffList(data.added_sections).items as name (name)}<code>{name}</code>{/each}
									</span>
								</li>
								<li>
									<span class="diffname">Removed</span>
									<span class="paths">
										{#each diffList(data.removed_sections).items as name (name)}<code>{name}</code>{/each}
									</span>
								</li>
							</ol>
							<p class="prose quiet">
								Sections are compared whole, by value. A section marked changed differs
								somewhere inside it; where is not computed.
							</p>
							<p class="prose quiet">{data.derivation}</p>
							<p class="prose quiet">
								Provenance of the comparison: {data.provenance.join(' · ')}
							</p>
						</div>
					{/snippet}
				</AsyncField>
			{/if}

			<!-- The sections, in exactly the order the daemon serves them. The
			     list is the packet's own record; the comparison annotates it and
			     never injects a ghost row into it. -->
			<div class="rows" bind:this={listEl}>
				{#each sectionRows(snapshot) as section, sectionIndex (section.name)}
					{#if memory && sectionIndex === memoryStart}
						<div class="memory-fence-head">
							<p class="label rule-label"><span>Memory · {selected?.memory_mode}</span><span class="rule"></span><span class="member" data-state={memoryPair && memoryPair.length > 0 ? 'seated' : 'slack'}>{memoryPair === null ? 'unread' : memoryPair.length === 0 ? 'no leaves' : `${memoryPair.length} ${memoryPair.length === 1 ? 'leaf' : 'leaves'} · ${count(memory.tokens)}`}</span></p>
							<p class="prose quiet">{MODE_SENTENCES[selected?.memory_mode ?? 'legacy']}</p>
							{#if memoryPair && memoryPair.length === 0}<p class="prose quiet">{NO_LEAVES}</p>{/if}
							{#if memoryPair === null}<p class="prose quiet member" data-state="slack">{PAIRING_REFUSED}</p>{/if}
							{#if currentClass && selected && classDisagreement(currentClass, selected.memory_mode)}<p class="prose quiet">{classDisagreement(currentClass, selected.memory_mode)}</p>{/if}
						</div>
					{:else if !memory && sectionIndex === 0 && snapshot.sections.some((item) => item.name === 'memory' || item.name.startsWith('memory_'))}
						<p class="prose quiet member" data-state="slack">The memory sections are not contiguous in this packet, so what memory cost is not stated here.</p>
					{/if}
					{@const body = sectionBody(section.value)}
					{@const marker =
						diff?.data ? diffVerdict(diff.data, section.name) : null}
					<div class="row plate">
						<p class="label rule-label">
							<span class="rowname"><code>{section.name}</code></span><span class="rule"></span>
							{#if marker}
								<span class="tag member" data-state="balanced">{marker}</span>
							{/if}
							<span class="member" data-state="seated">{sectionTokens(section)}</span>
						</p>
						{#if body.kind === 'string'}
							<p class="prose value" class:clamped={!expanded && body.text.length > CLAMP_AT}
								>{body.text}</p
							>
							{#if !expanded && body.text.length > CLAMP_AT}
								<p class="prose quiet foot">Clamped at sheet width — expand to read the whole.</p>
							{/if}
						{:else if body.kind === 'null'}
							<p class="prose quiet">No value — the section was compiled and carries nothing.</p>
						{:else if body.kind === 'list'}
							<ol class="versions">
								{#each expanded ? body.items : body.items.slice(0, CAP) as item, at (at)}
									<li class="version-row member" data-state="seated">
										<span class="ring" aria-hidden="true"></span>
										<div class="version-text">
											{#if codeLike(item)}<code>{item}</code>{:else}<p class="prose">{item}</p>{/if}
										</div>
									</li>
								{/each}
							</ol>
							{#if !expanded && body.items.length > CAP}
								<p class="prose quiet foot">
									{count(body.items.length - CAP)} more — expand to read them.
								</p>
							{/if}
						{:else if body.kind === 'object'}
							<dl class="readout plate">
								{#each Object.entries(body.value) as [key, value] (key)}
									<div>
										<dt class="label">{key}</dt>
										<dd class="value">{objectCell(value)}</dd>
									</div>
								{/each}
							</dl>
						{:else if body.kind === 'objectList'}
							{#each expanded ? body.items : body.items.slice(0, CAP) as item, at (at)}
								<dl class="readout plate nested">
									{#each Object.entries(item) as [key, value] (key)}
										<div class={Array.isArray(value) ? 'wide' : ''}>
											<dt class="label">{key}</dt>
											<dd class="paths">
												{#if Array.isArray(value)}
													{#each value as entry (JSON.stringify(entry))}
														<code>{objectCell(entry)}</code>
													{/each}
												{:else}
													{objectCell(value)}
												{/if}
											</dd>
										</div>
									{/each}
								</dl>
							{/each}
							{#if !expanded && body.items.length > CAP}
								<p class="prose quiet foot">
									{count(body.items.length - CAP)} more — expand to read them.
								</p>
							{/if}
						{:else if body.kind === 'empty'}
							<p class="prose quiet">None declared</p>
						{:else}
							<p class="prose quiet">
								This section's shape is not one this build knows how to lay out, so it is
								shown as the daemon serves it.
							</p>
							<pre>{body.json}</pre>
						{/if}
					</div>
					{#if memory && sectionIndex === memoryEnd}
						<div class="memory-fence-foot">
							{#each memoryPair ?? [] as leaf (leaf.id)}
								<p class="prose quiet"><code>{leaf.id}@{leaf.version}</code> — carried into this packet</p>
							{/each}
							{#if memoryReceipts.length > 0}
								<p class="label rule-label"><span>Drawn since</span><span class="rule"></span></p>
								{#each expanded ? memoryReceipts : memoryReceipts.slice(0, CAP) as row (row.receipt.at + row.receipt.operation)}
									<div class="memory-receipt plate">
										<p class="label rule-label"><span>{row.receipt.operation}</span><span class="rule"></span><span class="member" data-state="seated">{count(row.receipt.tokens)} tokens</span></p>
										<p class="prose"><code>{receiptPairs(row.receipt).join(' · ') || 'no leaves named'}</code></p>
										<p class="prose quiet">{row.receipt.provenance} · {shownAt(row.receipt.at)} · {row.runScoped ? 'recorded against the run; a pull receipt names no attempt' : `attempt ${attemptNumber(selected!)}`}</p>
										{#if row.receipt.operation === 'inline' && selected?.memory_mode === 'legacy'}<p class="prose quiet">Recorded as inline; this attempt's mode was legacy.</p>{/if}
									</div>
								{/each}
								{#if !expanded && memoryReceipts.length > CAP}<p class="prose quiet foot">{count(memoryReceipts.length - CAP)} more — expand to read them.</p>{/if}
								<p class="prose quiet">{RECEIPT_ESTIMATES}</p>
								<p class="prose quiet">{DELIVERY_VS_PACKET}</p>
							{/if}
							{#if memoryPair && memoryPair.length > 0}
								<p class="label rule-label"><span>Still true?</span><span class="rule"></span></p>
								{#if memoryStatus?.phase === 'loading'}<p class="prose quiet" aria-busy="true">Reading whether these leaves are still current…</p>
								{:else if memoryStatus?.phase === 'error' && !memoryStatus.data}<p class="prose quiet member" data-state="slack">{STATUS_UNREAD} <button class="act" type="button" onclick={() => void memoryStatus?.load()}>Read again</button></p>
								{:else if memoryStatus?.data}
									{#if memoryStatus.stale}<p class="prose quiet member" data-state="slack">These current-validity values were last confirmed earlier; receipts on this screen may be newer. <button class="act" type="button" onclick={() => void memoryStatus?.load()}>Read again</button></p>{/if}
									<p class="prose quiet">{CARRIED_HEADER}</p>
									{#each memoryPair as leaf (leaf.id)}{@const verdict = currentVerdict(memoryStatus.data, leaf.id, leaf.version)}{#if verdict}<p class="prose quiet member" data-state="slack">{verdict.line}</p>{/if}{/each}
								{:else}<p class="prose quiet member" data-state="slack">{STATUS_UNREAD} <button class="act" type="button" onclick={() => void memoryStatus?.load()}>Read again</button></p>{/if}
							{/if}
						</div>
					{/if}
				{/each}
			</div>
		{:else if selected}
			<p class="prose quiet member" data-state="slack">
				This attempt recorded {count(selected.packet_tokens)} tokens of packet, but no
				section receipt was persisted with it, so what was in it cannot be read. The
				total is the fold's own figure and is not derived from sections.
			</p>
		{/if}
	{/if}

	<p class="prose quiet foot">
		The run's token budget is accounted above on the page behind this sheet.
	</p>
</section>

<style>
	/* Svelte scopes the drawer's section rule to its own markup, so restate the
	   same rhythm here; nothing new is invented, and nothing here moves. */
	section {
		margin-top: 1.75rem;
	}
	.memory-fence-head,
	.memory-fence-foot {
		border-left: 1px solid var(--rule-strong);
		border-right: 1px solid var(--rule-strong);
		padding: 0.75rem 0.875rem;
	}
	.memory-fence-head { border-top: 1px solid var(--rule-strong); }
	.memory-fence-foot { border-bottom: 1px solid var(--rule-strong); }
	.memory-fence-head .rule-label,
	.memory-fence-foot .rule-label { margin-bottom: 0.6rem; }
	.memory-receipt { margin: 0.75rem 0; padding: 0.75rem; }
	.memory-receipt .rule-label { margin-bottom: 0.5rem; }
	.memory-receipt .prose { overflow-wrap: anywhere; }
	.memory-fence-foot code { overflow-wrap: anywhere; }

	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin: 0 0 0.9rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.rule-label > span:last-of-type {
		flex: none;
		max-width: 55%;
		overflow-wrap: anywhere;
		text-align: right;
	}
	.member[data-state='slack'] {
		color: var(--ink-2);
	}
	.member[data-state='failed'] {
		color: var(--red);
	}
	.prose {
		margin: 0;
		max-width: 68ch;
		color: var(--ink-2);
	}
	.quiet {
		font-size: 0.8125rem;
	}
	.prose + .prose {
		margin-top: 0.55rem;
	}
	.foot {
		margin-top: 0.55rem;
	}
	section > .foot {
		margin-top: 0.9rem;
	}

	/* --- the snapshot band and the section rows, as readouts --------------- */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		margin: 0.9rem 0 0;
		background: var(--rule);
		border: 1px solid var(--rule);
		overflow: hidden;
	}
	.readout > div {
		flex: 1 1 10rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.7rem 0.9rem;
	}
	.readout .wide {
		flex-basis: 100%;
	}
	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		color: var(--member-ink, var(--ink));
	}
	dd.value {
		overflow-wrap: anywhere;
	}
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	code {
		background: var(--ground);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
		overflow-wrap: anywhere;
	}

	/* --- the chooser, as the interventions build it ------------------------- */
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin: 0.9rem 0 0;
		max-width: none;
	}
	.pick {
		position: relative;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		line-height: 1.5;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.pick::after {
		content: '';
		position: absolute;
		right: 0.85rem;
		top: calc(50% - 0.35em);
		width: 0.4em;
		height: 0.4em;
		border-right: 1px solid var(--ink-2);
		border-bottom: 1px solid var(--ink-2);
		transform: rotate(45deg);
		pointer-events: none;
	}
	select {
		--cut: 10px;
		font: inherit;
		width: 100%;
		box-sizing: border-box;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
		appearance: none;
		padding-right: 2.25rem;
	}
	select:focus-visible {
		border-color: var(--red);
	}

	/* --- section rows ------------------------------------------------------- */
	.rows {
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
		margin-top: 0.9rem;
	}
	.row {
		--cut: 12px;
		border: 1px solid var(--rule);
		padding: 0.9rem 1rem 1rem;
		background: var(--plate);
	}
	.rows .row .rule-label {
		margin-bottom: 0.75rem;
	}
	/* The section name is the record's own spelling; the ruled label's caps
	   belong to labels, not to record vocabulary. */
	.rowname code {
		text-transform: none;
		letter-spacing: 0.02em;
	}
	.tag {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		flex: none;
	}
	.clamped {
		display: -webkit-box;
		-webkit-line-clamp: 5;
		line-clamp: 5;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.value:not(.clamped) {
		overflow-wrap: anywhere;
	}
	pre {
		margin: 0.55rem 0 0;
		max-width: 68ch;
		overflow-x: auto;
		background: var(--ground);
		border: 1px solid var(--rule);
		padding: 0.55rem 0.7rem;
		font-size: 0.75rem;
		line-height: 1.5;
	}
	.nested {
		margin-top: 0;
	}
	.nested + .nested,
	.nested + .prose {
		margin-top: 0.55rem;
	}

	/* The ruled chain, as the redirects and failures draw it. */
	.versions {
		position: relative;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.versions::before {
		content: '';
		position: absolute;
		left: 5px;
		top: 0.5rem;
		bottom: -0.55rem;
		width: 1px;
		background: var(--member-line);
	}
	.version-row {
		position: relative;
		display: grid;
		grid-template-columns: 11px minmax(0, 1fr);
		gap: 0.55rem;
		padding: 0.4rem 0 0.6rem;
		color: var(--ink-2);
	}
	.version-row .ring {
		align-self: start;
		margin-top: 0.3rem;
		width: 11px;
		height: 11px;
		border: 1.5px solid var(--member-ink);
		border-radius: 50%;
		background: var(--plate);
	}
	.version-row[data-state='seated'] .ring {
		background: var(--seat);
	}
	.version-text {
		min-width: 0;
	}

	/* --- the comparison ----------------------------------------------------- */
	.diffplate {
		margin-top: 0.9rem;
		border: 1px solid var(--rule);
		padding: 0.9rem 1rem 1rem;
		background: var(--plate);
	}
	.diffplate .rule-label {
		margin-bottom: 0.75rem;
	}
	.compare {
		list-style: none;
		margin: 0.9rem 0 0;
		padding: 0;
	}
	.compare li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem 0.75rem;
		padding: 0.4rem 0;
		border-bottom: 1px solid var(--rule);
	}
	.compare .paths {
		margin: 0;
	}
	.diffname {
		flex: none;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.diffplate .paths {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.3rem 0.45rem;
		margin: 0 0 0 0.6rem;
	}
	.diffplate .paths code {
		overflow-wrap: normal;
		white-space: nowrap;
	}
	.act {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink);
		background: transparent;
		border: 1px solid var(--rule-strong);
		padding: 0.35rem 0.85rem;
		white-space: nowrap;
		cursor: pointer;
	}
	.act:hover {
		border-color: var(--red);
		color: var(--red);
	}

	@media (max-width: 60rem) {
		select {
			max-width: none;
		}
	}
</style>
