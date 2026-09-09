<script lang="ts">
	/*
	  Unit R3 — the plan approval gate. Its direction contract lives in
	  `.impeccable/surfaces/ui-src-lib-plangate-svelte.md`, not here.

	  The division with R1 and R2, which is the whole design: R1's field says
	  where a member sits, R2's drawer says what one initiative is, and this
	  sheet says what the *revision* commits you to. It is the same right-edge
	  slot as the drawer and deliberately so — while a plan is proposed the
	  decision is the only thing you can do with it, and selecting a member
	  from here opens the drawer over this sheet and comes back to it.

	  Two things this build cannot do, stated on screen rather than implied:
	  no revision action exists (the daemon has no route that re-proposes a
	  plan), and no estimate here is a limit (budget enforcement is Sprint 4).

	  Siblings deliberately absent: Dispatch, checkpoint approval (R4) and
	  recalibration comparison (R10).
	*/
	import AsyncField from './AsyncField.svelte';
	import type { Resource } from './resource.svelte';
	import { daemon, DaemonError, type Plan, type PlanGraph, type RiskReport } from './daemon';
	import { step, type Field } from './field';
	import {
		budgetOf,
		calloutsOf,
		downstream,
		registerOf,
		shapeOf,
		PROVENANCE,
		type Callout
	} from './gate';

	let {
		open,
		planId,
		graph,
		field,
		risk,
		plan,
		covered,
		selected,
		onselect,
		onclose,
		onapproved
	}: {
		open: boolean;
		planId: string;
		/** The projection the decision is made from; it alone is enough to decide. */
		graph: PlanGraph;
		/** R1's lane model, so the gate and the drawing count parallelism once. */
		field: Field;
		/** Contention. `null` data means unread — which is not the same as none. */
		risk: Resource<RiskReport> | null;
		/** The folded plan: the brief, the specs, and what planning cost. */
		plan: Resource<Plan> | null;
		/** The drawer is over this sheet; hand it the keyboard entirely. */
		covered: boolean;
		selected: string | null;
		onselect: (id: string) => void;
		onclose: () => void;
		/** Approval landed; the page re-reads rather than this sheet guessing. */
		onapproved: () => void;
	} = $props();

	const approved = $derived(graph.approval === 'approved');
	const version = $derived(graph.version);

	/* --- the decision -------------------------------------------------------
	   Arm, then confirm. The version confirmed is the version that was on
	   screen when the operator armed, so a revision that lands in between is
	   refused by the daemon rather than approved unread. */
	type Decide = { phase: 'idle' | 'armed' | 'sending' | 'done' | 'failed'; message: string };
	let decide = $state<Decide>({ phase: 'idle', message: '' });
	/** The revision the operator armed against, not the one on screen now. */
	let armedAt = $state<number | null>(null);
	let confirmEl = $state<HTMLButtonElement | null>(null);

	/* A new plan is a new question, and so is a new revision: never carry an
	   armed decision — or last time's refusal — across either.

	   Keyed rather than dependency-tracked, because this effect also fires on
	   every live re-read of the same revision, and a reset there wipes the
	   outcome of the write that caused the re-read. Approving is the one action
	   on this sheet; its result must outlive the refresh it triggers. */
	let decidedFor = $state<string | null>(null);
	$effect(() => {
		const key = `${planId}@${version}`;
		if (decidedFor === key) return;
		decidedFor = key;
		decide = { phase: 'idle', message: '' };
		armedAt = null;
	});

	function arm() {
		armedAt = version;
		decide = { phase: 'armed', message: '' };
		// Confirm is a new control; put the keyboard on it rather than leaving
		// focus on a button that no longer exists.
		queueMicrotask(() => confirmEl?.focus());
	}

	function disarm() {
		armedAt = null;
		decide = { phase: 'idle', message: '' };
	}

	async function confirm() {
		if (armedAt === null || decide.phase === 'sending') return;
		const target = armedAt;
		decide = { phase: 'sending', message: '' };
		try {
			const result = await daemon.approve(planId, target);
			armedAt = null;
			decide = {
				phase: 'done',
				message: `Revision ${result.version} is approved.`
			};
			onapproved();
		} catch (cause) {
			armedAt = null;
			decide = {
				phase: 'failed',
				message:
					cause instanceof DaemonError
						? cause.message
						: 'Something in this build failed while asking the daemon to approve.'
			};
		}
	}

	/* Escape peels one layer at a time: an armed decision is cancelled before
	   the sheet closes, so Escape can never dismiss the sheet out from under a
	   half-made decision. Nothing else is bound — no shortcut in this build
	   approves anything, and none may. */
	function onkeydown(event: KeyboardEvent) {
		if (!open || covered || event.key !== 'Escape') return;
		if (decide.phase === 'armed') disarm();
		else onclose();
	}

	/* --- the register's keyboard, exactly the load schedule's ---------------
	   One tab stop, arrows within it. A twenty-eight member plan must not be
	   twenty-eight tab stops in a scrolling sheet. */
	function onRegisterKey(event: KeyboardEvent, order: string[]) {
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		const next = step(order, selected, event.key === 'ArrowDown' ? 1 : -1);
		if (!next) return;
		event.preventDefault();
		onselect(next);
		document.getElementById(`gate-row-${next}`)?.focus();
	}

	const KIND: Record<Callout['kind'], string> = {
		scope: 'Scope',
		choke: 'Chokepoint',
		overlap: 'Overlap'
	};

	const NUMBER = new Intl.NumberFormat();
