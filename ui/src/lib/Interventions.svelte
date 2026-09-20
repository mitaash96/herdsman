<script lang="ts">
	/*
	  Unit R6 — the interventions, inside R2's drawer. Its direction contract
	  lives in `.impeccable/surfaces/ui-src-lib-interventions-svelte.md`.

	  It sits directly under the blocking statement because that is the reading
	  order an operator actually has: what is wrong, then what I can do about
	  it. Everything below it — the checkpoint, the brief, the contract, the
	  attempts — is evidence for the choice made here.

	  Three rules this surface is built on and a later unit should not soften:

	  1. Nothing is armed and confirmed in one press. Every write states its
	     consequence first, and for the three that can strand work that
	     consequence is read from the daemon rather than recomputed here.
	  2. An unavailable action is a sentence naming the rule, never a greyed
	     control. The rule is the only thing that tells you what would change it.
	  3. A refusal is reported as a refusal, in the daemon's own words. No write
	     on this surface is ever presented as having landed when it did not.

	  Deliberately absent, each named on screen where an operator would look:
	  pause, resume, cancel and recovery reconciliation (R9); recalibration
	  (R10); curating leaves, the memory shelf and batched attention belong to the Library.

	  A reassignment picks its harness and model from the Kitchen's catalog
	  (`GET /kitchen`), read when the action is armed, exactly as the downstream
	  impact is. Neither half is ever typed: the identity of a harness that
	  exists is chosen, or the action says why there is nothing to choose.
	*/
	import {
		daemon,
		DaemonError,
		type DownstreamImpact,
		type Initiative,
		type MemoryLeaf,
		type MemoryStatus,
		type Kitchen,
		type Plan
	} from './daemon';
	import type { Resource } from './resource.svelte';
	import {
		ACTION_GLOSS,
		ACTION_WORD,
		assignmentWord,
		availability,
		checkpointChoices,
		currentBriefVersion,
		disruptive,
		heldGroups,
		impactLines,
		liveAttempt,
		sameAssignment,
		type Action
	} from './interventions';

	let {
		planId,
		id,
		initiative,
		plan,
		approved,
		onchanged,
		onreview,
		memoryStatus
	}: {
		planId: string;
		id: string;
		/** From the folded plan. `null` while that read has not answered. */
		initiative: Initiative | null;
		/** The whole fold, for the plan-wide checkpoint targets a redirect accepts. */
		plan: Plan | null;
		/** Whether this revision is approved. Nothing may start until it is. */
		approved: boolean;
		/** A write landed; the page re-reads everything it moved. */
		onchanged: () => void;
		/** Open the checkpoint reader (R4) on this member's recorded evidence. */
		onreview: () => void;
		memoryStatus: Resource<MemoryStatus> | null;
	} = $props();

	const offers = $derived.by(() => {
		if (!initiative) return [];
		return availability(initiative, approved).map((offer) =>
			offer.action !== 'answer-memory'
				? offer
				: memoryStatus?.data && !memoryStatus.stale
					? {
							...offer,
							available: memoryStatus.data.leaves.some((leaf) => leaf.status === 'active'),
							refused: memoryStatus.data.leaves.some((leaf) => leaf.status === 'active')
								? null
								: 'Nothing in this run’s memory can answer for you yet.'
						}
					: {
							...offer,
							available: false,
							refused: 'The memory status read has not answered, so the subject list is unread. Read it before choosing a leaf.'
						}
		);
	});
	const open = $derived(offers.filter((offer) => offer.available));
	const answerable = $derived(
		!memoryStatus?.stale ? memoryStatus?.data?.leaves.filter((leaf) => leaf.status === 'active') ?? [] : []
	);
	const chosenMemory = $derived(answerable.find((leaf) => leaf.id === memoryChoice) ?? null);
	const held = $derived(heldGroups(offers));
	const live = $derived(initiative ? liveAttempt(initiative) : null);
	const choices = $derived(
		initiative && plan ? checkpointChoices(plan.initiatives, id) : []
	);

	/* --- arming --------------------------------------------------------------
	   Three of the six can strand work that already ran, so arming a disruptive
	   action reads `GET …/impact` — the daemon's own projection, which is also
	   what the write routes return under `preview`. The sentence shown before
	   confirming and the rule applied on confirming therefore come from one
	   place, and a read that fails says so rather than reporting no impact. */
	type Armed = { action: Action; actionId: string };
	type Sending = { phase: 'idle' } | { phase: 'sending' } | { phase: 'done' | 'failed'; message: string };

	let armed = $state<Armed | null>(null);
	let sending = $state<Sending>({ phase: 'idle' });
	let confirmEl = $state<HTMLButtonElement | null>(null);

	type Reading =
		| { phase: 'none' }
		| { phase: 'reading' }
		| { phase: 'read'; impact: DownstreamImpact }
		| { phase: 'failed'; message: string };
	let reading = $state<Reading>({ phase: 'none' });

	/* The catalog a reassignment chooses from. Read on arming, like the impact:
	   a harness and a model are the identity of things that exist, so they are
	   picked from the Kitchen's own inventory rather than typed. An unconfigured
	   Kitchen is not a failure — it is an empty catalog, and it says so in the
	   daemon's own blockers. */
	type Catalog =
		| { phase: 'none' }
		| { phase: 'reading' }
		| { phase: 'read'; kitchen: Kitchen }
		| { phase: 'failed'; message: string };
	let catalog = $state<Catalog>({ phase: 'none' });
	const harnesses = $derived(
		catalog.phase === 'read' ? catalog.kitchen.adapters.map((adapter) => adapter.name) : []
	);
	/** The chosen model's tier, when the catalog resolves one. Never guessed. */
	const chosenTier = $derived(
		catalog.phase === 'read'
			? (catalog.kitchen.models.find(
					(entry) => entry.harness === harness && entry.model === model
				)?.tier ?? null)
			: null
	);
	const models = $derived(
		catalog.phase === 'read'
			? catalog.kitchen.models.filter((entry) => entry.harness === harness)
			: []
	);

	/* Inputs. Held across arming so a long brief is not retyped after a refusal
	   — the refusal is the thing to fix, not the form. */
	let harness = $state('');
	let model = $state('');
	let reason = $state('');
	let brief = $state('');
	let checkpointId = $state('');
	let target = $state<'brief' | 'checkpoint'>('brief');
	let nudgeText = $state('');
	let groundTruth = $state(false);
	let subject = $state('');
	let answerText = $state('');
	let memoryChoice = $state('');
	let memoryOutcome = $state<{ leaf: MemoryLeaf | null } | null>(null);

	/* A different member is a different question, and only a different member.
	   Keying this on the initiative's *state* would wipe the operator's own
	   outcome on the re-read their write just triggered — R3's bug and R4's,
	   arrived at a third time. The outcome outlives the state change that
	   caused it. */
	let asked = $state<string | null>(null);
	$effect(() => {
		if (asked === id) return;
		asked = id;
		armed = null;
		sending = { phase: 'idle' };
		reading = { phase: 'none' };
		harness = '';
		model = '';
		reason = '';
		brief = '';
		checkpointId = '';
		target = 'brief';
		nudgeText = '';
		groundTruth = false;
		subject = '';
		answerText = '';
		memoryChoice = '';
		memoryOutcome = null;
	});

	function arm(action: Action) {
		armed = {
			action,
			/* The daemon's idempotency key for a retry. Minted once per arming, so
			   a doubted press cannot cost a second agent: the fold answers the
			   repeat from the record it already has. */
			actionId: crypto.randomUUID()
		};
		sending = { phase: 'idle' };
		if (disruptive(action)) void readImpact();
		else reading = { phase: 'none' };
		if (action === 'reassign') void readCatalog();
		queueMicrotask(() => confirmEl?.focus());
	}

	function disarm() {
		armed = null;
		reading = { phase: 'none' };
		sending = { phase: 'idle' };
	}

	async function readImpact() {
		reading = { phase: 'reading' };
		try {
			reading = { phase: 'read', impact: await daemon.impact(planId, id) };
		} catch (cause) {
			reading = {
				phase: 'failed',
				message:
					cause instanceof DaemonError
						? cause.message
						: 'Something in this build failed while reading the downstream impact.'
			};
		}
	}

	async function readCatalog() {
		catalog = { phase: 'reading' };
		try {
			catalog = { phase: 'read', kitchen: await daemon.kitchen() };
		} catch (cause) {
			catalog = {
				phase: 'failed',
				message:
					cause instanceof DaemonError
						? cause.message
						: 'Something in this build failed while reading the harness catalog.'
			};
		}
	}

	/* --- what confirming needs ---------------------------------------------- */
	const pair = $derived(
		harness.length > 0 && model.length > 0 ? { harness, model } : null
	);
	const duplicatePair = $derived(
		initiative !== null && pair !== null && sameAssignment(initiative, harness, model)
	);
	const ready = $derived.by(() => {
		if (!armed || !initiative) return false;
		switch (armed.action) {
			case 'reassign':
				return pair !== null && !duplicatePair;
			case 'redirect':
				return target === 'brief' ? brief.trim().length > 0 : checkpointId.length > 0;
			case 'nudge':
				return nudgeText.trim().length > 0;
			case 'answer':
				return subject.trim().length > 0 && answerText.trim().length > 0;
			case 'answer-memory':
				return memoryChoice.length > 0 && answerable.some((leaf) => leaf.id === memoryChoice);
			default:
				return true;
		}
	});

	const lines = $derived.by(() => {
		if (!armed || !initiative) return [];
		if (armed.action === 'answer-memory') {
			const leaf = answerable.find((entry) => entry.id === memoryChoice);
			return leaf && live
				? [
						`The daemon delivers ${leaf.id}@${leaf.version}’s recorded claim to attempt ${live.id} on its subject. No turn from you and no model call — it is the record answering.`,
						'The daemon matches the subject against the record, not against a question. If the agent has not asked this, it receives the claim anyway.'
					]
				: [];
		}
		return impactLines(armed.action, {
			initiative,
			impact: reading.phase === 'read' ? reading.impact : null,
			assignment: pair,
			fromCheckpoint: target === 'checkpoint'
		});
	});

	/** The landed sentence per action — what happened, not that a button worked. */
	function landed(action: Action, detail: string): string {
		switch (action) {
			case 'retry':
				return `Retried. ${detail}`;
			case 'restart':
				return `The process was re-issued in ${detail}.`;
			case 'reassign':
				return `Reassigned. The next attempt runs on ${detail}.`;
			case 'redirect':
				return `Redirected. ${detail}`;
			case 'nudge':
				return 'Delivered to the running agent, and recorded.';
			case 'answer':
				return 'Answered, delivered to the running agent, and recorded against that subject.';
			case 'pause':
				return 'Held. New attempts stop here; any running attempt keeps going.';
			case 'unpause':
				return 'Released. Nothing starts because of this; choose Retry when you mean to start work.';
			case 'cancel':
				return 'Cancelled. The member is terminal and its worktree and evidence stay.';
			case 'answer-memory':
				return 'Answered from memory.';
		}
	}

	async function confirm() {
		if (!armed || !initiative || !ready || sending.phase === 'sending') return;
		const action = armed.action;
		const actionId = armed.actionId;
		sending = { phase: 'sending' };
		try {
			let detail = '';
			if (action === 'retry') {
				const result = await daemon.retry(planId, id, actionId);
				detail = result.checkpoint
					? `The attempt recorded checkpoint ${result.checkpoint.id}; its evidence is in the checkpoint section below.`
					: 'The attempt finished without recording a checkpoint — read its outcome in the attempt history below.';
			} else if (action === 'restart') {
				const result = await daemon.restart(planId, id);
				detail = result.pane_ref;
			} else if (action === 'reassign' && pair) {
				await daemon.reassign(planId, id, pair.harness, pair.model, reason.trim());
				detail = `${pair.harness}/${pair.model}`;
				harness = '';
				model = '';
			} else if (action === 'redirect') {
				const version = currentBriefVersion(initiative) + 1;
				await daemon.redirect(
					planId,
					id,
					target === 'brief' ? { brief: brief.trim() } : { checkpointId },
					reason.trim()
				);
				detail = `Brief version ${version} is recorded, and new attempts run on it.`;
				brief = '';
				checkpointId = '';
			} else if (action === 'nudge') {
				await daemon.nudge(planId, id, nudgeText.trim(), groundTruth);
				nudgeText = '';
			} else if (action === 'answer' && live) {
				await daemon.answer(planId, live.id, subject.trim(), answerText.trim());
				subject = '';
				answerText = '';
			} else if (action === 'pause') {
				await daemon.pause(planId, id, reason.trim(), actionId);
			} else if (action === 'unpause') {
				await daemon.unpause(planId, id, reason.trim(), actionId);
			} else if (action === 'cancel') {
				await daemon.cancel(planId, id, reason.trim(), actionId);
			} else if (action === 'answer-memory' && live) {
				memoryOutcome = await daemon.autoAnswer(planId, live.id, memoryChoice);
			}
			reason = '';
			sending = action === 'answer-memory' ? { phase: 'idle' } : { phase: 'done', message: landed(action, detail) };
			armed = null;
			reading = { phase: 'none' };
			onchanged();
		} catch (cause) {
			sending = {
				phase: 'failed',
				message:
					cause instanceof DaemonError
						? cause.message
						: `Something in this build failed while sending the ${ACTION_WORD[action].toLowerCase()}.`
			};
		}
	}
