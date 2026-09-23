<script lang="ts">
	/*
	  Unit R2 — the initiative detail drawer. Its direction contract lives in
	  `.impeccable/surfaces/ui-src-lib-initiativedrawer-svelte.md`, not here.

	  The division with R1 is the whole design: the Run sheet's inline readout
	  keeps every fact about the member's *place in the plan*; this drawer carries
	  only the initiative itself. Nothing appears in both.

	  R4 joined here: checkpoint review sits directly under the blocking
	  statement, because for a member awaiting review the checkpoint *is* what
	  is in the way. Its own contract is in
	  `.impeccable/surfaces/ui-src-lib-checkpointreview-svelte.md`. The sheet
	  widens to reading width for it and this file owns that width, because the
	  sheet is this file's.

	  R6 joined here: the interventions sit directly under the blocking
	  statement, because the reading order an operator actually has is what is
	  wrong, then what I can do about it — everything below is evidence for that
	  choice. Its contract is in
	  `.impeccable/surfaces/ui-src-lib-interventions-svelte.md`.

	  Siblings deliberately absent, each named on screen where an operator would
	  look for it: budgets and burn-down (R8), grouped code-diff cohorts (R5),
	  pause/resume/cancel and recovery (R9). The memory shelf, leaf curation and
	  batched attention remain Library/Home concerns. R7's packet section joined
	  here after Attempts; its contract is in the design brief this unit was built
	  to, `.impeccable/surfaces/r7-design-brief.md`.
	*/
	import { tick, type Snippet } from 'svelte';
	import DrawerSeat from './DrawerSeat.svelte';
