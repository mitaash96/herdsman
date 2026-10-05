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
	import { tick, type Snippet } from 'svelte';
	import Button from './Button.svelte';
	import IconButton from './IconButton.svelte';
	import Segmented from './Segmented.svelte';
	import type { IconName } from './icons';
	import type { Resource } from './resource.svelte';
	import { defaultEffort, effortPool } from './kitchen';
	import { withEffort } from './dispatch';
	import {
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
		memoryStatus,
		statement,
		awaiting = null,
		asking = false,
		ready: nodeReady = false,
		pane = null,
		onfocus,
		ontab,
		onverdict,
		historical = false
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
		/** The status statement; shown in place until a form replaces it. */
		statement?: Snippet;
		/** The checkpoint version waiting on the operator, else null. */
		awaiting?: number | null;
		/** The live agent stopped at a dialog and wants an answer. */
		asking?: boolean;
		/** Every dependency has settled; nothing but the daemon starts it. */
		ready?: boolean;
		pane?: string | null;
		onfocus?: () => void;
		/** Switch the dock tab (`diff`, `review`). */
		ontab?: (tab: string) => void;
		/** Approve / Request changes are armed in the Review pane. */
		onverdict?: (verdict: 'approve' | 'changes') => void;
		historical?: boolean;
	} = $props();

	const offers = $derived.by(() => {
		if (!initiative) return [];
		return availability(initiative, approved).map((offer) => {
			if (offer.action !== 'answer-memory' || !offer.available) return offer;
			/* The live-pane rule already holds; the memory read can only narrow it further. */
			if (!memoryStatus?.data || memoryStatus.stale)
				return {
					...offer,
					available: false,
					refused: 'The memory status read has not answered, so the subject list is unread. Read it before choosing a leaf.'
				};
			const any = memoryStatus.data.leaves.some((leaf) => leaf.status === 'active');
			return {
				...offer,
				available: any,
				refused: any ? null : 'Nothing in this run’s memory can answer for you yet.'
			};
		});
	});
	const openOffers = $derived(offers.filter((offer) => offer.available));
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
	let formEl = $state<HTMLElement | null>(null);

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
	/* The one level the next attempt runs under, chosen from the pool the pair
	   is allowed. Empty means "the daemon's own pick" — the highest level the
	   pair reports — which is also what the row shows pressed. */
	let effort = $state('');
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
		effort = '';
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
		void tick().then(() => formEl?.querySelector<HTMLElement>('select:not(:disabled),textarea,input:not([type=checkbox]),.btnrow button')?.focus({ preventScroll: true }));
	}

	/** The Assignment column's pencil opens the reassign form in place. */
	export function openAction(action: Action) {
		if (offers.find((o) => o.action === action)?.available) arm(action);
	}

	/** Whether an action is open right now, and the rule that refuses it when it is not. */
	export function status(action: Action): { available: boolean; refused: string | null } {
		const offer = offers.find((o) => o.action === action);
		return { available: offer?.available ?? false, refused: offer?.refused ?? null };
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

	/* The pair's effort pool and the level the row shows pressed: the operator's
	   pick when they made one, else the highest level of the pool. An empty pool
	   renders no chips and sends no level, and the daemon launches its default. */
	const effortChoices = $derived(
		catalog.phase === 'read' && harness !== '' && model !== ''
			? effortPool(catalog.kitchen, `${harness}/${model}`)
			: []
	);
	const effortPick = $derived(
		effort !== '' ? effort : (defaultEffort(effortChoices) ?? '')
	);

	/* --- what confirming needs ---------------------------------------------- */
	const pair = $derived(
		harness.length > 0 && model.length > 0 ? { harness, model } : null
	);
	/* The whole assignment, level included: moving only the level of the same
	   pair is a reassignment the fold accepts, so this is a duplicate only when
	   the triple already in force is exactly what is chosen here. */
	const duplicatePair = $derived(
		initiative !== null &&
			pair !== null &&
			sameAssignment(initiative, harness, model, effortPick || null)
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
			assignment: pair ? { ...pair, effort: effortPick || null } : null,
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
				await daemon.reassign(planId, id, pair.harness, pair.model, effortPick || null, reason.trim());
				detail = withEffort(`${pair.harness}/${pair.model}`, effortPick);
				harness = '';
				model = '';
				effort = '';
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

	const ICON: Record<Action, IconName> = {
		retry: 'rotate-ccw', restart: 'refresh-cw', reassign: 'arrow-left-right', redirect: 'route', nudge: 'send-horizontal',
		answer: 'circle-help', pause: 'hand', unpause: 'play', cancel: 'x', 'answer-memory': 'brain'
	};
	const refusal = (action: Action) => offers.find((o) => o.action === action)?.refused ?? undefined;
	const can = (action: Action) => offers.find((o) => o.action === action)?.available ?? false;

	type Btn = { key: string; icon: IconName; label: string; run: () => void; action?: Action };
	const btn = (action: Action, label = ACTION_WORD[action]): Btn => ({ key: action, icon: ICON[action], label, run: () => arm(action), action });
	const diffBtn: Btn = { key: 'diff', icon: 'git-compare', label: 'Diff', run: () => ontab?.('diff') };

	/** One primary per state, then secondaries (DS §9.14). A step the daemon has no action for is never invented. */
	const row = $derived.by((): Btn[] => {
		if (!initiative) return [];
		const only = (list: (Btn | null)[]) => list.filter((b): b is Btn => b !== null && (!b.action || can(b.action)));
		if (awaiting !== null)
			return [
				{ key: 'approve', icon: 'check-check', label: `Approve v${awaiting}`, run: () => onverdict?.('approve') },
				{ key: 'changes', icon: 'message-square-diff', label: 'Request changes', run: () => onverdict?.('changes') },
				diffBtn
			];
		if (asking && can('answer')) return only([btn('answer')]);
		switch (initiative.state) {
			case 'failed': return only([btn('retry'), btn('reassign'), diffBtn]);
			case 'running':
				return only([
					pane && onfocus ? { key: 'focus', icon: 'square-terminal', label: 'Focus pane', run: () => onfocus() } : null,
					btn('nudge'), diffBtn
				]);
			case 'paused': return only([btn('unpause'), btn('reassign')]);
			case 'settled': return only([{ key: 'handoff', icon: 'file-text', label: 'Open handoff', run: () => ontab?.('review') }, diffBtn]);
			case 'pending': return only([btn('reassign'), nodeReady ? null : btn('redirect', 'Redirect brief')]);
			default: return [];
		}
	});
	const bar = $derived.by(() => {
		const used = new Set(row.map((b) => b.action));
		const ordered: Action[] = ['redirect', 'reassign', initiative?.state === 'paused' ? 'unpause' : 'pause', 'cancel'];
		const canonical = ordered.filter((a) => !used.has(a));
		const extra = (['restart', 'nudge', 'answer', 'answer-memory', 'retry'] as Action[]).filter((a) => !used.has(a) && can(a));
		return [...canonical, ...extra];
	});
</script>

<div class="iv">
	{#if !armed}
		{#if statement}{@render statement()}{/if}
	{/if}

	{#if historical}
		<p class="dk-note">Historical replay is read-only; nothing can be done to this member from a past state.</p>
	{:else if !initiative}
		<p class="dk-note">The folded plan has not answered for <strong>{id}</strong>, so what can be done to it is unread.</p>
	{:else}
		{#if armed}
			{@const reasonable = armed.action === 'reassign' || armed.action === 'redirect' || armed.action === 'pause' || armed.action === 'unpause' || armed.action === 'cancel'}
			<div class="form" bind:this={formEl}>
				<div class="rule-h"><span class="lbl">{ACTION_WORD[armed.action]}</span></div>

				{#if reading.phase === 'reading'}<p class="dk-note" aria-busy="true">Reading what this would disturb…</p>{/if}
				{#if reading.phase === 'failed'}
					<p role="alert"><span class="state" data-tone="failed">Downstream read failed</span> <span class="dk-note">{reading.message} What this would disturb is unknown.</span></p>
				{/if}

				{#if armed.action === 'reassign'}
					{#if catalog.phase === 'reading'}
						<p class="dk-note" aria-busy="true">Reading the harness catalog…</p>
					{:else if catalog.phase === 'failed'}
						<p role="alert"><span class="state" data-tone="failed">Catalog read failed</span> <span class="dk-note">{catalog.message} Nothing can be chosen from a list that was not read.</span></p>
					{:else if catalog.phase === 'read' && harnesses.length === 0}
						<p class="dk-note">The Kitchen declares no harness, so there is nothing to reassign to{#each catalog.kitchen.blockers as blocker, at (blocker)}{at > 0 ? '; ' : ': '}{blocker}{/each}.</p>
					{:else if catalog.phase === 'read'}
						<div class="fields">
							<div class="field-l">
								<label class="lbl" for="reassign-harness">Harness</label>
								<select class="sel" id="reassign-harness" bind:value={harness} onchange={() => { model = ''; effort = ''; }}>
									<option value="">Harness…</option>
									{#each harnesses as name (name)}<option value={name}>{name}</option>{/each}
								</select>
							</div>
							<div class="field-l">
								<label class="lbl" for="reassign-model">Model</label>
								<select class="sel" id="reassign-model" bind:value={model} onchange={() => (effort = '')} disabled={harness === ''}>
									<option value="">{harness === '' ? 'Harness first…' : 'Model…'}</option>
									{#each models as entry (entry.model)}<option value={entry.model}>{entry.model}</option>{/each}
								</select>
							</div>
							<div class="field-l">
								<label class="lbl" for="reassign-effort">Effort</label>
								<select class="sel" id="reassign-effort" value={effortPick} onchange={(e) => (effort = e.currentTarget.value)} disabled={effortChoices.length === 0}>
									{#if effortChoices.length === 0}<option value="">{model === '' ? '—' : 'harness default'}</option>{/if}
									{#each effortChoices as level (level)}<option value={level}>{level}</option>{/each}
								</select>
							</div>
						</div>
						<p class="dk-note">Currently {assignmentWord(initiative)}.{#if chosenTier} {model} is tiered {chosenTier}.{/if}</p>
						{#if harness !== '' && models.length === 0}<p class="dk-note"><strong>{harness}</strong> declares no model in the Kitchen; the pair is the identity, so another harness's model is not a choice.</p>{/if}
						{#if duplicatePair}<p class="dk-note">That is the assignment already in force; the fold refuses a reassignment onto it.</p>{/if}
					{/if}
				{/if}

				{#if armed.action === 'redirect'}
					<Segmented label="Redirect to" value={target}
						options={[{ id: 'brief', label: 'A brief you write' }, { id: 'checkpoint', label: 'A checkpoint' }]}
						onchange={(id) => { if (id === 'brief' || choices.length > 0) target = id; }} />
					{#if choices.length === 0}<p class="dk-note">No checkpoint is recorded in this plan to continue from.</p>{/if}
					{#if target === 'brief'}
						<div class="field-l">
							<label class="lbl" for="redirect-brief">Brief version {currentBriefVersion(initiative) + 1} · replaces v{currentBriefVersion(initiative)} for new attempts</label>
							<textarea class="ta" id="redirect-brief" rows="5" bind:value={brief}></textarea>
						</div>
					{:else}
						<div class="field-l">
							<label class="lbl" for="redirect-checkpoint">Checkpoint</label>
							<select class="sel" id="redirect-checkpoint" bind:value={checkpointId}>
								<option value="">Choose a recorded version…</option>
								{#each choices as choice (choice.id)}
									<option value={choice.id}>{choice.producer} v{choice.version}{choice.own ? ' — this member' : ''} · {choice.id}</option>
								{/each}
							</select>
						</div>
						{#if checkpointId}
							{@const chosen = choices.find((choice) => choice.id === checkpointId)}
							{#if chosen?.own}
								<p class="dk-note">Its evidence is in the Review tab. <Button icon="eye" small onclick={onreview}>Read it</Button></p>
							{:else}
								<p class="dk-note">This version belongs to <strong>{chosen?.producer}</strong>; read its evidence by opening that member.</p>
							{/if}
						{/if}
					{/if}
				{/if}

				{#if reasonable}
					<div class="field-l">
						<label class="lbl" for="intervene-reason">Reason · optional, kept with the record</label>
						<input class="inp" id="intervene-reason" bind:value={reason} spellcheck="false" />
					</div>
				{/if}

				{#if armed.action === 'nudge'}
					<div class="field-l">
						<label class="lbl" for="nudge-text">Guidance · delivered as typed</label>
						<textarea class="ta" id="nudge-text" rows="3" bind:value={nudgeText}></textarea>
					</div>
					<label class="chk"><input type="checkbox" bind:checked={groundTruth} /> Record as a correction, so a retry's packet carries it</label>
				{/if}

				{#if armed.action === 'answer'}
					<div class="fields two">
						<div class="field-l">
							<label class="lbl" for="answer-subject">Subject</label>
							<input class="inp" id="answer-subject" bind:value={subject} spellcheck="false" autocomplete="off" />
						</div>
						<div class="field-l">
							<label class="lbl" for="answer-text">Answer</label>
							<textarea class="ta" id="answer-text" rows="3" bind:value={answerText}></textarea>
						</div>
					</div>
					<p class="dk-note">The daemon projects no list of questions; read it in the pane and name it here — the subject is what the answer is recorded against.</p>
				{/if}

				{#if armed.action === 'answer-memory'}
					{#if memoryStatus?.data && answerable.length > 0}
						<div class="field-l">
							<label class="lbl" for="memory-leaf">Memory leaf</label>
							<select class="sel" id="memory-leaf" bind:value={memoryChoice}>
								<option value="">Choose a leaf…</option>
								{#each answerable as leaf (leaf.id)}<option value={leaf.id}>{leaf.subject} — {leaf.claim}</option>{/each}
							</select>
						</div>
						{#if chosenMemory}<p class="dk-note"><code>{chosenMemory.id}@{chosenMemory.version}</code> · {chosenMemory.origin} · recorded {new Date(chosenMemory.at).toLocaleTimeString()}</p>{/if}
					{:else}
						<p class="dk-note">The subject list is unread or empty, so this cannot be armed. <Button icon="refresh-cw" small onclick={() => void memoryStatus?.load()}>Read again</Button></p>
					{/if}
				{/if}

				{#if armed.action === 'restart'}
					<p class="dk-note">A restart re-issues a command held in daemon memory; after a daemon restart it has nothing to re-issue and is refused — a missing record, not a dead agent.</p>
				{/if}

				{#each lines as line, at (at)}<p class="dk-note" class:lead={at === 0}>{line}</p>{/each}
				<div class="btnrow">
					<Button icon={ICON[armed.action]} kind={armed.action === 'cancel' ? 'danger' : 'primary'}
						busy={sending.phase === 'sending'} disabled={!ready || sending.phase === 'sending'} onclick={() => void confirm()}>
						{ACTION_WORD[armed.action]}
					</Button>
					<Button icon="x" onclick={disarm} disabled={sending.phase === 'sending'}>Cancel</Button>
					{#if armed.action === 'retry' && sending.phase === 'sending'}
						<span class="state" data-tone="running" role="status">Held open until the attempt settles</span>
					{/if}
				</div>
				{#if sending.phase === 'failed'}
					<p role="alert"><span class="state" data-tone="failed">Not done</span> <span class="dk-note">{sending.message}</span></p>
				{/if}
			</div>
		{:else}
			<div class="btnrow">
				{#each row as b, at (b.key)}
					<Button icon={b.icon} kind={at === 0 ? 'primary' : 'secondary'} onclick={b.run}>{b.label}</Button>
				{/each}
				<span class="ibar">
					{#each bar as action (action)}
						<IconButton icon={ICON[action]} label={ACTION_WORD[action]} danger={action === 'cancel'}
							disabled={!can(action)} reason={refusal(action)} onclick={() => arm(action)} />
					{/each}
				</span>
			</div>
			{#if sending.phase === 'done'}
				<p class="state" data-tone="pass" role="status">{sending.message}</p>
			{:else if memoryOutcome}
				<p class="state" data-tone={memoryOutcome.leaf ? 'pass' : 'idle'} role="status">
					{memoryOutcome.leaf
						? `Answered from memory: ${memoryOutcome.leaf.id}@${memoryOutcome.leaf.version} on ${memoryOutcome.leaf.subject}`
						: 'Nothing delivered — no active leaf matches that subject; this one is yours to answer'}
				</p>
			{:else if sending.phase === 'failed'}
				<p role="alert"><span class="state" data-tone="failed">Not done</span> <span class="dk-note">{sending.message}</span></p>
			{/if}
		{/if}
	{/if}
</div>

<style>
	.iv { min-width: 0; }
	.form { display: grid; gap: 10px; }
	.form .rule-h { margin-bottom: 0; }
	.fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
	.fields.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1.6fr); }
	.lead { color: var(--tx2); }
	.chk { display: flex; align-items: center; gap: 8px; color: var(--tx2); font-size: 12.5px; }
	.chk input { accent-color: var(--tx); }
	:global(.iv .btnrow) { margin-top: 14px; }
	.form .btnrow { margin-top: 4px; }
	@media (max-width: 1100px) { .fields { grid-template-columns: minmax(0, 1fr); } }
</style>
