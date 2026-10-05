<script lang="ts">
	/*
	  Unit R4 — checkpoint review, inside R2's drawer. Its direction contract
	  lives in `.impeccable/surfaces/ui-src-lib-checkpointreview-svelte.md`.

	  The whole surface is one reading order at two widths. Expanding does not
	  reveal new sections and does not move anything: it un-truncates the lists
	  that were capped, so an operator who expands mid-read is looking at the
	  same document, longer. That is what makes reading position cheap to keep.

	  R6 joined the drawer above this section, so what to do about a member —
	  retry, restart, reassign, redirect, nudge, answer — is a real control now
	  and is named as one here rather than as a gap.

	  Deliberately absent, each named on screen where an operator would look:
	  R5 replaced the flat artifact list with the grouped walkthrough: the
	  daemon's own grouping of the same paths, read as overview before detail,
	  with the dropped half of the comparison under the base version's own
	  cohort names. Its decisions live in
	  `.impeccable/surfaces/r5-design-brief.md`.

	  Deliberately absent, each named on screen where an operator would look:
	  packet contents (R7), verification PASS/WARN/BLOCK visualization (R15).

	  What this build cannot show, and says so rather than implying otherwise:
	  file content. `patch_path` is a reference to bytes on disk that the daemon
	  serves no route for, and `changes_since_approved` is a set subtraction
	  over path names. Every comparison here is a change list — the walkthrough
	  groups the paths and stops at the file name.
	*/
	import {
		daemon,
		DaemonError,
		type CheckpointReport,
		type Contract,
		type Initiative,
		type PlanGraph,
		type Verdict,
		type Walkthrough
	} from './daemon';
	import type { Resource } from './resource.svelte';
	import Button from './Button.svelte';
	import Icon from './Icon.svelte';
	import {
		allowed,
		artifactsOf,
		changesOf,
		checksOf,
		consumersOf,
		DECISION_STATE,
		DECISION_WORD,
		impactOf,
		refused,
		reviewOf,
		summarize,
		VERDICT_WORD,
		versionsOf,
		walkthroughOf,
		type Version,
		type WalkthroughView
	} from './review';

	let {
		planId,
		id,
		initiative,
		graph,
		report,
		expanded,
		onexpand,
		ondecided,
		oncompare,
		historical = false
	}: {
		planId: string;
		id: string;
		/** From the folded plan: the manifests. `null` when that read has not answered. */
		initiative: Initiative | null;
		/** R1's graph, already on screen — the downstream sentence is computed from it. */
		graph: PlanGraph;
		/** The fourth read: `GET /plans/{id}/checkpoints`, the review lifecycle. */
		report: Resource<CheckpointReport> | null;
		expanded: boolean;
		onexpand: (next: boolean) => void;
		/** A verdict landed; the page re-reads everything it changed. */
		ondecided: () => void;
		/** Compare opens the dock's Diff tab; the dock owns tabs. */
		oncompare?: () => void;
		historical?: boolean;
	} = $props();

	/** Collapsed, a list this long stops and says how much it is holding back. */
	const CAP = 5;

	const view = $derived(historical ? null : reviewOf(report?.data ?? null, id));
	const versions = $derived<Version[]>(versionsOf(initiative, view, historical));
	const current = $derived<Version | null>(versions[versions.length - 1] ?? null);
	const prior = $derived<Version[]>(versions.slice(0, -1));
	const contract = $derived<Contract | null>(initiative?.spec.contract ?? null);
	const policy = $derived(initiative?.spec.approval ?? view?.policy ?? 'automatic');
	/* Named `nodeState`, not `state`: a local called `state` shadows the rune. */
	const nodeState = $derived(initiative?.state ?? view?.state ?? 'pending');
	const summary = $derived(summarize(versions, view, policy));

	/** The version a change list is read against: the latest ever approved. */
	const base = $derived<Version | null>(
		view?.approved_checkpoint_id
			? (versions.find((v) => v.id === view.approved_checkpoint_id) ?? null)
			: null
	);

	/** The grouped walkthrough of the version being read — the daemon's own
	 *  projection, marked against the approved base where one exists. */
	const walk = $derived<WalkthroughView | null>(
		current ? walkthroughOf(current, base, contract) : null
	);

	/* Cohort open state, keyed by version id and cohort name — two versions
	   share cohort names, so a bare name would remember one version's state
	   onto another. A cohort that vanishes on a re-read drops its entry
	   harmlessly; one that reappears returns to its remembered state. Default:
	   all cohorts open when the version holds CAP paths or fewer, all closed
	   above it — a small change reads whole, a large one opens as an overview. */
	let cohortsOpen = $state<Record<string, boolean>>({});
	const cohortOpen = (versionId: string, name: string, total: number): boolean =>
		cohortsOpen[`${versionId}!${name}`] ?? total <= CAP;
	function toggleCohort(versionId: string, name: string, total: number): void {
		const key = `${versionId}!${name}`;
		cohortsOpen[key] = !(cohortsOpen[key] ?? total <= CAP);
	}

	const consumers = $derived(
		historical ? [] : consumersOf(graph, id, report?.data?.attention ?? [])
	);
	/* Only the current version's contract is validated by the report; a prior
	   version's violations are not projected and are not guessed at here. */
	const violations = $derived<string[]>(historical ? [] : view?.violations ?? []);

	/* --- the decision --------------------------------------------------------
	   Three writes, three consequences, and none of them can be withdrawn by
	   pressing the same control again. So a verdict is armed, read, and then
	   confirmed -- and the arm step is where the downstream sentence and the
	   reason field appear, because that is when they change the answer. */
	type Decide =
		| { phase: 'idle' }
		| { phase: 'armed'; verdict: Verdict }
		| { phase: 'sending'; verdict: Verdict }
		| { phase: 'failed'; verdict: Verdict; message: string }
		| { phase: 'done'; verdict: Verdict; message: string };

	let decide = $state<Decide>({ phase: 'idle' });
	let reason = $state('');
	let confirmEl = $state<HTMLButtonElement | null>(null);

	/* A different member, or a different version of it, is a different question.
	   The decision is deliberately *not* in this key, and that is the whole
	   point: a verdict changes the decision, so keying on it would reset the
	   sheet on the very re-read the operator's own write triggered and wipe the
	   confirmation of it -- R3's bug, arrived at from the other side.

	   A verdict decided somewhere else while this sheet is open leaves an arm
	   pointing at a version that has moved; confirming it is refused by the
	   fold and reported as a failure, which is the right answer. The readouts
	   above have already re-read and say what is true. */
	let asked = $state<string | null>(null);
	$effect(() => {
		const key = `${id}@${current?.id ?? '-'}`;
		if (asked === key) return;
		asked = key;
		decide = { phase: 'idle' };
		reason = '';
		/* The walkthrough's open state resets on the same key and nothing else:
		   a different member, or a new current version, is a different question;
		   a re-read the operator's own write triggered is not. */
		cohortsOpen = {};
	});

	/** Blocking verdicts need a sentence: a held member is somebody's next task. */
	const REQUIRES_REASON: Verdict[] = ['reject', 'changes'];
	/* Declared here, not cast inline: `as` in an `{#each}` head is the block's
	   own keyword and a type assertion there does not parse. */
	const EVERY_VERDICT: Verdict[] = ['approve', 'changes', 'reject'];
	const needsReason = $derived(
		decide.phase === 'armed' && REQUIRES_REASON.includes(decide.verdict)
	);
	const reasonReady = $derived(!needsReason || reason.trim().length > 0);

	function arm(verdict: Verdict) {
		decide = { phase: 'armed', verdict };
		queueMicrotask(() => confirmEl?.focus());
	}
	function disarm() {
		decide = { phase: 'idle' };
	}
	/** The Overview's Approve / Request changes arm the verdict here, then the dock shows this pane. */
	export function armVerdict(verdict: Verdict) {
		if (current && allowed(current.decision).includes(verdict)) arm(verdict);
	}
	export const awaitingVersion = () => (summary.awaiting ? (current?.number ?? null) : null);

	async function confirm() {
		if (decide.phase !== 'armed' || !current || !reasonReady) return;
		const verdict = decide.verdict;
		decide = { phase: 'sending', verdict };
		try {
			await daemon.review(planId, current.id, verdict, reason.trim());
			decide = {
				phase: 'done',
				verdict,
				message:
					verdict === 'approve'
						? 'Approved.'
						: verdict === 'reject'
							? 'Rejected. The version stays here with your reason.'
							: 'Changes requested. The version stays here with your reason.'
			};
			reason = '';
			ondecided();
		} catch (cause) {
			decide = {
				phase: 'failed',
				verdict,
				message:
					cause instanceof DaemonError
						? cause.message
						: 'Something in this build failed while sending the verdict.'
			};
		}
	}

	/* --- values -------------------------------------------------------------- */
	function when(value: string | null): string {
		if (!value) return '—';
		const at = new Date(value);
		return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
	}
	const short = (sha: string | null) => (sha ? sha.slice(0, 10) : null);
	const count = (n: number) => n.toLocaleString();
	const listing = <T,>(all: T[]): T[] => (expanded ? all : all.slice(0, CAP));