import type { SeatWidth } from './seat.svelte';
	import AsyncField from './AsyncField.svelte';
	import CheckpointReview from './CheckpointReview.svelte';
	import Interventions from './Interventions.svelte';
	import PacketInspector from './PacketInspector.svelte';
	import type { Resource } from './resource.svelte';
	import {
		daemon,
		DaemonError,
		type Attempt,
		type CheckpointReport,
		type Initiative,
		type Kitchen,
		type MemoryStatus,
		type Plan,
		type PlanGraph,
		type RecoveryAttempt,
		type Subtask,
		type Usage
	} from './daemon';
	import { currentBriefVersion } from './interventions';
	import type { Member } from './field';

	let {
		open,
		planId,
		id,
		member,
		plan,
		graph,
		report,
		approved,
		activity,
		failure,
		targetCheckpointId,
		staleAttempt,
		focusOnOpen,
		memoryStatus,
		kitchen,
		ondecided,
		onrecovery,
		onclose,
		historical = false,
		place,
		strip
	}: {
		open: boolean;
		/** True only when the *address* opened the drawer: its heading then
		    takes focus once it has rendered. A click on a seat does not move
		    focus — the operator is reading the field and the drawer is beside
		    them, so taking the caret would be imposed, not claimed. */
		focusOnOpen: boolean;
		planId: string;
		/** The initiative the drawer was opened for. Held apart from `member` so a
		    revision that drops it leaves the drawer open and saying so, rather
		    than emptying under the operator's hands. */
		id: string | null;
		/** That initiative's member in the current revision, or null once dropped. */
		member: Member | null;
		/** The third read: `GET /plans/{id}`, the folded plan. */
		plan: Resource<Plan> | null;
		/** Whether this revision has been approved. R3 owns approving it. */
		approved: boolean;
		/** Runtime observations seen on the live stream since this page opened. */
		activity: { at: string; kind: string }[];
		/** A failure reason caught live. The fold does not project it. */
		failure: string | null;
		/** A fleet deep link asks the existing review section to take focus. */
		targetCheckpointId: string | null;
		/** The plan-level recovery read, joined by initiative id; no second stale derivation. */
		staleAttempt: RecoveryAttempt | null;
		memoryStatus: Resource<MemoryStatus> | null;
		kitchen: Resource<Kitchen> | null;
		/** R1's graph, already read. R4 computes downstream blocking from it. */
		graph: PlanGraph;
		/** The fourth read: the checkpoint review lifecycle (R4). */
		report: Resource<CheckpointReport> | null;
		/** A verdict landed; the page re-reads what it changed. */
		ondecided: () => void;
		onrecovery: () => void;
		onclose: () => void;
		historical?: boolean;
		place?: Snippet;
		strip?: Snippet;
	} = $props();

	/* Not a <dialog>: the sheet expands alongside the field on selection and the
	   field stays interactive, so there is no modal state to reconcile. Escape
	   is wired by hand because only a modal dialog gets it for free.

	   Escape collapses the reader before it closes the sheet: at reading width
	   the reader is what you are in, and closing the whole drawer on the first
	   press would throw away the member as well as the document. */

	/* --- the reading width (R4) ---------------------------------------------
	   R3 settled one right-edge seat with two occupants and refused a third
	   sheet, so the reader is not a new surface: this same sheet widens from
	   30rem to the system's own 74rem reading measure and back. One element,
	   never remounted, so nothing in it loses focus or state on the way.

	   What *does* move is everything above the reader, because a 68ch measure
	   rewraps at the new width. So the section is re-pinned by hand: measure
	   its distance from the top of the scroll box, change the width, and put it
	   back where it was. Reading position is the point of the reader. */
	let width = $state<SeatWidth>('docked');
	const expanded = $derived(width === 'wide');
	let bodyEl = $state<HTMLElement | null>(null);
	let reviewEl = $state<HTMLElement | null>(null);

	/* `anchor` defaults to the checkpoint section, the reader R4 re-pins. R7's
	   packet section expands too — one sheet, one width, un-truncation
	   everywhere — and re-pins against itself, because measuring against the
	   checkpoint section would throw the operator to a different part of the
	   sheet. One parameter, not a new system. */
	async function setExpanded(next: boolean, anchor: HTMLElement | null = reviewEl) {
		const body = bodyEl;
		const before =
			body && anchor
				? anchor.getBoundingClientRect().top - body.getBoundingClientRect().top
				: null;
		width = next ? 'wide' : 'docked';
		await tick();
		if (before === null || !bodyEl || !anchor) return;
		const after = anchor.getBoundingClientRect().top - bodyEl.getBoundingClientRect().top;
		bodyEl.scrollTop += after - before;
	}
	async function widenToReview(): Promise<void> {
		await setExpanded(true);
		reviewEl?.scrollIntoView({ block: 'start' });
	}

	/* A new member is read at sheet width. Carrying the last one's reading mode
	   over would open a document nobody asked for over the field. */
	$effect(() => {
		void id;
		width = 'docked';
	});

	/* Arrival focus: claimed when the address opened the drawer, never when a
	   click did. The heading takes tabindex=-1 for exactly this. */
	let nameEl = $state<HTMLHeadingElement | null>(null);
	$effect(() => {
		if (!open || !focusOnOpen) return;
		void tick().then(() => nameEl?.focus());
	});

	/* The reverse link out: close the drawer, then put the caret on the seat
	   this member occupies in the field. One behaviour at every width — below
	   60rem the drawer covers the field, so focusing a seat behind it would
	   put the caret somewhere invisible. The selection survives, because
	   `onclose` clears only the drawer's own id. */
	async function showInField(): Promise<void> {
		const seat = id;
		onclose();
		await tick();
		if (seat) document.getElementById(`seat-${seat}`)?.focus();
	}

	let addressedCheckpoint = $state<string | null>(null);
	$effect(() => {
		const checkpoint = targetCheckpointId;
		if (!checkpoint) {
			addressedCheckpoint = null;
			return;
		}
		if (!open || checkpoint === addressedCheckpoint) return;
		/* The reader opens the member's CURRENT version only, so an address is
		   honoured as a reading only when it names that version: a real but
		   earlier version is said where an operator looking for it is reading,
		   never opened as if it were the current one — expanding the reader for
		   it would show a version the address does not name. An address naming
		   nothing is said the same way, and the reader is not expanded for it.
		   When the report has not answered yet, the effect re-runs when it does,
		   before giving up on the id. */
		if (!checkpointCurrent) return;
		addressedCheckpoint = checkpoint;
		void setExpanded(true).then(() => reviewEl?.scrollIntoView({ block: 'start' }));
	});

	/* Whether the addressed checkpoint id is recorded against this member in
	   the report this page read — any version of any reading of it counts. */
	const checkpointKnown = $derived(
		!!targetCheckpointId &&
			(report?.data?.initiatives ?? []).some(
				(view) =>
					view.initiative_id === id &&
					view.versions.some((version) => version.checkpoint_id === targetCheckpointId)
			)
	);
	/* Whether it is recorded as this member's current (last) version — the one
	   the reader opens. Known-but-earlier is a statement below, never a read. */
	const checkpointCurrent = $derived(
		!!targetCheckpointId &&
			(report?.data?.initiatives ?? []).some(
				(view) =>
					view.initiative_id === id &&
					view.versions[view.versions.length - 1]?.checkpoint_id === targetCheckpointId
			)
	);

	const initiative = $derived<Initiative | null>(
		id && plan?.data ? (plan.data.initiatives[id] ?? null) : null
	);

	/* Focus targets the most recent attempt that recorded a pane; an attempt can
	   start before herdr answers with one, and a failed run may never have. */
	const attempts = $derived<Attempt[]>(initiative?.attempts ?? []);
	const pane = $derived(
		[...attempts].reverse().find((attempt) => attempt.pane_ref)?.pane_ref ?? null
	);

	/* R7's section exposes one handle: the attempt plates' `Read this packet`
	   control selects the attempt in the Packet section and brings it into
	   view. Binding the instance, not an event bus — the section is mounted
	   below, inside the plan read, where the attempts live. */
	let packetInspector = $state<{ selectAttempt(attemptId: string): Promise<void> } | null>(
		null
	);

	type Focus = { phase: 'idle' | 'working' | 'done' | 'failed'; message: string };
	let focus = $state<Focus>({ phase: 'idle', message: '' });
	/* A new member is a new question; never carry the last one's answer over. */
	$effect(() => {
		void id;
		focus = { phase: 'idle', message: '' };
	});

	async function focusPane() {
		if (!id) return;
		focus = { phase: 'working', message: '' };
		try {
			const result = await daemon.focus(planId, id);
			focus = { phase: 'done', message: result.pane_ref };
		} catch (cause) {
			const failed =
				cause instanceof DaemonError
					? cause.message
					: 'Something in this build failed while asking the daemon to focus.';
			focus = { phase: 'failed', message: failed };
		}
	}

	/* --- what is in the way -------------------------------------------------
	   The one thing an operator opens a stalled member to read, so it is stated
	   first and it is never blank. Everything but a failure is derivable; a
	   failure's own sentence is not projected, and that is said rather than
	   shown as an absence of trouble. */
	type Blocking = { state: string; lead?: string; text: string };

	function blocking(m: Member): Blocking {
		const node = m.node;
		if (m.cancelled) {
			return {
				state: 'slack',
				text: 'Struck out of the structure. A cancelled member carries no load and nothing is waiting on it to.'
			};
		}
		if (node.state === 'failed') {
			/* R2 had to say the reason was unreadable after a reload: the fold kept
			   only `state = "failed"` and dropped the sentence. It is projected now
			   — `Initiative.failures`, one entry per recorded failure — so the last
			   recorded reason is read from the fold and the live frame is only the
			   fallback for a failure that has not folded yet. */
			const recorded = initiative?.failures ?? [];
			const last = recorded[recorded.length - 1];
			return {
				state: 'failed',
				lead:
					recorded.length > 1
						? `This member failed ${recorded.length} times.`
						: 'This member failed.',
				text:
					last?.reason ??
					failure ??
					'No reason has been recorded against this failure and none reached this page on the live stream. That is unread, not a failure without a cause.'
			};
		}
		if (node.state === 'paused') {
			return {
				state: 'balanced',
				text: 'Held. It keeps its place in the structure and carries no load while the hold is on; no new attempt starts here until it is released. Releasing it is below.'
			};
		}
		if (node.state === 'settled') {
			return { state: 'seated', text: 'Nothing. Its load transferred and its dependents were released.' };
		}
		if (node.state === 'running') {
			return {
				state: 'loaded',
				text: 'Nothing recorded. An attempt is under way, and nothing the daemon projects says whether the agent is waiting on you — if it has asked you something, it asked in its pane. Answering it is below.'
			};
		}
		if (!approved) {
			return {
				state: 'slack',
				text: 'The plan revision is not approved, so no member may start — this one included, whatever its dependencies say. Close this to read and approve the revision.'
			};
		}
		if (m.blockedBy.length > 0) {
			return {
				state: 'slack',
				text: `Waiting on ${m.blockedBy.join(', ')} — unsettled. Nothing starts here until every one of them has.`
			};
		}
		return {
			state: 'balanced',
			text: 'Nothing. Every dependency has settled and this member is ready to run.'
		};
	}

	/* --- subtasks ------------------------------------------------------------ */
	const SUBTASK_STATE: Record<Subtask['state'], string> = {
		todo: 'balanced',
		doing: 'loaded',
		done: 'seated',
		skipped: 'slack'
	};
	const SUBTASK_WORD: Record<Subtask['state'], string> = {
		todo: 'Not started',
		doing: 'Doing',
		done: 'Done',
		skipped: 'Skipped'
	};

	/* --- values --------------------------------------------------------------
	   Unknown reads as an em dash, never as zero and never as an empty string. */
	function when(value: string | null): string {
		if (!value) return '—';
		const at = new Date(value);
		return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
	}

	function attemptOutcome(attempt: Attempt): string {
		if (!attempt.checkpoint) return attempt.ended_at ? 'Ended' : 'In progress';
		return attempt.checkpoint.exit_code === null
			? 'Checkpoint recorded'
			: `Exit ${attempt.checkpoint.exit_code}`;
	}

	function elapsed(attempt: Attempt): string {
		if (!attempt.ended_at) return '—';
		const from = new Date(attempt.started_at).getTime();
		const to = new Date(attempt.ended_at).getTime();
		if (Number.isNaN(from) || Number.isNaN(to) || to < from) return '—';
		const seconds = Math.round((to - from) / 1000);
		if (seconds < 60) return `${seconds}s`;
		const minutes = Math.floor(seconds / 60);
		if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
		return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
	}

	const count = (n: number) => n.toLocaleString();

	/* A brief is written in a terminal and hard-wrapped for one. Those breaks are
	   the author's column width, not their meaning, so they are dropped and the
	   blank lines between paragraphs -- which do mean something -- are kept. */
	const paragraphs = (brief: string): string[] =>
		brief
			.split(/\n\s*\n/)
			.map((block) => block.replace(/\s*\n\s*/g, ' ').trim())
			.filter((block) => block.length > 0);

	/* Provenance is never dropped and never smoothed into sounding measured. */
	const REPORTED: Record<Usage['source'], string> = {
		harness: 'reported by the harness',
		provider: 'reported by the provider',
		gateway: 'reported by the gateway relay',
		tokenizer: 'counted by the tokenizer',
		estimate: 'estimated, not measured'
	};