</script>

<section>
	<p class="label rule-label">
		<span>Interventions</span><span class="rule"></span>
		<span class="member" data-state={open.length === 0 ? 'slack' : 'balanced'}>
			{initiative ? `${open.length} of ${offers.length} available` : 'Unread'}
		</span>
	</p>

	{#if !initiative}
		<p class="prose quiet">
			The folded plan has not answered for <strong>{id}</strong>, so what can be done to it
			is unread — not an initiative nothing can be done to. Every intervention is
			decided from the fold's own rules, and there is no fold on screen yet.
		</p>
	{:else}
		{#if open.length > 0}
			<ul class="acts">
				{#each open as offer (offer.action)}
					<li>
						<button
							class="act plate"
							type="button"
							aria-expanded={armed?.action === offer.action}
							onclick={() =>
								armed?.action === offer.action ? disarm() : arm(offer.action)}
						>
							{ACTION_WORD[offer.action]}
						</button>
						<span class="act-gloss">{ACTION_GLOSS[offer.action]}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="prose quiet">
				Nothing can be done to this member from here right now. Each rule below says what
				would have to change.
			</p>
		{/if}

		{#if armed}
			<!-- Armed: the consequence, the inputs it needs, then the confirm. The
			     order matters — a control that takes its input after stating its
			     consequence is read in the order it is decided. -->
			<div class="panel plate">
				<p class="label rule-label">
					<span>Armed</span><span class="rule"></span>
					<span class="member" data-state="loaded">{ACTION_WORD[armed.action]}</span>
				</p>

				{#if reading.phase === 'reading'}
					<p class="prose quiet" aria-busy="true">Reading what this would disturb…</p>
				{/if}

				{#each lines as line, at (at)}
					<p class="prose panel-line" class:lead-line={at === 0}>{line}</p>
				{/each}

				{#if reading.phase === 'failed'}
					<p class="prose quiet member" data-state="failed" role="alert">
						The downstream read failed: {reading.message} Nothing above claims this is safe;
						what it would disturb is unknown.
					</p>
				{/if}

				{#if armed.action === 'restart'}
					<p class="prose quiet">
						The command a restart re-issues is held in the daemon's memory rather than in
						the plan's own history, so a daemon that was restarted since this attempt
						launched has nothing to re-issue and will refuse this. That refusal is a
						missing record, not a dead agent.
					</p>
				{/if}

				{#if armed.action === 'reassign'}
					{#if catalog.phase === 'reading'}
						<p class="prose quiet" aria-busy="true">Reading the harness catalog…</p>
					{:else if catalog.phase === 'failed'}
						<p class="prose quiet member" data-state="failed" role="alert">
							The catalog read failed: {catalog.message} Nothing can be chosen from a list
							that was not read, so this cannot be sent.
						</p>
					{:else if catalog.phase === 'read' && harnesses.length === 0}
						<p class="prose quiet member" data-state="slack">
							The Kitchen declares no harness, so there is nothing to reassign to. This is
							a configuration this project has not made yet, not a missing feature:
							{#each catalog.kitchen.blockers as blocker, at (blocker)}{at > 0
									? '; '
									: ''}{blocker}{/each}.
						</p>
					{:else if catalog.phase === 'read'}
						<div class="fields">
							<p class="field">
								<label class="label" for="reassign-harness">Harness</label>
								<span class="pick">
									<select
										class="plate"
										id="reassign-harness"
										bind:value={harness}
										onchange={() => (model = '')}
										aria-describedby="reassign-note"
									>
										<option value="">Choose a harness…</option>
										{#each harnesses as name (name)}
											<option value={name}>{name}</option>
										{/each}
									</select>
								</span>
							</p>
							<p class="field">
								<label class="label" for="reassign-model">Model</label>
								<span class="pick">
									<select
										class="plate"
										id="reassign-model"
										bind:value={model}
										disabled={harness === ''}
										aria-describedby="reassign-note"
									>
										<option value="">
											{harness === '' ? 'Choose a harness first…' : 'Choose a model…'}
										</option>
										{#each models as entry (entry.model)}
											<option value={entry.model}>{entry.model}</option>
										{/each}
									</select>
								</span>
							</p>
						</div>
						<p id="reassign-note" class="req">
							Both are required. Currently {assignmentWord(initiative)}.
							{#if chosenTier}— {model} is tiered {chosenTier}.{/if}
						</p>
						{#if harness !== '' && models.length === 0}
							<p class="prose quiet member" data-state="slack">
								<strong>{harness}</strong> declares no model in the Kitchen. A model is
								declared or discovered there, per harness — the pair is the identity, so a
								model from another harness is not a choice here.
							</p>
						{/if}
						{#if duplicatePair}
							<p class="prose quiet member" data-state="slack">
								That is the pair already in force. The fold refuses a reassignment onto the
								current assignment, so there is nothing to record.
							</p>
						{/if}
						<p class="prose quiet">
							The daemon checks that both halves are present and that the pair is new. The
							catalog says what is declared, not what will launch: a harness whose command
							cannot be compiled fails when the next attempt starts, not now.
						</p>
					{/if}
				{/if}

				{#if armed.action === 'redirect'}
					<fieldset class="targets">
						<legend class="label">Redirect to</legend>
						<p class="choice">
							<input type="radio" id="target-brief" value="brief" bind:group={target} />
							<label for="target-brief">A brief you write</label>
						</p>
						<p class="choice">
							<input
								type="radio"
								id="target-checkpoint"
								value="checkpoint"
								bind:group={target}
								disabled={choices.length === 0}
							/>
							<label for="target-checkpoint">
								A recorded checkpoint to continue from
								{#if choices.length === 0}— none recorded in this plan{/if}
							</label>
						</p>
					</fieldset>

					{#if target === 'brief'}
						<p class="field">
							<label class="label" for="redirect-brief"
								>Brief version {currentBriefVersion(initiative) + 1}</label
							>
							<textarea
								class="plate"
								id="redirect-brief"
								rows="6"
								bind:value={brief}
								aria-describedby="redirect-note"
							></textarea>
						</p>
						<p id="redirect-note" class="req">
							Required. This replaces the brief for new attempts; version
							{currentBriefVersion(initiative)} stays readable.
						</p>
					{:else}
						<p class="field">
							<label class="label" for="redirect-checkpoint">Checkpoint</label>
							<span class="pick">
							<select class="plate" id="redirect-checkpoint" bind:value={checkpointId}>
								<option value="">Choose a recorded version…</option>
								{#each choices as choice (choice.id)}
									<option value={choice.id}>
										{choice.producer} v{choice.version}{choice.own ? ' — this member' : ''}
										· {choice.id}
									</option>
								{/each}
							</select>
							</span>
						</p>
						{#if checkpointId}
							{@const chosen = choices.find((choice) => choice.id === checkpointId)}
							{#if chosen?.own}
								<p class="prose quiet">
									Its evidence — the checks that ran, what changed, and every decision on
									it — is below.
									<button class="linky" type="button" onclick={onreview}
										>Read it in the checkpoint section</button
									>, then come back to this.
								</p>
							{:else}
								<p class="prose quiet">
									This version belongs to <strong>{chosen?.producer}</strong>. Its evidence
									is read by opening that member, not from here — this build shows one
									member's checkpoints at a time and does not summarise another's.
								</p>
							{/if}
						{/if}
						<p class="prose quiet">
							The daemon derives the new brief from the version you choose. A redirect takes
							a brief or a checkpoint, never both.
						</p>
					{/if}
				{/if}

				{#if armed.action === 'reassign' || armed.action === 'redirect' || armed.action === 'pause' || armed.action === 'unpause' || armed.action === 'cancel'}
					<p class="field">
						<label class="label" for="intervene-reason">Reason</label>
						<input
							class="plate"
							id="intervene-reason"
							bind:value={reason}
							spellcheck="false"
							aria-describedby="reason-note"
						/>
					</p>
					<p id="reason-note" class="req">
						Optional, and kept with the record. It is what the next reader — including you
						— has to go on.
					</p>
				{/if}

				{#if armed.action === 'nudge'}
					<p class="field">
						<label class="label" for="nudge-text">Guidance</label>
						<textarea
							class="plate"
							id="nudge-text"
							rows="4"
							bind:value={nudgeText}
							aria-describedby="nudge-note"
						></textarea>
					</p>
					<p id="nudge-note" class="req">Required. Delivered as typed.</p>
					<p class="choice">
						<input type="checkbox" id="nudge-truth" bind:checked={groundTruth} />
						<label for="nudge-truth">Record this as a correction, not just a message</label>
					</p>
					<p class="prose quiet">
						{#if groundTruth}
							It is recorded against this run, so a later packet — a retry's included —
							carries the correction rather than repeating the mistake. Use this when you
							are telling the agent something that stays true.
						{:else}
							It reaches the pane and nothing else: a retry would compile its packet without
							it, and the correction would live only in a terminal nobody re-reads.
						{/if}
					</p>
				{/if}

				{#if armed.action === 'answer'}
					<p class="field">
						<label class="label" for="answer-subject">Subject</label>
						<input
							class="plate"
							id="answer-subject"
							bind:value={subject}
							spellcheck="false"
							autocomplete="off"
							aria-describedby="answer-note"
						/>
					</p>
					<p class="field">
						<label class="label" for="answer-text">Answer</label>
						<textarea
							class="plate"
							id="answer-text"
							rows="4"
							bind:value={answerText}
							aria-describedby="answer-note"
						></textarea>
					</p>
					<p id="answer-note" class="req">Both are required.</p>
					<p class="prose quiet">
						There is no question to pick from here, and that is a gap rather than a quiet
						agent: the daemon projects no list of what an agent has asked, so the only place
						the question exists is the pane itself. Read it there — the terminal control is
						at the bottom of this drawer — and name it here. The subject is what the answer
						is recorded against, so a repeat of the same question can be answered from the
						record instead of from you.
					</p>
				{/if}

				{#if armed.action === 'answer-memory'}
					{#if memoryStatus?.data && answerable.length > 0}
						<p class="field">
							<label class="label" for="memory-leaf">Memory leaf</label>
							<span class="pick">
								<select class="plate" id="memory-leaf" bind:value={memoryChoice} aria-describedby="memory-note">
									<option value="">Choose a leaf…</option>
									{#each answerable as leaf (leaf.id)}
										<option value={leaf.id}>{leaf.subject} — {leaf.claim}</option>
									{/each}
								</select>
							</span>
						</p>
						{#if chosenMemory}
							<p class="req" id="memory-note"><code>{chosenMemory.id}@{chosenMemory.version}</code> · {chosenMemory.origin} · recorded {new Date(chosenMemory.at).toLocaleTimeString()}</p>
						{:else}<p class="req" id="memory-note">Choose an active leaf. The daemon remains the authority on whether it can answer.</p>{/if}
					{:else}
						<p class="prose quiet member" data-state="slack">The subject list is unread or empty, so this cannot be armed. <button class="act" type="button" onclick={() => void memoryStatus?.load()}>Read again</button></p>
					{/if}
				{/if}

				<p class="confirmrow">
					<button
						class="act plate"
						type="button"
						bind:this={confirmEl}
						onclick={() => void confirm()}
						disabled={!ready || sending.phase === 'sending'}
					>
						{sending.phase === 'sending' ? 'Sending…' : `Confirm ${ACTION_WORD[armed.action].toLowerCase()}`}
					</button>
					<button
						class="act plate"
						type="button"
						onclick={disarm}
						disabled={sending.phase === 'sending'}
					>
						Cancel
					</button>
					{#if armed.action === 'retry' && sending.phase === 'sending'}
						<span class="member outcome" data-state="loaded" role="status">
							The daemon holds this open until the attempt settles, which can take minutes.
						</span>
					{/if}
				</p>
			</div>
		{/if}

		{#if sending.phase === 'done'}
			<p class="member outcome standalone" data-state="seated" role="status">
				{sending.message}
			</p>
		{:else if memoryOutcome}
			{#if memoryOutcome.leaf}
				<p class="member outcome standalone" data-state="seated" role="status">
					Answered from memory: {memoryOutcome.leaf.id}@{memoryOutcome.leaf.version}. Delivered on {memoryOutcome.leaf.subject}: {memoryOutcome.leaf.claim}
				</p>
			{:else}
				<p class="member outcome standalone" data-state="slack" role="status">
					Nothing was delivered. No active leaf matches that subject for this member, so the daemon recorded nothing and the pane received nothing. This one is yours to answer — the answer control is above.
				</p>
			{/if}
		{:else if sending.phase === 'failed'}
			<p class="member outcome standalone" data-state="failed" role="alert">
				Not done: {sending.message}
			</p>
		{/if}

		{#if held.length > 0}
			<!-- An unavailable action is the rule that refuses it. A greyed control
			     says only that you cannot; the rule says what would change that.

			     Actions refused by the same check share one entry: three of the six
			     are held by whether there is a live pane, and printing that sentence
			     three times is the defect this drawer was already corrected for
			     once. The label rides a hairline like every other label here. -->
			<dl class="held">
				{#each held as group (group.actions.join('+'))}
					<div>
						<dt class="label">
							<span>{group.actions.map((action) => ACTION_WORD[action]).join(' · ')}</span>
							<span class="rule"></span>
						</dt>
						<dd class="prose quiet">{group.refused}</dd>
					</div>
				{/each}
			</dl>
		{/if}

		<p class="prose quiet foot">
			Holding and cancelling a whole plan are not available in one write. Hold,
			release hold and cancel are decided here, one member at a time; reconciling
			attempts a dead daemon left open is a plan-level action above the drawing.
			Comparing a replanned graph against this one is not built. Salvage reads the
			whole run’s preserved evidence on the page behind this sheet; this section is
			what can be done to one member.
		</p>
	{/if}
</section>

<style>
	section {
		margin-top: 1.75rem;
	}

	/* --- the ruled label, as everywhere else in this world ------------------ */
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
	.rule-label > span:last-child {
		flex: none;
		max-width: 55%;
		overflow-wrap: anywhere;
		text-align: right;
	}

	.prose {
		margin: 0;
		max-width: 68ch;
		color: var(--ink-2);
	}
	.quiet {
		font-size: 0.8125rem;
	}
	.foot {
		margin-top: 1.1rem;
	}
	strong {
		color: var(--ink);
		font-weight: 500;
	}

	/* Ash draws slack and never sets text: a slack reading is graphite carrying
	   a dashed ash rule instead. */
	.member[data-state='slack'] {
		color: var(--ink-2);
	}
	/* --- the available six -------------------------------------------------- */
	/* A grid, not a wrapping flex row: with `flex: 1 1` the last control on a
	   row stretches to fill it, so five available actions put a double-width
	   ANSWER under four ordinary ones and the row reads as a hierarchy that
	   does not exist. Auto-fill tracks keep every control one column wide
	   whatever the count. */
	.acts {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: 0.6rem 0.75rem;
	}
	.acts li {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		min-width: 0;
	}
	.act-gloss {
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
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
		cursor: pointer;
		text-align: center;
	}
	.act:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}
	.act:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}

	/* --- the armed panel ----------------------------------------------------
	   A plate on a hairline inside a plate, exactly as an attempt is. Nothing
	   here floats, nothing animates: this system has one authored motion and it
	   belongs to load, not to panels opening. */
	.panel {
		--cut: 12px;
		margin-top: 1.1rem;
		border: 1px solid var(--rule-strong);
		padding: 1rem 1rem 1.1rem;
		background: var(--plate);
	}
	.panel .rule-label {
		margin-bottom: 0.75rem;
	}
	.panel-line + .panel-line {
		margin-top: 0.6rem;
	}
	/* One line carries the break; the sentences explaining it do not. */
	.lead-line {
		color: var(--ink);
	}

	/* --- inputs, as the system builds them ---------------------------------- */
	.fields {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}
	.fields .field {
		flex: 1 1 10rem;
		min-width: 0;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin: 0.9rem 0 0;
	}
	/* The native dropdown arrow belongs to no design system; this is the
	   hairline the rest of the surface is drawn with. */
	.pick {
		position: relative;
		display: flex;
		min-width: 0;
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
	input:not([type]),
	textarea,
	select {
		--cut: 10px;
		font: inherit;
		width: 100%;
		box-sizing: border-box;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	select {
		appearance: none;
		padding-right: 2.25rem;
	}
	select:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	textarea {
		resize: vertical;
		line-height: 1.6;
	}
	input:focus-visible,
	textarea:focus-visible,
	select:focus-visible {
		border-color: var(--red);
	}
	.req {
		margin: 0.35rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.1em;
		line-height: 1.5;
		text-transform: uppercase;
		color: var(--ink-2);
	}

	.targets {
		margin: 0.9rem 0 0;
		border: 0;
		padding: 0;
		min-width: 0;
	}
	.targets legend {
		padding: 0;
		margin-bottom: 0.4rem;
	}
	.choice {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		margin: 0.4rem 0 0;
		color: var(--ink-2);
		font-size: 0.8125rem;
	}
	.choice input {
		--cut: 0;
		width: auto;
		flex: none;
		/* Carbon, not red. A ticked box is a choice that is seated in the
		   structure, not a member under load, and the Load-Only Red Rule does not
		   bend for a form control. */
		accent-color: var(--ink);
		padding: 0;
		border: 0;
	}

	/* A control set inline in a sentence is the sentence's own word, underlined
	   like every other link in this world — not a second button competing with
	   the confirm. */
	.linky {
		font: inherit;
		color: var(--ink);
		background: none;
		border: 0;
		padding: 0;
		text-decoration: underline;
		text-underline-offset: 0.2em;
		cursor: pointer;
	}
	.linky:hover {
		color: var(--red);
	}

	.confirmrow {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 1.1rem 0 0;
		padding-top: 0.9rem;
		border-top: 1px solid var(--rule);
	}

	.outcome {
		font-size: 0.8125rem;
		color: var(--member-ink);
	}
	.outcome[data-state='seated'] {
		color: var(--ink);
	}
	.standalone {
		display: block;
		margin: 1.1rem 0 0;
		max-width: 68ch;
	}

	/* --- what is refused, and by which rule --------------------------------- */
	.held {
		margin: 1.4rem 0 0;
		display: flex;
		flex-direction: column;
		gap: 0.8rem;
	}
	/* Deliberately not `.rule-label`: that pattern pins a state word at the far
	   right and gives its last child `flex: none`, which collapses a trailing
	   hairline to zero width. These entries have no state word — the section
	   label already said how many are held — so the rule is the last child and
	   needs its own flex. */
	.held dt {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.4rem;
	}
	.held .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.held dd {
		margin: 0;
	}

	@media (max-width: 60rem) {
		.acts li {
			flex-basis: 100%;
		}
	}
</style>