</script>

<svelte:window on:keydown={onkeydown} />

<aside
	class="gate plate"
	hidden={!open}
	inert={covered || undefined}
	aria-labelledby="gate-title"
>
	{#if open}
		{@const shape = shapeOf(graph, field.lanes.length)}
		{@const budget = budgetOf(plan?.data ?? null)}
		{@const callouts = calloutsOf(graph, risk?.data ?? null, downstream(graph))}
		{@const rows = registerOf(field.members, plan?.data ?? null)}
		{@const order = field.members.map((m) => m.node.initiative_id)}
		{@const anchor = selected && field.byId.has(selected) ? selected : order[0]}
		{@const hard = callouts?.filter((c) => !c.advisory) ?? []}
		{@const advisory = callouts?.filter((c) => c.advisory) ?? []}

		<header>
			<p class="label rule-label">
				<span>Plan</span><span class="rule"></span><span>{planId}</span>
			</p>
			<div class="headrow">
				<h2 id="gate-title" tabindex="-1">Revision {version}</h2>
				<button class="act plate" type="button" onclick={onclose}>Close</button>
			</div>
			<p class="prose quiet head-note member" data-state={approved ? 'seated' : 'slack'}>
				{#if approved}
					Approved. Members may run.
				{:else}
					Proposed. Nothing in this plan can run until you approve it.
				{/if}
			</p>
		</header>

		<div class="body">
			<!-- 1. What was asked. You are approving a decomposition *of* something. -->
			<section>
				<p class="label rule-label">
					<span>The brief</span><span class="rule"></span>
					<span>{plan?.data?.planner ? `${plan.data.planner.harness} · ${plan.data.planner.model}` : 'Planner unread'}</span>
				</p>
				{#if plan}
					<AsyncField resource={plan} reading="the plan" onretry={() => void plan?.load()}>
						{#snippet children(folded: Plan)}
							<blockquote class="brief prose">{folded.brief}</blockquote>
						{/snippet}
					</AsyncField>
				{:else}
					<p class="prose">No plan is addressed, so there is no brief to read.</p>
				{/if}
			</section>

			<!-- 2. What it became. Every number here is structure, and none is a time. -->
			<section>
				<p class="label rule-label">
					<span>Shape</span><span class="rule"></span><span>{shape.members} members</span>
				</p>
				<dl class="readout">
					<div>
						<dt class="label">Lanes</dt>
						<dd class="value">{shape.lanes}</dd>
						<p class="gloss">the most agents this plan can ever keep busy</p>
					</div>
					<div>
						<dt class="label">Critical path</dt>
						<dd class="value">{shape.chain || '—'}</dd>
						<p class="gloss">members on the longest chain — how deep, not how long</p>
					</div>
					<div>
						<dt class="label">Ready on approval</dt>
						<dd class="value member" data-state={shape.readyOnApproval > 0 ? 'balanced' : 'slack'}>
							{shape.readyOnApproval}
						</dd>
						<p class="gloss">
							{shape.readyOnApproval > 0
								? 'may start the moment this revision is approved'
								: 'nothing can start yet; every member waits on another'}
						</p>
					</div>
				</dl>
				{#if !field.agrees}
					<p class="prose foot member" data-state="failed" role="alert">
						This build counts {field.lanes.length} lanes where the daemon computes a maximum
						concurrency of {graph.max_concurrency}. Treat the parallelism above as
						unreliable until the two agree.
					</p>
				{/if}
			</section>

			<!-- 3. What it costs. One real figure, and an honest account of the rest. -->
			<section>
				<p class="label rule-label">
					<span>Budget</span><span class="rule"></span><span>Not enforced</span>
				</p>
				<dl class="readout">
					<div>
						<dt class="label">Planning cost</dt>
						<dd
							class="value member"
							data-state={budget.planningTokens === null ? 'slack' : 'seated'}
						>
							{budget.planningTokens === null ? '—' : NUMBER.format(budget.planningTokens)}
						</dd>
						<p class="gloss">
							{#if !plan?.data}
								unread — the plan did not answer
							{:else if budget.planning}
								tokens, {PROVENANCE[budget.planning.source]}
							{:else}
								the planner reported no usage, so this is unknown rather than zero
							{/if}
						</p>
					</div>
					<div>
						<dt class="label">Per member</dt>
						<dd class="value member" data-state="slack">—</dd>
						<p class="gloss">nothing has run, so no member has a measured cost</p>
					</div>
				</dl>
				<p class="prose foot quiet">
					Herdsman does not estimate what a member will spend before it runs, and
					nothing on this sheet is a limit: approving sets no ceiling and stops
					nothing. Metered budgets and burn-down arrive with the token ledger.
				</p>
			</section>

			<!-- 4. What is risky. Hard limits and chokepoints first; advice ranked below. -->
			<section>
				<p class="label rule-label">
					<span>Callouts</span><span class="rule"></span>
					<span class="member" data-state={callouts === null ? 'slack' : 'seated'}>
						{callouts === null ? 'Unread' : callouts.length}
					</span>
				</p>
				{#if callouts === null}
					<p class="prose member" data-state="slack" role="status">
						The risk report did not answer, so scope collisions, chokepoints and
						unordered overlap are <strong>unread</strong> — that is unknown, not none.
						Approving now approves a decomposition whose contention nobody has seen.
						{#if risk}
							<button class="act inline" type="button" onclick={() => void risk?.load()}>
								Read again
							</button>
						{/if}
					</p>
				{:else if callouts.length === 0}
					<p class="prose">
						No scope collision, no chokepoint and no unordered overlap in this
						revision. Every member writes where nothing else writes, and every path
						that matters is ordered by a dependency.
					</p>
				{:else}
					{#if hard.length > 0}
						<ul class="callouts">
							{#each hard as callout (callout.kind + callout.about.join())}
								<li class="callout member" data-state={callout.state}>
									<p class="callout-kind label">{KIND[callout.kind]}</p>
									<p class="callout-text">{callout.text}</p>
									<p class="callout-about">
										{#each callout.about as id (id)}
											<button class="pick-id" type="button" onclick={() => onselect(id)}>{id}</button>
										{/each}
									</p>
								</li>
							{/each}
						</ul>
					{/if}
					{#if advisory.length > 0}
						<p class="label rule-label sub">
							<span>Advisory</span><span class="rule"></span><span>{advisory.length}</span>
						</p>
						<p class="prose quiet">
							Neither the lanes nor the daemon stop any of these. One shared file can
							suggest several.
						</p>
						<ul class="callouts">
							{#each advisory as callout (callout.kind + callout.about.join())}
								<li class="callout member" data-state={callout.state}>
									<p class="callout-kind label">{KIND[callout.kind]}</p>
									<p class="callout-text">{callout.text}</p>
									<p class="callout-about">
										{#each callout.about as id (id)}
											<button class="pick-id" type="button" onclick={() => onselect(id)}>{id}</button>
										{/each}
									</p>
								</li>
							{/each}
						</ul>
					{/if}
				{/if}
			</section>

			<!-- 5. The decomposition itself, in the field's own order. The schedule
			     carries placement; this carries what the planner wrote. -->
			<section>
				<p class="label rule-label">
					<span>Register</span><span class="rule"></span>
					<span>
						{rows.length} {rows.length === 1 ? 'member' : 'members'}{plan?.data ? '' : ' · briefs unread'}
					</span>
				</p>
				<p class="prose quiet">
					Every member, in the field's order. Arrow keys move between them; opening one
					reads it in full.
				</p>
				<ol class="register">
					{#each rows as row (row.member.node.initiative_id)}
						{@const id = row.member.node.initiative_id}
						<li aria-current={selected === id ? 'true' : undefined}>
							<button
								id="gate-row-{id}"
								type="button"
								class="entry"
								tabindex={id === anchor ? 0 : -1}
								onclick={() => onselect(id)}
								onkeydown={(event) => onRegisterKey(event, order)}
							>
								<span class="entry-head">
									<span class="mark">{id}</span>
									<span class="who">{row.member.node.name}</span>
								</span>
								<span class="entry-meta">
									{#if row.spec && row.spec.routes.writes.length > 0}
										<span class="writes">writes {row.spec.routes.writes.join(', ')}</span>
									{:else if row.spec}
										<span class="member" data-state="slack">declares no writes</span>
									{/if}
									<span class="who-runs">
										{row.member.node.harness} · {row.member.node.model}
									</span>
									<span class="rank">rank {row.member.depth}</span>
									{#if row.member.onCriticalPath}<span class="cp">critical path</span>{/if}
								</span>
								{#if row.lead}
									<span class="entry-brief">
										{row.lead}{#if row.truncated}…{/if}
									</span>
								{:else if !plan?.data}
									<span class="entry-brief member" data-state="slack">
										Brief unread; the plan did not answer.
									</span>
								{/if}
								{#if row.because}
									<span class="entry-because">{row.because}</span>
								{/if}
							</button>
						</li>
					{/each}
				</ol>
			</section>

			<!-- 6. Why refusing is the only alternative. Context for the decision,
			     so it reads before the control rather than under it. -->
			{#if !approved}
				<section>
					<p class="label rule-label">
						<span>Revision</span><span class="rule"></span><span>Not revisable</span>
					</p>
					<p class="prose">
						There is no revise action. The daemon exposes no route that re-proposes a
						plan — <code>POST /plans</code> creates a different plan rather than a new
						revision of this one — so a decomposition you do not want is refused by not
						approving it, and replaced by creating a plan from a better brief.
					</p>
				</section>
			{/if}
		</div>

		<!-- The one write, and the only thing on this sheet that is not a read.
		     It is pinned rather than sitting under the register: a decision the
		     operator has to hunt for at the bottom of a scroll is a decision
		     they make from the numbers they can still remember. -->
		<footer>
			<p class="label rule-label">
				<span>Decide</span><span class="rule"></span>
				<span class="member" data-state={approved ? 'seated' : 'slack'}>
					{approved ? 'Approved' : `Revision ${version}`}
				</span>
			</p>

			<!-- A refusal outranks the state of the world. If someone approved this
			     revision between the arm and the confirm, the operator's own write
			     still failed, and reading "already approved" where their action was
			     refused is the failed write presented as somebody's success. -->
			{#if decide.phase === 'failed'}
				<div role="alert">
					<!-- Red marks the break in one lead line; the sentence that explains
					     it is graphite prose. A daemon message can be long, and a long
					     red paragraph is out of world. -->
					<p class="lead member" data-state="failed">Not approved.</p>
					<p class="prose">
						{decide.message}
						{#if approved}
							This revision is approved — but not by this action, and not by you here.
						{/if}
					</p>
				</div>
				<p class="actions">
					<button class="act" type="button" onclick={disarm} disabled={approved}>
						{approved ? 'Nothing left to decide' : 'Try again'}
					</button>
				</p>
			{:else if approved && decide.phase === 'done'}
				<p class="prose member outcome" data-state="seated" role="status">
					{decide.message} {shape.readyOnApproval}
					{shape.readyOnApproval === 1 ? 'member is' : 'members are'} ready to run.
				</p>
			{:else if approved}
				<!-- Approved somewhere else: the CLI, another window, or before this
				     sheet opened. Say which revision, and offer nothing to press. -->
				<p class="prose member" data-state="seated" role="status">
					Revision {version} is already approved. This build appends approval once and
					the daemon refuses a second, so there is nothing left to decide here.
				</p>
			{:else if decide.phase === 'armed'}
				<p class="prose">
					Approving commits revision {version}: {shape.readyOnApproval}
					{shape.readyOnApproval === 1 ? 'member becomes' : 'members become'} ready and
					may be dispatched to {shape.lanes === 1 ? 'an agent' : `up to ${shape.lanes} agents`}.
					Approval is recorded once and cannot be withdrawn.
				</p>
				<p class="actions">
					<button class="act" type="button" bind:this={confirmEl} onclick={() => void confirm()}>
						Confirm revision {version}
					</button>
					<button class="act" type="button" onclick={disarm}>Cancel</button>
				</p>
			{:else}
				<p class="actions">
					<button
						class="act"
						type="button"
						onclick={arm}
						disabled={decide.phase === 'sending'}
						aria-busy={decide.phase === 'sending' || undefined}
					>
						{decide.phase === 'sending' ? 'Approving…' : `Approve revision ${version}`}
					</button>
				</p>
			{/if}
		</footer>
	{/if}
</aside>

<style>
	/* --- the sheet ----------------------------------------------------------
	   The drawer's geometry, unchanged: a plate laid over the ground at the
	   right edge, no scrim, no shadow, no entrance. It sits one layer below the
	   drawer so selecting a member from the register covers this sheet rather
	   than racing it for the slot, and `inert` hands the keyboard over with it. */
	.gate {
		--cut: 12px;
		position: fixed;
		inset: 0 0 0 auto;
		/* Above the layout chrome (10) and the field's seats (1), below the
		   drawer (20), which opens from here and returns to it. */
		z-index: 19;
		/* The drawer's exact slot, not a wider one. They are two occupants of a
		   single right-edge seat: selecting a member from the register puts the
		   drawer over this sheet, and a gate a few rem wider would show as a
		   strip of a second sheet rather than as one layer over another. */
		width: min(30rem, 100%);
		max-width: 100%;
		height: 100dvh;
		display: flex;
		flex-direction: column;
		padding: 0;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule);
		font: inherit;
		overflow: hidden;
		border-radius: 0 0 0 var(--cut);
	}
	/* Beats the UA's `[hidden]` rule, which `.gate`'s own display would win. */
	.gate[hidden] {
		display: none;
	}
	/* Overriding the shared chamfer means overriding its fallback in the same
	   breath, or the fallback still cuts both corners. See the Edge-Cut
	   Exception: a top-right cut against the browser frame reads as a notch. */
	@supports not (corner-shape: bevel) {
		.gate {
			border-radius: 0;
			clip-path: polygon(0 0, 100% 0, 100% 100%, var(--cut) 100%, 0 calc(100% - var(--cut)));
		}
	}

	header {
		flex: none;
		padding: 1.5rem 1.5rem 1.25rem;
		border-bottom: 1px solid var(--rule);
	}
	.headrow {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}
	h2 {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 70, 'wght' 620;
		font-weight: 620;
		text-transform: uppercase;
		letter-spacing: -0.01em;
		font-size: 2rem;
		line-height: 1;
		margin: 0;
		text-wrap: balance;
		min-width: 0;
	}
	.head-note {
		margin-top: 0.6rem;
	}

	.body {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: 0 1.5rem 2.5rem;
	}
	/* The decision does not scroll. A hairline above it, exactly as the header
	   carries one below: the sheet is a plate held between two rules. */
	footer {
		flex: none;
		padding: 1.1rem 1.5rem 1.25rem;
		border-top: 1px solid var(--rule);
	}
	footer .rule-label {
		margin-bottom: 0.7rem;
	}
	footer .actions {
		margin-top: 0.7rem;
	}
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
	.sub {
		margin-top: 1.25rem;
	}

	/* Ash draws slack and never sets text: a slack reading is graphite carrying
	   a dashed ash rule instead. */
	.member[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}
	.prose.member[data-state='slack'],
	.callout.member[data-state='slack'],
	.entry .member[data-state='slack'] {
		text-decoration: none;
	}
	.prose.member[data-state='slack'] {
		border-bottom: 1px dashed var(--ash);
		padding-bottom: 0.5rem;
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
		margin-top: 0.9rem;
	}
	.brief {
		margin: 0;
		padding: 0 0 0 0.9rem;
		border-left: 1px solid var(--rule);
		color: var(--ink);
		white-space: pre-line;
		overflow-wrap: anywhere;
	}
	code {
		background: var(--ground);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
		overflow-wrap: anywhere;
	}
	strong {
		color: var(--ink);
		font-weight: 500;
	}

	/* --- readouts, built exactly as the Run sheet's are --------------------- */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		margin: 0;
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

	/* --- callouts -----------------------------------------------------------
	   Not cards: each is a ruled entry whose kind is named in words, so the
	   ranking survives without colour and a scope collision is legible to a
	   reader who cannot see that it is red. */
	.callouts {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.callout {
		padding: 0.75rem 0;
		border-top: 1px solid var(--rule);
	}
	.callout:last-child {
		border-bottom: 1px solid var(--rule);
	}
	.callout-kind {
		margin: 0 0 0.3rem;
		color: var(--member-ink);
	}
	.callout[data-state='balanced'] .callout-kind,
	.callout[data-state='slack'] .callout-kind {
		color: var(--ink-2);
	}
	.callout[data-state='slack'] .callout-kind {
		border-bottom: 1px dashed var(--ash);
		display: inline-block;
	}
	/* Red marks the break in the kind above; the sentence explaining it is
	   graphite prose, never red. */
	.callout-text {
		margin: 0;
		max-width: 68ch;
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}
	.callout-about {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin: 0.45rem 0 0;
	}
	.pick-id {
		font: inherit;
		font-size: 0.8125rem;
		color: var(--ink);
		background: transparent;
		border: 0;
		border-bottom: 1px solid var(--rule-strong);
		padding: 0;
		cursor: pointer;
	}
	.pick-id:hover {
		color: var(--red);
		border-color: var(--red);
	}

	/* --- the register -------------------------------------------------------
	   Ruled rows, not a table: this carries prose per member, and the load
	   schedule beside the field is already the tabular rendering of placement. */
	.register {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.register li {
		border-top: 1px solid var(--rule);
	}
	.register li:last-child {
		border-bottom: 1px solid var(--rule);
	}
	.register li[aria-current='true'] {
		background: var(--red-quiet);
	}
	.entry {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		width: 100%;
		font: inherit;
		text-align: left;
		background: transparent;
		border: 0;
		padding: 0.75rem 0.5rem;
		cursor: pointer;
		color: var(--ink-2);
	}
	.entry-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.2rem 0.6rem;
	}
	.mark {
		color: var(--ink);
	}
	.who {
		color: var(--ink);
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.entry:hover .mark,
	.entry:hover .who {
		color: var(--red);
	}
	.entry-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 0.15rem 0.75rem;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.writes,
	.who-runs,
	.rank {
		overflow-wrap: anywhere;
	}
	/* The critical path is named at the weight it is drawn at, so the register
	   and the field say it the same way. */
	.cp {
		color: var(--ink);
		border-bottom: 2.5px solid var(--ink);
	}
	.entry-brief {
		max-width: 68ch;
		overflow-wrap: anywhere;
	}
	.entry-because {
		font-size: 0.8125rem;
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}

	/* --- the decision ------------------------------------------------------- */
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 0.9rem 0 0;
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
	.act:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}
	.act:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	.act.inline {
		margin-left: 0.4rem;
	}
	.lead {
		margin: 0 0 0.35rem;
		color: var(--member-ink);
	}
	.outcome {
		font-size: 0.8125rem;
		color: var(--member-ink);
	}
	.outcome[data-state='seated'] {
		color: var(--ink);
		max-width: 68ch;
	}

	/* A narrow desktop has no room for a sheet beside the field, and a seam
	   cannot carry the layer at that width — so the gate takes the whole
	   viewport and reads as the one thing on screen, exactly as the drawer does. */
	@media (max-width: 60rem) {
		.gate {
			width: 100%;
		}
		header {
			padding: 1.25rem 1rem 1rem;
		}
		.body {
			padding: 0 1rem 2rem;
		}
		footer {
			padding: 1rem 1rem 1.1rem;
		}
	}
</style>