</script>

<!--
  A sheet with no scrim: the field it expanded from stays fully legible and
  fully usable beside it, because dimming the drawing you are supervising is
  the wrong instinct. The hairline seam carries the separation on its own.
-->
{#if id}
	<DrawerSeat open={open && !!id} label="Member" tag={`${id}${historical ? ' · HISTORICAL' : ''}`} title={member ? member.node.name : id} titleId="drawer-name" {focusOnOpen} bind:width onclose={onclose} returnFocus={() => document.getElementById(`seat-${id}`)}>
		{#snippet tools()}
			<button type="button" aria-label="Locate in field (L)" title="Locate in field (L)" onclick={() => void showInField()}>
				<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2"/></svg>
			</button>
			{#if pane}
				<button type="button" aria-label="Focus this pane" title="Focus this pane" onclick={() => void focusPane()} disabled={focus.phase === 'working'}>
					<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12v10H2zM2 6h12M5 3v3M8 9v2M6.5 10h3"/></svg>
				</button>
			{/if}
		{/snippet}
		{#snippet strip()}{@render strip?.()}{/snippet}
		{#if place}{@render place()}{/if}
		<div class="body-content" bind:this={bodyEl}>
			{#if !member}
				<!-- A live re-read dropped it. Say so and stay open: closing a panel
				     under the operator's hands loses their place for them. -->
				<p class="prose">
					<strong>{id}</strong> is no longer in this revision of the plan. It was here when
					you opened it and a live re-read has since replaced the graph, so there is nothing
					left to read. Close this to return to the field.
				</p>
			{:else}
				{@const held = blocking(member)}
				{@const failures = initiative?.failures ?? []}
				{@const activityUnread = historical || activity.length === 0}
				<section>
				<p class="label rule-label">
					<span>In the way</span><span class="rule"></span>
					<span class="member" data-state={held.state}
						>{member.cancelled ? 'cancelled' : member.node.state}</span
					>
				</p>
				{#if held.lead}
					<p class="lead member" data-state={held.state}>{held.lead}</p>
				{/if}
				<p class="prose held member" data-state={held.state}>{held.text}</p>
				{#if activityUnread}<p class="gloss activity-gloss">Activity · Unread</p>{/if}
				{#if staleAttempt}
					<p class="prose stale-recovery"><span class="stale-lead member" data-state="failed">Attempt <code>{staleAttempt.attempt_id}</code> is stale: this daemon no longer owns it.</span> <span>Whether its recorded pane is still alive is unknown until the plan-level reconciliation probes it.</span></p>
					<button class="act recovery-link" type="button" onclick={onrecovery}>Read the recovery section</button>
				{/if}
				{#if failures.length > 0}
					{@const lastFailure = failures[failures.length - 1]}
					{#if lastFailure}
					<p class="label rule-label evidence-label"><span>Preserved evidence</span><span class="rule"></span><span>{lastFailure.evidence.length || 'None'}</span></p>
					{#if lastFailure.evidence.length > 0}
						<p class="prose quiet">Paths to diagnostic artifacts the run preserved before anything was cleaned up. They are references recorded in this plan's history — this build can name them and cannot open them; nothing here serves their bytes. Read them beside the terminal.</p>
						<ul class="evidence">{#each lastFailure.evidence as path (path)}<li><code>{path}</code></li>{/each}</ul>
					{:else}
						{#if lastFailure.reason.startsWith('recovery:') || lastFailure.reason.startsWith('daemon death: pane')}<p class="prose quiet">This failure was recorded by reconciliation, so nothing new was preserved with it. The worktree the attempt was given is still there.</p>{:else}<p class="prose quiet">This failure recorded no preserved artifact. Nothing was written for it to point at, which is an absence of evidence rather than evidence that went missing.</p>{/if}
					{/if}
					{/if}
				{/if}
			</section>

			<!-- R6, second in the read: what is wrong, then what can be done about
			     it. Like R4 below, it mounts outside the plan `AsyncField` so a
			     failed plan read reports what it could not decide from rather than
			     vanishing behind a broken load path. -->
			<Interventions
				{historical}
				{planId}
				id={id ?? ''}
				{initiative}
				plan={plan?.data ?? null}
				{memoryStatus}
				{approved}
				onchanged={ondecided}
				onreview={() => {
					/* The redirect path's one reuse of R4: reading the evidence on a
					   version you are about to continue from is the checkpoint
					   section's job, so this opens it rather than building a second
					   reader inside the redirect panel. */
					void widenToReview();
				}}
			/>

			<!-- R4, third in the read: for a member awaiting review the checkpoint
			     is what is in the way, so it is stated where that question is
			     asked. It sits outside the plan read on purpose — it joins two
			     reads and either one can fail, so it must be able to say which
			     half it is missing rather than disappear behind a broken load
			     path that only cost it the manifests. -->
			<div bind:this={reviewEl} data-seat-anchor>
				{#if targetCheckpointId && report?.data && !checkpointKnown}
					<!-- A stale or hand-edited address. Said where an operator looking
					     for that checkpoint is reading, and the reader is not expanded
					     for an id the report never returned. -->
					<p class="prose quiet member" data-state="slack" role="status">
						<code>{targetCheckpointId}</code> is not recorded against this member in
						the checkpoint report this page read, so there is no version of it to
						open. The member's own versions are below, unchanged.
					</p>
				{:else if targetCheckpointId && report?.data && !checkpointCurrent}
					<!-- A real version the reader cannot open: the reader has no
					     version switcher, so expanding it would show a different
					     version than the address names. Said, not opened. -->
					<p class="prose quiet member" data-state="slack" role="status">
						<code>{targetCheckpointId}</code> is an earlier version of this member. The reader opens the
						current version only, so it has not been opened for this address — the
						earlier versions and their decisions are listed below.
					</p>
				{/if}
				<CheckpointReview
					{historical}
					{planId}
					id={id ?? ''}
					{initiative}
					{graph}
					{report}
					{expanded}
					onexpand={(next) => void setExpanded(next)}
					{ondecided}
				/>
			</div>

			{#if plan}
				<AsyncField resource={plan} reading="this initiative" onretry={() => void plan?.load()}>
					{#snippet children(folded: Plan)}
						{#if !initiative}
							<p class="prose">
								<strong>{id}</strong> is not in revision {folded.version} of this plan. The
								field still holds it because the graph and the plan were read a moment apart;
								close and reopen to read what is there now.
							</p>
						{:else}
							{@const spec = initiative.spec}
							{@const briefBlocks = paragraphs(initiative.brief_versions.at(-1)?.brief ?? spec.brief)}
							{@const contract = spec.contract}
							{@const done = initiative.subtasks.filter((s) => s.state === 'done').length}

							{@const briefVersion = currentBriefVersion(initiative)}
							{@const ceiling = spec.policy.max_attempts}
							<section>
								<p class="label rule-label">
									<span>Brief</span><span class="rule"></span>
									<span class="member" data-state={briefVersion > 1 ? 'balanced' : 'seated'}>
										{briefVersion === 1
											? 'As planned'
											: `v${briefVersion} of ${briefVersion}`}
									</span>
								</p>
											<details class="brief-fold"><summary>{briefBlocks[0] ?? 'Read the whole brief'}</summary>
								{#each briefBlocks.slice(1) as block, at (at)}<p class="prose">{block}</p>{/each}
							</details>
								{#if initiative.brief_versions.length > 0}
									<!-- Redirects are history, not a replacement: version 1 is the
									     planner's and is never stored here, so it is named from the
									     spec and the appended versions are listed under it. An
									     attempt records the version it ran on, so nothing here
									     rewrites what an earlier run was told. -->
									<ol class="versions">
										{#each initiative.brief_versions as version (version.version)}
											<li class="version-row member" data-state="seated">
												<span class="ring" aria-hidden="true"></span>
												<div class="version-text">
													<p class="value">
														v{version.version} · {version.by} · {when(version.at)}
													</p>
													<p class="prose quiet">
														{version.reason
															? version.reason
															: 'No reason was recorded with this redirect.'}
													</p>
												</div>
											</li>
										{/each}
									</ol>
									<p class="prose quiet foot">
										Version 1 is the planner's brief and is not stored as a version;
										it is what this member was proposed with. Redirecting again
										appends, and nothing here is ever removed.
									</p>
								{/if}
							</section>

							<section>
								<p class="label rule-label">
									<span>Contract</span><span class="rule"></span>
									<span class="member" data-state={contract ? 'seated' : 'slack'}
										>{contract ? contract.id : 'None attached'}</span
									>
								</p>
								{#if !contract}
									<p class="prose quiet">
										No contract is attached, so settlement applies the clean-evidence rule
										only: nothing beyond a clean exit is required and no declared write scope
										is enforced. The routes below are still declared and are what the field
										computes contention from.
									</p>
								{/if}
								<dl class="readout plate">
									<div>
										<dt class="label">Role</dt>
										<dd class="value">{contract ? contract.role : '—'}</dd>
									</div>
									<div>
										<dt class="label">Approval</dt>
										<dd class="value member" data-state={spec.approval === 'required' ? 'balanced' : 'seated'}>
											{spec.approval === 'required' ? 'Review required' : 'Automatic'}
										</dd>
										<p class="gloss">
											{spec.approval === 'required'
												? 'settlement waits on a reviewer; the checkpoint section above is where you are one'
												: 'clean evidence settles it and releases its dependents'}
										</p>
									</div>
									<div>
										<dt class="label">Writes</dt>
										<dd class="paths">
											{#if contract && !contract.allow_writes}
												<span class="member" data-state="failed">No writes permitted</span>
											{:else if spec.routes.writes.length === 0}
												Declares none.
											{:else}
												{#each spec.routes.writes as path (path)}<code>{path}</code>{/each}
											{/if}
										</dd>
									</div>
									<div>
										<dt class="label">Reads</dt>
										<dd class="paths">
											{#if spec.routes.reads.length === 0}
												Declares none.
											{:else}
												{#each spec.routes.reads as path (path)}<code>{path}</code>{/each}
											{/if}
										</dd>
									</div>
									{#if contract}
										<div>
											<dt class="label">Required checks</dt>
											<dd class="paths">
												{#if contract.required_checks.length === 0}
													None required.
												{:else}
													{#each contract.required_checks as check (check)}<code>{check}</code>{/each}
												{/if}
											</dd>
										</div>
										<div>
											<dt class="label">Required artifacts</dt>
											<dd class="paths">
												{#if contract.required_paths.length === 0}
													None required{contract.require_patch ? ', but a patch must be present.' : '.'}
												{:else}
													{#each contract.required_paths as path (path)}<code>{path}</code>{/each}
													{#if contract.require_patch}<span class="quiet">and a patch</span>{/if}
												{/if}
											</dd>
										</div>
										<div class="wide">
											<dt class="label">Commands</dt>
											<dd class="paths">
												{#if contract.allowed_commands === null}
													Unrestricted — no command policy is declared, which is not the same
													as an empty list.
												{:else if contract.allowed_commands.length === 0}
													No command may run.
												{:else}
													{#each contract.allowed_commands as command (command)}<code>{command}</code>{/each}
												{/if}
											</dd>
										</div>
									{/if}
								</dl>
							</section>

							<section>
								<p class="label rule-label">
									<span>Subtasks</span><span class="rule"></span>
									<span
										class="member"
										data-state={initiative.subtasks.length === 0
											? 'slack'
											: done === initiative.subtasks.length
												? 'seated'
												: 'balanced'}
									>
										{initiative.subtasks.length === 0
											? 'None declared'
											: `${done} of ${initiative.subtasks.length} done`}
									</span>
								</p>
								{#if initiative.subtasks.length === 0}
									<p class="prose quiet">
										The planner declared no subtasks for this initiative, so there are no steps
										to advance through — not zero progress through steps that exist.
									</p>
								{:else}
									<ol class="steps">
										{#each initiative.subtasks as sub (sub.id)}
											<li class="step member" data-state={SUBTASK_STATE[sub.state]}>
												<span class="ring" aria-hidden="true"></span>
												<span class="step-text">{sub.brief}</span>
												<span class="step-state">{SUBTASK_WORD[sub.state]}</span>
											</li>
										{/each}
									</ol>
								{/if}
							</section>

							<section>
								<p class="label rule-label">
									<span>Attempts</span><span class="rule"></span>
									<span
										class="member"
										data-state={attempts.length === 0
											? 'slack'
											: attempts.length >= ceiling
												? 'failed'
												: 'balanced'}
									>
										{attempts.length === 0
											? `None of ${count(ceiling)} started`
											: `${count(attempts.length)} of ${count(ceiling)}`}
									</span>
								</p>
								{#if initiative.assignment_override}
									<p class="prose quiet">
										A new attempt would run on
										<strong
											>{initiative.assignment_override.harness}/{initiative
												.assignment_override.model}</strong
										>, not the planner's {spec.assignment.harness}/{spec.assignment.model}.
										The override applies to the next attempt only; every attempt below
										keeps the pair it actually ran under.
									</p>
								{/if}
								{#if initiative.failures.length > 0}
									<!-- Failures are the fold's own record, one per recorded failure and
									     bounded by the ceiling. They are listed whole rather than
									     collapsed into the latest, because a retry's value is read from
									     whether the second failure is the same failure. -->
									<ol class="versions">
										{#each initiative.failures as recorded, at (at)}
											<li class="version-row member" data-state="failed">
												<span class="ring" aria-hidden="true"></span>
												<div class="version-text">
													<p class="prose">{recorded.reason}</p>
													{#if recorded.evidence.length > 0}
														<p class="paths">
															{#each recorded.evidence as path (path)}<code>{path}</code>{/each}
														</p>
													{:else}
														<p class="prose quiet">No evidence was preserved with it.</p>
													{/if}
												</div>
											</li>
										{/each}
									</ol>
								{/if}
								{#if attempts.length === 0}
									<p class="prose quiet">
										No attempt has started, so there is no worktree, no pane and no usage to
										read.
									</p>
								{:else}
									{#each attempts as attempt, at (attempt.id)}
										{@const checkpoint = attempt.checkpoint}
										<details class="attempt plate" open={at === attempts.length - 1}>
							<summary>Attempt {attempt.id} · {attemptOutcome(attempt)} · {when(attempt.started_at)} → {when(attempt.ended_at)}</summary>
											<p class="label rule-label">
												<span>Attempt {at + 1}</span><span class="rule"></span
												><span class="member" data-state={attempt.origin === 'retry' ? 'balanced' : 'seated'}
													>{attempt.origin === 'retry' ? 'Retry' : 'First run'}</span
												>
											</p>
											<dl class="readout plate">
												<div>
													<dt class="label">Reserved by</dt>
													<dd class="value">{attempt.by}</dd>
													<p class="gloss">
														{attempt.origin === 'retry'
															? 'an operator retry of failed work'
															: 'an ordinary run, started by the daemon'}
														· {attempt.id}
													</p>
												</div>
												<div>
													<dt class="label">Ran as</dt>
													<dd class="value">{attempt.assignment.harness}</dd>
													<p class="gloss">{attempt.assignment.model}</p>
												</div>
												<div>
													<dt class="label">Brief</dt>
													<dd class="value">v{attempt.brief_version}</dd>
													<p class="gloss">
														{attempt.brief_version === briefVersion
															? 'the version new attempts still run on'
															: 'superseded by a later redirect; this attempt kept it'}
													</p>
												</div>
												<div>
													<dt class="label">Started</dt>
													<dd class="value">{when(attempt.started_at)}</dd>
												</div>
												<div>
													<dt class="label">Ran for</dt>
													<dd class="value member" data-state={attempt.ended_at ? 'seated' : 'slack'}>
														{elapsed(attempt)}
													</dd>
													<p class="gloss">
														{#if attempt.ended_at}
															closed when its checkpoint was recorded
														{:else if member.node.state === 'running'}
															still open — this attempt has not been closed
														{:else}
															never closed; only a recorded checkpoint ends an attempt, so a
															failed run has no end time
														{/if}
													</p>
												</div>
												<div>
													<dt class="label">Packet</dt>
													<dd class="value member" data-state={attempt.packet_tokens > 0 ? 'seated' : 'slack'}>
														{attempt.packet_tokens > 0 ? count(attempt.packet_tokens) : '—'}
													</dd>
													<p class="gloss">
														{#if attempt.packet_snapshot}
															tokens of context this attempt was handed, section by
															section, below
														{:else}
															tokens of context this attempt was handed. No section receipt
															was persisted with it, so what was in it cannot be read.
														{/if}
													</p>
												</div>
												<div>
													<dt class="label">Memory</dt>
												<dd class="value member" data-state={attempt.memory_leaf_ids.length > 0 ? 'seated' : 'slack'}>
													{attempt.memory_mode} · {attempt.memory_leaf_ids.length} {attempt.memory_leaf_ids.length === 1 ? 'leaf' : 'leaves'}
												</dd>
												<p class="gloss">what this attempt's packet carried; the inspector below reads versions and receipts</p>
											</div>
											<div>
												<dt class="label">Usage</dt>
													<dd class="value member" data-state={checkpoint?.usage ? 'seated' : 'slack'}>
														{#if checkpoint?.usage}
															{count(checkpoint.usage.input_tokens + checkpoint.usage.output_tokens)}
														{:else}
															—
														{/if}
													</dd>
													<p class="gloss">
														{#if checkpoint?.usage}
															{count(checkpoint.usage.input_tokens)} in ·
															{count(checkpoint.usage.output_tokens)} out,
															{REPORTED[checkpoint.usage.source]}
														{:else}
															unread — no checkpoint has reported this attempt's usage
														{/if}
													</p>
												</div>
												<div>
													<dt class="label">Exit</dt>
													<dd
														class="value member"
														data-state={checkpoint?.exit_code == null
															? 'slack'
															: checkpoint.exit_code === 0
																? 'seated'
																: 'failed'}
													>
														{checkpoint?.exit_code ?? '—'}
													</dd>
												</div>
												<div class="wide">
													<dt class="label">Worktree</dt>
													<dd class="paths">
														{#if attempt.worktree_ref}
															<code>{attempt.worktree_ref}</code>
														{:else}
															<span class="member" data-state="slack"
																>This attempt recorded no worktree.</span
															>
														{/if}
													</dd>
												</div>
												{#if checkpoint}
													<div class="wide">
														<dt class="label">Checkpoint</dt>
														<dd class="paths">
															<code>{checkpoint.id}</code>
															<span class="quiet">
																{checkpoint.changed_paths.length === 0
																	? 'no changed paths recorded'
																	: `${count(checkpoint.changed_paths.length)} changed ${checkpoint.changed_paths.length === 1 ? 'path' : 'paths'}`}.
																Its manifest, checks and review are read in the checkpoint
																section above.
															</span>
														</dd>
													</div>
												{/if}
											</dl>
											{#if attempt.packet_snapshot}
												<p class="actions">
													<button
														class="act"
														type="button"
														onclick={() => void packetInspector?.selectAttempt(attempt.id)}
													>
														Read this packet
													</button>
												</p>
											{/if}
										</details>
									{/each}
									<p class="prose quiet foot">
										{#if attempts.length >= ceiling}
											This member has used all {count(ceiling)} of its attempts. The ceiling
											is a fold invariant rather than daemon memory, so no further attempt
											can start here — not by retry, not by replay, not by a direct append.
										{:else}
											{count(ceiling - attempts.length)} of {count(ceiling)}
											{ceiling - attempts.length === 1 ? 'attempt remains' : 'attempts remain'}.
											A retry appends here and keeps everything above it; restarting the
											process re-issues a live attempt's command without appending
											anything, which is why the two are separate controls.
										{/if}
									</p>
								{/if}
							</section>

							<PacketInspector
								historical={historical}
								bind:this={packetInspector}
								{planId}
								id={id ?? ''}
								{initiative}
								plan={plan?.data ?? null}
								{expanded}
								onexpand={(next, anchor) => void setExpanded(next, anchor ?? reviewEl)}
								{memoryStatus}
								{kitchen}
							/>

							{#if !activityUnread}
							<section>
								<p class="label rule-label">
									<span>Activity</span><span class="rule"></span>
									<span class="member" data-state="slack">{count(activity.length)} this session</span>
								</p>
								<ul class="feed">
									{#each activity as event (event.at + event.kind)}
										<li><span class="feed-at">{when(event.at)}</span><span class="feed-kind">{event.kind}</span></li>
									{/each}
								</ul>
								<p class="prose quiet foot">Only what has reached this page since it opened. Everything before it is unread, not absent.</p>
							</section>
						{/if}

							{#if !historical && focus.phase !== 'idle'}
							<section class="terminal-outcome">
								{#if focus.phase === 'working'}
									<p class="member outcome" data-state="balanced" role="status">Focusing pane…</p>
								{:else if focus.phase === 'done'}
									<p class="member outcome" data-state="seated" role="status">herdr focused <code>{focus.message}</code>.</p>
								{:else}
									<p class="member outcome" data-state="failed" role="alert">Not focused: {focus.message}</p>
								{/if}
							</section>
						{/if}
						{/if}
					{/snippet}
				</AsyncField>
				{:else}
					<p class="prose">
						No plan is addressed, so there is nothing to read this initiative from.
					</p>
				{/if}
			{/if}
		</div>
	</DrawerSeat>
{/if}

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
	/* Ash draws slack and never sets text: a slack reading is graphite carrying
	   a dashed ash rule instead. */
	.member[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash);
		text-decoration-thickness: 1px;
		text-underline-offset: 0.3em;
	}
	.prose.member[data-state='slack'],
	.paths .member[data-state='slack'] {
		text-decoration: none;
		border-bottom: 1px dashed var(--ash);
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
	.evidence-label { margin-top: 1.25rem; }
	.evidence { list-style: none; margin: .5rem 0 0; padding: 0; }
	.evidence li { border-bottom: 1px solid var(--rule); padding: .45rem 0; overflow-wrap: anywhere; }
	.evidence code { color: var(--ink); }
	.stale-recovery { margin-top: .85rem; }
	.recovery-link { margin-top: .65rem; }
	/* One line carries the break; the sentence explaining it does not. */
	.lead {
		margin: 0 0 0.5rem;
		color: var(--member-ink);
	}
	.held {
		color: var(--ink-2);
	}
	.held[data-state='seated'] {
		color: var(--ink);
	}
	/* A long unbroken path in a brief wraps rather than widening the sheet. */
	.brief-fold p {
		overflow-wrap: anywhere;
	}
	.brief-fold p + p {
		margin-top: 0.8rem;
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
	.paths {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
		color: var(--ink-2);
		font-size: 0.8125rem;
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
	strong {
		color: var(--ink);
		font-weight: 500;
	}

	/* --- subtasks: the seat ring vocabulary, unchanged ---------------------- */
	.steps {
		position: relative;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	/* The subtasks are a chain, so they are drawn as one: a 1px carbon member
	   down the ring column, overshooting the last node because a line that stops
	   exactly at content is a list rule, not a member. The rings knock out of it
	   in plate, exactly as a seat knocks out of its lane run. */
	.steps::before {
		content: '';
		position: absolute;
		left: 5px;
		top: 0.85rem;
		bottom: -0.55rem;
		width: 1px;
		background: var(--member-line);
	}
	.step {
		position: relative;
		display: grid;
		grid-template-columns: 11px minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.55rem 0.65rem;
		padding: 0.5rem 0;
		color: var(--ink-2);
	}
	.step[data-state='seated'] .step-text {
		color: var(--ink);
	}
	.step-text {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.step-state {
		flex: none;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.step[data-state='loaded'] .step-state {
		color: var(--red);
	}
	.step[data-state='skipped'] .step-state,
	.step[data-state='slack'] .step-state {
		border-bottom: 1px dashed var(--ash);
	}
	.ring {
		position: relative;
		align-self: center;
		width: 11px;
		height: 11px;
		border: 1.5px solid var(--member-ink);
		border-radius: 50%;
		background: var(--plate);
	}
	.step[data-state='slack'] .ring {
		border-style: dashed;
	}
	.step[data-state='loaded'] .ring {
		background: var(--red);
	}
	.step[data-state='seated'] .ring {
		background: var(--seat);
	}
	/* Ready is the one state an operator acts on, and a dashed border against a
	   solid one is invisible at 11px, so a charged member carries a pip. */
	.step[data-state='balanced'] .ring::before {
		content: '';
		position: absolute;
		inset: 2px;
		border-radius: 50%;
		background: currentColor;
	}

	/* --- redirects and failures: the same chain, at record scale -------------
	   Both are ordered histories, so both are drawn as chains rather than listed
	   as rows: rings on a 1px carbon run that overshoots the last one, knocking
	   out in the plate they sit on. The Member-Runs-Through Rule and the
	   Knock-Out Rule, unchanged, exactly as the subtasks above use them. */
	.versions {
		position: relative;
		list-style: none;
		margin: 0.9rem 0 0;
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
	}
	.version-row[data-state='failed'] .ring {
		/* The load path is discontinuous: the ring is cut open left and right,
		   exactly as a failed seat is drawn in the field. */
		border-left-color: transparent;
		border-right-color: transparent;
	}
	.version-row[data-state='seated'] .ring {
		background: var(--seat);
	}
	.version-text {
		min-width: 0;
	}
	.version-text .value {
		margin: 0 0 0.2rem;
		color: var(--ink);
		overflow-wrap: anywhere;
	}
	.version-text .paths {
		margin: 0.3rem 0 0;
	}

	/* --- attempts ----------------------------------------------------------- */
	.attempt {
		--cut: 12px;
		border: 1px solid var(--rule);
		padding: 1rem 1rem 1.1rem;
		background: var(--plate);
	}
	.attempt + .attempt {
		margin-top: 0.9rem;
	}
	.attempt summary {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		cursor: pointer;
	}
	.attempt .rule-label {
		margin-bottom: 0.75rem;
	}
	.attempt .readout {
		border-color: var(--rule);
	}

	/* --- activity ----------------------------------------------------------- */
	.feed {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.feed li {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem 0.75rem;
		padding: 0.4rem 0;
		border-bottom: 1px solid var(--rule);
		color: var(--ink-2);
		font-size: 0.8125rem;
	}
	.feed-kind {
		color: var(--ink);
		overflow-wrap: anywhere;
	}

	/* --- the one action ----------------------------------------------------- */
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
	.outcome {
		font-size: 0.8125rem;
		color: var(--member-ink);
	}
	.outcome[data-state='seated'] {
		color: var(--ink);
	}

	/* A narrow desktop has no room for a sheet beside the field, and a seam
	   cannot carry modality at that width -- so the drawer takes the whole
	   viewport and reads as the one thing on screen. */
</style>