</script>

{#snippet cohortList(versionId: string, view: WalkthroughView, marked: boolean)}
	{#each view.cohorts as cohort, at (cohort.name)}
		{@const bodyId = `${versionId}-cohort-${at}`}
		{@const open = cohortOpen(versionId, cohort.name, view.totalFiles)}
		<div class="cohort">
			<button type="button" class="cohort-head" aria-expanded={open} aria-controls={bodyId}
				onclick={() => toggleCohort(versionId, cohort.name, view.totalFiles)}>
				<Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
				<span class="cn">{cohort.name}</span>
				<span class="count">{count(cohort.paths.length)} {cohort.paths.length === 1 ? 'file' : 'files'}</span>
			</button>
			{#if cohort.summary}<p class="dk-note">{cohort.summary}</p>{/if}
			{#if open}
				<ul class="dk-list" id={bodyId}>
					{#each listing(cohort.paths) as row (row.path)}
						<li>
							<code>{row.path}</code>
							{#if marked && row.mark === 'added'}<span class="chip">added</span>
							{:else if marked && row.mark === 'carried'}<span class="chip">also in v{view.base}</span>{/if}
							{#if marked && row.required}<span class="chip">required</span>{/if}
						</li>
					{/each}
				</ul>
				{#if !expanded && cohort.paths.length > CAP}
					<p class="dk-note">{count(cohort.paths.length - CAP)} more — maximize the dock to read them.</p>
				{/if}
			{/if}
		</div>
	{/each}
{/snippet}

<section class="rv">
	{#if report?.stale}
		<p class="dk-note full">The checkpoint report has not answered since {when(report.loadedAt?.toISOString() ?? null)}; this is the last thing it said.</p>
	{/if}

	{#if !initiative && !view}
		<p class="dk-note full">Neither the folded plan nor the checkpoint report has answered for <strong>{id}</strong> — unread, not an initiative that produced no evidence.</p>
	{:else if versions.length === 0}
		<p class="dk-note full">
			{#if nodeState === 'running'}No checkpoint yet — the attempt is running; evidence appears when it is written.
			{:else if nodeState === 'failed'}No checkpoint was recorded; this member failed before it wrote evidence.
			{:else if nodeState === 'pending'}Nothing has run here, so there is no evidence to review.
			{:else}No checkpoint version is recorded against this initiative.{/if}
		</p>
	{:else if current}
		{@const rows = checksOf(current.manifest, contract)}
		{@const artifacts = artifactsOf(current.manifest, contract)}
		{@const changes = changesOf(current, base)}
		{@const verdicts = allowed(current.decision)}
		{@const requiredRows = rows.filter((r) => r.required)}
		{@const passedRequired = requiredRows.filter((r) => r.result?.passed).length}
		{@const tone = current.unread ? 'idle' : summary.awaiting ? 'needs' : current.decision === 'approved' ? 'settled' : current.decision === 'pending' ? 'idle' : 'failed'}

		<div class="col">
			<div class="rule-h"><span class="lbl">Checkpoint</span></div>
			<div class="stmt" data-tone={tone}>
				<b>{current.number === null ? 'Latest' : `Checkpoint v${current.number}`} · {current.unread ? 'unread' : DECISION_WORD[current.decision].toLowerCase()}</b>
				<p>
					{#if current.unread}The review report did not answer; the evidence is real and the verdict on it is unknown.
					{:else if current.decided_by === 'policy'}Settled automatically on clean evidence — no reviewer was asked.
					{:else if current.decided_at}{current.decided_by || 'operator'} · {when(current.decided_at)}{current.reason ? ` — ${current.reason}` : ''}
					{:else if policy === 'required'}A reviewer decides; nothing settles until one does.
					{:else}No review required — clean evidence settles this on its own.{/if}
					Exit {current.manifest?.exit_code ?? '—'} ·
					{requiredRows.length === 0 ? 'no required checks' : `${passedRequired} of ${requiredRows.length} required checks pass`} ·
					{artifacts.length === 0 ? 'no changed paths' : `${count(artifacts.length)} changed ${artifacts.length === 1 ? 'path' : 'paths'}`}.
				</p>
			</div>

			{#if historical}
				<p class="dk-note">Historical replay is read-only; a verdict cannot be recorded against a past state.</p>
			{:else if decide.phase === 'armed' || decide.phase === 'sending'}
				{@const lines = impactOf(decide.verdict, {
					initiativeId: id, version: current.number, state: nodeState, current: true, consumers,
					approvedVersion: view?.approved_version ?? null
				})}
				<div class="armed">
					{#each lines as line, at (at)}<p class="dk-note">{line}</p>{/each}
					{#if decide.verdict === 'approve' && violations.length > 0}
						<p class="dk-note"><span class="state" data-tone="failed">Will be refused</span> {count(violations.length)} contract {violations.length === 1 ? 'violation' : 'violations'} stand; the decision stays pending.</p>
					{/if}
					<div class="field-l">
						<label class="lbl" for="review-reason">Reason{REQUIRES_REASON.includes(decide.verdict) ? ' · required' : ' · optional'}</label>
						<textarea class="ta" id="review-reason" rows="2" bind:value={reason} spellcheck="false" disabled={decide.phase === 'sending'}></textarea>
					</div>
					<div class="btnrow">
						<Button icon={decide.verdict === 'approve' ? 'check-check' : decide.verdict === 'reject' ? 'x' : 'message-square-diff'} kind={decide.verdict === 'reject' ? 'danger' : 'primary'}
							busy={decide.phase === 'sending'} disabled={decide.phase === 'sending' || !reasonReady} onclick={() => void confirm()}>
							{VERDICT_WORD[decide.verdict]} v{current.number}
						</Button>
						<Button icon="x" onclick={disarm} disabled={decide.phase === 'sending'}>Cancel</Button>
					</div>
				</div>
			{:else}
				<div class="btnrow">
					{#if verdicts.length === 0}
						<p class="dk-note">{refused(current.decision, 'approve')}</p>
					{:else}
						{#each verdicts as verdict, at (verdict)}
							<Button icon={verdict === 'approve' ? 'check-check' : verdict === 'reject' ? 'x' : 'message-square-diff'}
								kind={at === 0 ? 'primary' : verdict === 'reject' ? 'danger' : 'secondary'} onclick={() => arm(verdict)}>
								{VERDICT_WORD[verdict]}{verdict === 'approve' ? ` v${current.number}` : ''}
							</Button>
						{/each}
					{/if}
					<Button icon="git-compare" onclick={() => oncompare?.()}>Compare</Button>
				</div>
				{#if decide.phase === 'done'}
					<p class="state" data-tone={decide.verdict === 'approve' ? 'pass' : 'failed'} role="status">{decide.message}</p>
				{:else if decide.phase === 'failed'}
					<p role="alert"><span class="state" data-tone="failed">Not recorded</span> <span class="dk-note">{decide.message}</span></p>
				{/if}
			{/if}

			{#if violations.length > 0}
				<div class="rule-h sp"><span class="lbl">Contract</span><span class="r"><span class="count bad">{violations.length}</span></span></div>
				<ul class="dk-list">
					{#each violations as violation (violation)}
						{@const at = violation.indexOf(': ')}
						<li><span class="state" data-tone="failed">{at === -1 ? 'violation' : violation.slice(0, at)}</span> {at === -1 ? violation : violation.slice(at + 2)}</li>
					{/each}
				</ul>
			{/if}

			{#if (current.manifest?.caveats.length ?? 0) > 0}
				<div class="rule-h sp"><span class="lbl">Caveats</span><span class="r"><span class="count">{count(current.manifest?.caveats.length ?? 0)}</span></span></div>
				<ul class="dk-list prose-list">
					{#each current.manifest?.caveats ?? [] as caveat (caveat)}<li>{caveat}</li>{/each}
				</ul>
			{/if}
		</div>

		<div class="col">
			<div class="rule-h"><span class="lbl">Checks</span><span class="r"><span class="count">{rows.length === 0 ? '—' : count(rows.length)}</span></span></div>
			{#if rows.length === 0}
				<p class="dk-note">{contract && contract.required_checks.length > 0 ? 'No check was recorded and the contract names some — each is a missing-check violation.' : 'No check ran and none is required; settlement rests on exit status and write scope.'}</p>
			{:else}
				<ul class="chain">
					{#each listing(rows) as row (row.name)}
						<li>
							<span class="dot" data-tone={row.result === null ? 'waiting' : row.result.passed ? 'pass' : 'failed'}></span>
							<span class="ell"><code>{row.name}</code>{#if row.result && !row.result.passed && row.result.summary}<span class="why">{row.result.summary}</span>{/if}</span>
							<span class="state" data-tone={row.result === null ? 'waiting' : row.result.passed ? 'pass' : 'failed'}>{row.result === null ? 'Did not run' : row.result.passed ? 'Passed' : 'Failed'}{row.required ? ' · req' : ''}</span>
						</li>
					{/each}
				</ul>
				{#if !expanded && rows.length > CAP}<p class="dk-note">{count(rows.length - CAP)} more — maximize the dock to read them.</p>{/if}
			{/if}

			<div class="rule-h sp"><span class="lbl">Downstream</span><span class="r"><span class="count">{consumers.length === 0 ? '—' : count(consumers.length)}</span></span></div>
			{#if consumers.length === 0}
				<p class="dk-note">Nothing depends on <strong>{id}</strong>; either verdict releases and holds nothing.</p>
			{:else}
				<ul class="chain">
					{#each listing(consumers) as consumer (consumer.id)}
						<li>
							<span class="dot" data-tone={consumer.tainted.length > 0 ? 'failed' : 'waiting'}></span>
							<span class="ell"><strong>{consumer.id}</strong> <span class="muted">{consumer.name}</span>
								{#if consumer.alsoWaitingOn.length > 0}<span class="why">also waits on {consumer.alsoWaitingOn.join(', ')}</span>{/if}
								{#each consumer.tainted as taint (taint.checkpoint_id + taint.reason)}<span class="why">{taint.reason}.</span>{/each}
							</span>
							<span class="state" data-tone={consumer.tainted.length > 0 ? 'failed' : 'waiting'}>{consumer.tainted.length > 0 ? 'Tainted' : consumer.direct ? 'Direct' : 'Through'}</span>
						</li>
					{/each}
				</ul>
				{#if !expanded && consumers.length > CAP}<p class="dk-note">{count(consumers.length - CAP)} more — maximize the dock to read the chain.</p>{/if}
			{/if}
		</div>

		<div class="col">
			<div class="rule-h"><span class="lbl">Evidence</span></div>
			<dl class="kv">
				<dt>Patch</dt>
				<dd class="ell">{#if current.manifest?.patch_path}<code>{current.manifest.patch_path}</code>{:else if contract?.require_patch}<span class="state" data-tone="failed">Required, none recorded</span>{:else}<span class="muted">—</span>{/if}</dd>
				<dt>Commit</dt>
				<dd class="ell">
					{#if short(current.manifest?.base_sha ?? null) && short(current.manifest?.head_sha ?? null)}<code>{short(current.manifest?.base_sha ?? null)}</code><Icon name="chevron-right" size={12} /><code>{short(current.manifest?.head_sha ?? null)}</code>
					{:else if short(current.manifest?.head_sha ?? null)}<code>{short(current.manifest?.head_sha ?? null)}</code>
					{:else}<span class="muted">—</span>{/if}
				</dd>
				<dt>Against</dt>
				<dd>
					{#if !base}<span class="muted">no approved version</span>
					{:else if !changes}<span class="muted">{current.id === base.id ? 'this is the approved version' : 'a manifest is unread'}</span>
					{:else if changes.identical}<span class="muted">v{base.number}: same {count(changes.carried.length)} paths</span>
					{:else}<span class="mono">v{base.number} · +{count(changes.added.length)} added · {count(changes.carried.length)} carried · −{count(changes.dropped.length)} dropped</span>{/if}
				</dd>
			</dl>

			{#if walk}
				<div class="rule-h sp"><span class="lbl">Walkthrough</span><span class="r"><span class="count">{walk.version === null ? 'latest' : `v${walk.version}`} · {count(walk.totalFiles)}</span></span></div>
				{#if walk.basis === 'ungrouped'}
					<p class="dk-note">The daemon returned no grouping for this version; paths are listed as they came.</p>
					<ul class="dk-list">{#each listing(walk.ungrouped) as path (path)}<li><code>{path}</code></li>{/each}</ul>
				{:else if walk.cohorts.length === 0 && walk.totalFiles === 0}
					<p class="dk-note">No changed path — nothing was written.</p>
				{:else}
					{@render cohortList(current.id, walk, true)}
				{/if}
				{#if walk.dropped.length > 0}
					<div class="rule-h sp"><span class="lbl">No longer touched · v{walk.base}</span></div>
					{#each listing(walk.dropped) as group (group.name)}
						<p class="cohort-head static"><span class="cn">{group.name}</span><span class="count">{count(group.paths.length)} of {count(group.baseTotal)}</span></p>
						<ul class="dk-list">{#each listing(group.paths) as path (path)}<li><code>{path}</code></li>{/each}</ul>
					{/each}
				{/if}
				{#if walk.missing.length > 0}
					<div class="rule-h sp"><span class="lbl">Required, missing</span></div>
					<ul class="dk-list">{#each listing(walk.missing) as path (path)}<li><code>{path}</code> <span class="state" data-tone="failed">Missing</span></li>{/each}</ul>
				{/if}
			{/if}

			{#if prior.length > 0}
				<div class="rule-h sp"><span class="lbl">Earlier versions</span><span class="r"><span class="count">{count(prior.length)}</span></span></div>
				<ul class="chain">
					{#each listing([...prior].reverse()) as version (version.id)}
						<li>
							<span class="dot" data-tone={version.unread ? 'idle' : version.decision === 'approved' ? 'settled' : version.decision === 'pending' ? 'idle' : 'failed'}></span>
							<span class="ell">v{version.number} <span class="muted">{version.decided_by === 'policy' ? 'automatic policy' : version.reason ? `${version.decided_by || 'operator'}: ${version.reason}` : version.decided_at ? 'decided without a reason' : when(null)}</span></span>
							<span class="state" data-tone={version.unread ? 'idle' : version.decision === 'approved' ? 'settled' : version.decision === 'pending' ? 'idle' : 'failed'}>{version.unread ? 'Unread' : DECISION_WORD[version.decision]}</span>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</section>

<style>
	.rv { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1fr); gap: 32px; align-items: start; }
	.full { grid-column: 1 / -1; }
	.col { min-width: 0; }
	.sp { margin-top: 20px; }
	.armed { display: grid; gap: 10px; margin-top: 14px; }
	.btnrow { margin-top: 14px; }
	.why { display: block; font: 400 12px/1.4 var(--f-ui); color: var(--dim); }
	.ell { min-width: 0; overflow-wrap: anywhere; }
	.prose-list { color: var(--tx2); }
	.cohort-head {
		display: flex; align-items: center; gap: 8px; width: 100%; padding: 4px 0; text-align: left; color: var(--tx2);
	}
	.cohort-head:hover { color: var(--tx); }
	.cohort-head.static { cursor: default; padding-left: 22px; }
	.cohort-head .cn { flex: 1; font: 500 12px var(--f-label); letter-spacing: 0.12em; text-transform: uppercase; min-width: 0; overflow-wrap: anywhere; }
	.cohort { margin-bottom: 4px; }
	.cohort .dk-list { padding-left: 22px; }
	@media (max-width: 1100px) { .rv { grid-template-columns: minmax(0, 1fr); gap: 24px; } }
</style>
