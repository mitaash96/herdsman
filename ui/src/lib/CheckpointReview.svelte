<script lang="ts">
	/*
	  Unit R4 — checkpoint review, inside R2's drawer. Its direction contract
	  lives in `.impeccable/surfaces/ui-src-lib-checkpointreview-svelte.md`.

	  The whole surface is one reading order at two widths. Expanding does not
	  reveal new sections and does not move anything: it un-truncates the lists
	  that were capped, so an operator who expands mid-read is looking at the
	  same document, longer. That is what makes reading position cheap to keep.

	  Deliberately absent, each named on screen where an operator would look:
	  grouped code-diff cohorts (R5), packet contents (R7), verification
	  PASS/WARN/BLOCK visualization (R15), retry and revision (R6).

	  What this build cannot show, and says so rather than implying otherwise:
	  file content. `patch_path` is a reference to bytes on disk that the daemon
	  serves no route for, and `changes_since_approved` is a set subtraction
	  over path names. Every comparison here is a change list.
	*/
	import {
		daemon,
		DaemonError,
		type CheckpointReport,
		type Contract,
		type Initiative,
		type PlanGraph,
		type Verdict
	} from './daemon';
	import type { Resource } from './resource.svelte';
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
		type Version
	} from './review';

	let {
		planId,
		id,
		initiative,
		graph,
		report,
		expanded,
		onexpand,
		ondecided
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
	} = $props();

	/** Collapsed, a list this long stops and says how much it is holding back. */
	const CAP = 5;

	const view = $derived(reviewOf(report?.data ?? null, id));
	const versions = $derived<Version[]>(versionsOf(initiative, view));
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
	const consumers = $derived(
		consumersOf(graph, id, report?.data?.attention ?? [])
	);
	/* Only the current version's contract is validated by the report; a prior
	   version's violations are not projected and are not guessed at here. */
	const violations = $derived<string[]>(view?.violations ?? []);

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

<section class:reading={expanded}>
	<p class="label rule-label">
		<span>Checkpoint</span><span class="rule"></span>
		<span class="member" data-state={summary.state}>{summary.word}</span>
	</p>

	{#if !initiative && !view}
		<p class="prose quiet">
			Neither the folded plan nor the checkpoint report has answered for
			<strong>{id}</strong>, so nothing is known about its evidence — which is unread,
			not an initiative that has produced none.
		</p>
	{:else if versions.length === 0}
		<p class="prose quiet">
			{#if nodeState === 'running'}
				No checkpoint has been recorded yet. The attempt is still running, and evidence
				appears here the moment it is written — one manifest per recorded version.
			{:else if nodeState === 'failed'}
				No checkpoint was recorded. This member failed before it wrote evidence, so
				there is nothing to review; what to do about it is retry or restart, which is
				not built yet.
			{:else if nodeState === 'pending'}
				No checkpoint has been recorded. Nothing has run here, so there is no evidence
				to review yet.
			{:else}
				No checkpoint version is recorded against this initiative.
			{/if}
		</p>
	{:else if current}
		{@const rows = checksOf(current.manifest, contract)}
		{@const artifacts = artifactsOf(current.manifest, contract)}
		{@const changes = changesOf(current, base)}
		{@const verdicts = allowed(current.decision)}
		{@const requiredRows = rows.filter((r) => r.required)}
		{@const passedRequired = requiredRows.filter((r) => r.result?.passed).length}
		{@const more =
			artifacts.length + rows.length + prior.length + consumers.length + (changes?.carried.length ?? 0)}

		{#if current.unread}
			<p class="prose quiet">
				The review report did not answer, so the evidence below is real and the verdict
				on it is unknown. Nothing here says a version is awaiting review when it may
				already have been decided.
			</p>
		{/if}

		<!-- The current version's manifest. Immutable: every version stays in the
		     projection, and the one being reviewed is simply the last. -->
		<div class="version plate">
			<p class="label rule-label">
				<span>Version</span><span class="rule"></span>
				<span class="member" data-state={current.unread ? 'slack' : DECISION_STATE[current.decision]}>
					{current.number === null ? 'Latest' : `v${current.number} of ${versions.length}`}
				</span>
			</p>

			<dl class="readout plate">
				<div>
					<dt class="label">Decision</dt>
					<dd class="value member" data-state={current.unread ? 'slack' : DECISION_STATE[current.decision]}>
						{current.unread ? 'Unread' : DECISION_WORD[current.decision]}
					</dd>
					<p class="gloss">
						{#if current.unread}
							the review report is unread; this is not a pending decision
						{:else if current.decided_by === 'policy'}
							settled automatically on clean evidence — no reviewer was asked
						{:else if current.decided_at}
							{current.decided_by || 'operator'} · {when(current.decided_at)}
						{:else if policy === 'required'}
							a reviewer decides; nothing settles until one does
						{:else}
							no review required — clean evidence settles this initiative on its own
						{/if}
					</p>
				</div>
				<div>
					<dt class="label">Exit</dt>
					<dd
						class="value member"
						data-state={current.manifest?.exit_code == null
							? 'slack'
							: current.manifest.exit_code === 0
								? 'seated'
								: 'failed'}
					>
						{current.manifest?.exit_code ?? '—'}
					</dd>
					<p class="gloss">
						{current.manifest ? 'the executor’s own exit status' : 'the manifest is unread'}
					</p>
				</div>
				<div>
					<dt class="label">Required checks</dt>
					<dd
						class="value member"
						data-state={requiredRows.length === 0
							? 'slack'
							: passedRequired === requiredRows.length
								? 'seated'
								: 'failed'}
					>
						{requiredRows.length === 0 ? '—' : `${passedRequired} of ${requiredRows.length}`}
					</dd>
					<p class="gloss">
						{requiredRows.length === 0
							? 'this contract requires no named check'
							: 'passed, of the checks the contract names'}
					</p>
				</div>
				<div>
					<dt class="label">Artifacts</dt>
					<dd class="value member" data-state={artifacts.length === 0 ? 'slack' : 'seated'}>
						{artifacts.length === 0 ? '—' : count(artifacts.length)}
					</dd>
					<p class="gloss">
						{artifacts.length === 0
							? 'no changed path is recorded on this version'
							: 'paths this version touched'}
					</p>
				</div>
				<div class="wide">
					<dt class="label">Patch</dt>
					<dd class="paths">
						{#if current.manifest?.patch_path}
							<code>{current.manifest.patch_path}</code>
							<span class="quiet">
								the physical handoff artifact, stored under <code>.herdsman/artifacts</code>.
								The daemon serves no route that returns its bytes, so this build can name
								the patch and cannot show it.
							</span>
						{:else if contract?.require_patch}
							<span class="member" data-state="failed">
								No patch was recorded, and this contract requires one.
							</span>
						{:else}
							<span class="member" data-state="slack">
								No patch was recorded. This contract does not require one.
							</span>
						{/if}
					</dd>
				</div>
				<div class="wide">
					<dt class="label">Commit</dt>
					<dd class="paths">
						{#if short(current.manifest?.base_sha ?? null) && short(current.manifest?.head_sha ?? null)}
							<code>{short(current.manifest?.base_sha ?? null)}</code>
							<span class="quiet">to</span>
							<code>{short(current.manifest?.head_sha ?? null)}</code>
						{:else if short(current.manifest?.head_sha ?? null)}
							<code>{short(current.manifest?.head_sha ?? null)}</code>
							<span class="quiet">— no base commit was recorded.</span>
						{:else}
							<span class="member" data-state="slack">
								This version recorded no commit, so its evidence cannot be located in
								history by sha.
							</span>
						{/if}
					</dd>
				</div>
			</dl>

			{#if violations.length > 0}
				<div class="block">
					<p class="lead member" data-state="failed">
						This version does not satisfy its contract.
					</p>
					<p class="prose">
						The daemon validates a contract before it records an approval, so approving
						is refused while any of these stands — the decision simply stays pending and
						no event is appended.
					</p>
					<ul class="lines">
						{#each violations as violation (violation)}
							{@const at = violation.indexOf(': ')}
							<li>
								<span class="vcode">{at === -1 ? 'violation' : violation.slice(0, at)}</span>
								{at === -1 ? violation : violation.slice(at + 2)}
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			{#if (current.manifest?.caveats.length ?? 0) > 0}
				<div class="block">
					<p class="label rule-label">
						<span>Caveats</span><span class="rule"></span>
						<span class="member" data-state="balanced">
							{count(current.manifest?.caveats.length ?? 0)}
						</span>
					</p>
					<p class="prose quiet">
						The one part of a manifest an executor writes, restricted to
						non-recoverable decisions and blockers — never a summary of the work.
					</p>
					<ul class="lines">
						{#each current.manifest?.caveats ?? [] as caveat (caveat)}
							<li>{caveat}</li>
						{/each}
					</ul>
				</div>
			{/if}

			<!-- Checks. Required first, in the contract's order; a required check
			     that never ran is neither a pass nor a failure and is drawn as
			     neither. -->
			<div class="block">
				<p class="label rule-label">
					<span>Checks</span><span class="rule"></span>
					<span class="member" data-state={rows.length === 0 ? 'slack' : 'balanced'}>
						{rows.length === 0 ? 'None ran' : count(rows.length)}
					</span>
				</p>
				{#if rows.length === 0}
					<p class="prose quiet">
						{contract && contract.required_checks.length > 0
							? 'This version recorded no check at all, and the contract names some. Every one of them is a missing-check violation.'
							: 'No check ran and none is required, so settlement rests on the exit status and the write scope alone.'}
					</p>
				{:else}
					<ul class="checks">
						{#each listing(rows) as row (row.name)}
							<li
								class="check member"
								data-state={row.result === null ? 'slack' : row.result.passed ? 'seated' : 'failed'}
							>
								<span class="ring" aria-hidden="true"></span>
								<span class="check-name"><code>{row.name}</code></span>
								<span class="check-word">
									{row.result === null ? 'Did not run' : row.result.passed ? 'Passed' : 'Failed'}{row.required
										? ' · required'
										: ''}
								</span>
								{#if row.result && !row.result.passed && row.result.summary}
									<p class="prose quiet check-why">{row.result.summary}</p>
								{:else if row.result === null}
									<p class="prose quiet check-why">
										The contract names it and this version has no result for it — which
										reads nothing like a failure and is not one.
									</p>
								{/if}
							</li>
						{/each}
					</ul>
					{#if !expanded && rows.length > CAP}
						<p class="prose quiet foot">
							{count(rows.length - CAP)} more {rows.length - CAP === 1 ? 'check' : 'checks'} —
							expand to read them with their summaries.
						</p>
					{/if}
				{/if}
			</div>

			<!-- Artifacts: what this version touched, required paths marked. -->
			<div class="block">
				<p class="label rule-label">
					<span>Artifacts</span><span class="rule"></span>
					<span class="member" data-state={artifacts.length === 0 ? 'slack' : 'balanced'}>
						{artifacts.length === 0 ? 'None recorded' : count(artifacts.length)}
					</span>
				</p>
				{#if artifacts.length === 0}
					<p class="prose quiet">
						This version records no changed path. That is evidence of nothing having been
						written, not evidence missing.
					</p>
				{:else}
					<ul class="lines paths-list">
						{#each listing(artifacts) as row (row.path)}
							<li class="member" data-state={row.required && !row.present ? 'failed' : 'seated'}>
								<code>{row.path}</code>
								{#if row.required}
									<span class="tag">{row.present ? 'required' : 'required · missing'}</span>
								{/if}
							</li>
						{/each}
					</ul>
					{#if !expanded && artifacts.length > CAP}
						<p class="prose quiet foot">
							{count(artifacts.length - CAP)} more — expand to read the whole list.
						</p>
					{/if}
				{/if}
			</div>

			<!-- Comparison with the approved version. A change list, and labelled
			     as one: nothing here has read a byte of any file. -->
			<div class="block">
				<p class="label rule-label">
					<span>Against approved</span><span class="rule"></span>
					<span class="member" data-state={base ? 'balanced' : 'slack'}>
						{base ? `v${base.number}` : 'No approved version'}
					</span>
				</p>
				{#if !base}
					<p class="prose quiet">
						No version of this checkpoint has ever been approved, so there is no base to
						read changes against. The manifest above is the whole of what is known.
					</p>
				{:else if !changes}
					<p class="prose quiet">
						{current.id === base.id
							? 'This is the approved version, so there is nothing to compare it with.'
							: 'One of the two manifests is unread, so no comparison can be made without guessing at it.'}
					</p>
				{:else}
					<p class="prose quiet">
						A comparison of <em>which paths</em> each version touched — not of what is in
						them. The daemon projects the added half of this as
						<code>changes_since_approved</code> and serves no route returning file
						content, so two versions can appear identical here and differ entirely.
						Reading the actual changes is not built, and reading them grouped into
						cohorts is a later unit.
					</p>
					{#if changes.identical}
						<p class="prose quiet foot member" data-state="slack">
							Both versions touch exactly the same {count(changes.carried.length)}
							{changes.carried.length === 1 ? 'path' : 'paths'}. That is not the same as
							being unchanged.
						</p>
					{:else}
						<dl class="readout plate">
							<div>
								<dt class="label">Added</dt>
								<dd class="value member" data-state={changes.added.length > 0 ? 'balanced' : 'slack'}>
									{changes.added.length > 0 ? count(changes.added.length) : '—'}
								</dd>
								<p class="gloss">not touched by v{changes.base}</p>
							</div>
							<div>
								<dt class="label">Also in v{changes.base}</dt>
								<dd class="value member" data-state={changes.carried.length > 0 ? 'seated' : 'slack'}>
									{changes.carried.length > 0 ? count(changes.carried.length) : '—'}
								</dd>
								<p class="gloss">same path, content unknown</p>
							</div>
							<div>
								<dt class="label">No longer touched</dt>
								<dd class="value member" data-state={changes.dropped.length > 0 ? 'balanced' : 'slack'}>
									{changes.dropped.length > 0 ? count(changes.dropped.length) : '—'}
								</dd>
								<p class="gloss">
									{changes.dropped.length > 0
										? 'in v' + changes.base + ', absent here'
										: 'nothing was dropped'}
								</p>
							</div>
						</dl>
						{#each [{ word: 'Added', paths: changes.added }, { word: 'No longer touched', paths: changes.dropped }] as group (group.word)}
							{#if group.paths.length > 0}
								<p class="label sub">{group.word}</p>
								<ul class="lines paths-list">
									{#each listing(group.paths) as path (path)}
										<li><code>{path}</code></li>
									{/each}
								</ul>
								{#if !expanded && group.paths.length > CAP}
									<p class="prose quiet foot">
										{count(group.paths.length - CAP)} more — expand to read them.
									</p>
								{/if}
							{/if}
						{/each}
					{/if}
				{/if}
			</div>
		</div>

		<!-- Prior versions. Nothing is ever removed, so refused evidence stays
		     addressable with the reason it was refused. -->
		{#if prior.length > 0}
			<div class="block">
				<p class="label rule-label">
					<span>Earlier versions</span><span class="rule"></span>
					<span class="member" data-state="balanced">{count(prior.length)}</span>
				</p>
				<p class="prose quiet">
					Superseded, never deleted. A rejection is answered by recording a revised
					version, and the refused one stays here with the reason it was refused.
				</p>
				{#each listing([...prior].reverse()) as version (version.id)}
					<div class="past">
						<p class="past-head">
							<span class="member" data-state={version.unread ? 'slack' : DECISION_STATE[version.decision]}>
								v{version.number}
								· {version.unread ? 'Unread' : DECISION_WORD[version.decision]}
							</span>
							<span class="quiet">{when(version.decided_at)}</span>
						</p>
						{#if version.decided_by === 'policy'}
							<p class="prose quiet">
								Approved by the automatic policy: settlement itself was the approval,
								so nobody read this version.
							</p>
						{:else if version.reason}
							<p class="prose quiet">
								{version.decided_by || 'operator'}: {version.reason}
							</p>
						{:else if !version.unread && version.decided_at}
							<p class="prose quiet member" data-state="slack">
								Decided without a reason. Nothing was written down about why.
							</p>
						{/if}
						{#if expanded && version.manifest}
							<ul class="lines paths-list">
								{#each version.manifest.changed_paths as path (path)}
									<li><code>{path}</code></li>
								{/each}
								{#if version.manifest.changed_paths.length === 0}
									<li class="member" data-state="slack">No changed path recorded.</li>
								{/if}
							</ul>
						{/if}
					</div>
				{/each}
				{#if !expanded && prior.length > CAP}
					<p class="prose quiet foot">
						{count(prior.length - CAP)} older {prior.length - CAP === 1 ? 'version' : 'versions'}
						— expand to read them with their paths.
					</p>
				{/if}
			</div>
		{/if}

		<!-- Who this decision holds. Computed from the graph on screen, named
		     member by member, never "downstream work may be affected". -->
		<div class="block">
			<p class="label rule-label">
				<span>Downstream</span><span class="rule"></span>
				<span class="member" data-state={consumers.length === 0 ? 'slack' : 'balanced'}>
					{consumers.length === 0 ? 'Nothing depends on it' : count(consumers.length)}
				</span>
			</p>
			{#if consumers.length === 0}
				<p class="prose quiet">
					No member of this plan depends on <strong>{id}</strong>, so whichever way this
					decision goes it releases and holds nothing.
				</p>
			{:else}
				<ul class="lines">
					{#each listing(consumers) as consumer (consumer.id)}
						<li class="consumer member" data-state={consumer.tainted.length > 0 ? 'failed' : 'balanced'}>
							<p class="consumer-head">
								<strong>{consumer.id}</strong>
								<span class="tag">{consumer.direct ? 'direct' : 'through a dependency'}</span>
								{#if consumer.tainted.length > 0}
									<span class="tag taint">tainted</span>
								{/if}
							</p>
							<p class="prose quiet">{consumer.name}</p>
							{#if consumer.alsoWaitingOn.length > 0}
								<p class="prose quiet">
									Also waiting on {consumer.alsoWaitingOn.join(', ')}, so approving this
									alone does not make it ready.
								</p>
							{/if}
							{#each consumer.tainted as taint (taint.checkpoint_id + taint.reason)}
								<p class="prose quiet">{taint.reason}.</p>
							{/each}
						</li>
					{/each}
				</ul>
				{#if !expanded && consumers.length > CAP}
					<p class="prose quiet foot">
						{count(consumers.length - CAP)} more — expand to read the whole chain.
					</p>
				{/if}
			{/if}
		</div>

		<!-- The three writes. Armed, read, then confirmed: none of them can be
		     undone by pressing the same control again. -->
		<div class="block decide">
			<p class="label rule-label">
				<span>Decide</span><span class="rule"></span>
				<span class="member" data-state={summary.awaiting ? 'balanced' : DECISION_STATE[current.decision]}>
					{current.number === null ? 'Latest' : `v${current.number}`}
				</span>
			</p>

			{#if policy !== 'required' && current.decision === 'pending'}
				<p class="prose quiet">
					This initiative settles automatically on clean evidence, so nobody is being
					asked for a verdict. Recording one anyway is allowed and is kept in the
					projection — a rejection here still refuses the evidence and holds everything
					downstream.
				</p>
			{/if}

			{#if decide.phase === 'failed'}
				<div role="alert">
					<p class="lead member" data-state="failed">
						Not {decide.verdict === 'approve' ? 'approved' : decide.verdict === 'reject' ? 'rejected' : 'recorded'}.
					</p>
					<p class="prose">{decide.message}</p>
				</div>
				<p class="actions">
					<button class="act" type="button" onclick={disarm}>Back</button>
				</p>
			{:else if decide.phase === 'done'}
				<p class="prose member outcome" data-state={decide.verdict === 'approve' ? 'seated' : 'failed'} role="status">
					{decide.message}
				</p>
			{:else if decide.phase === 'armed' || decide.phase === 'sending'}
				{@const lines = impactOf(decide.verdict, {
					initiativeId: id,
					version: current.number,
					state: nodeState,
					current: true,
					consumers,
					approvedVersion: view?.approved_version ?? null
				})}
				<div class="impact">
					{#each lines as line, at (at)}
						<p class="prose">{line}</p>
					{/each}
					{#if decide.verdict === 'approve' && violations.length > 0}
						<p class="prose member" data-state="failed">
							The daemon validates the contract before appending anything, and this
							version has {count(violations.length)}
							{violations.length === 1 ? 'violation' : 'violations'} — so this approval
							will be refused and the decision will stay pending.
						</p>
					{/if}
				</div>
				<p class="reasonrow">
					<label class="label" for="review-reason">
						Reason{REQUIRES_REASON.includes(decide.verdict) ? '' : ' (optional)'}
					</label>
					<textarea
						class="plate"
						id="review-reason"
						rows="2"
						bind:value={reason}
						spellcheck="false"
						disabled={decide.phase === 'sending'}
						aria-describedby="review-reason-note"
					></textarea>
					<span id="review-reason-note" class="req">
						{#if REQUIRES_REASON.includes(decide.verdict)}
							Required. It is recorded permanently against this version and is the only
							thing the implementer has to work from.
						{:else}
							Recorded permanently against this version if you write one.
						{/if}
					</span>
				</p>
				<p class="actions">
					<button
						class="act"
						type="button"
						bind:this={confirmEl}
						onclick={() => void confirm()}
						disabled={decide.phase === 'sending' || !reasonReady}
						aria-busy={decide.phase === 'sending' || undefined}
					>
						{decide.phase === 'sending'
							? 'Sending…'
							: `Confirm — ${VERDICT_WORD[decide.verdict].toLowerCase()} v${current.number}`}
					</button>
					<button class="act" type="button" onclick={disarm} disabled={decide.phase === 'sending'}>
						Cancel
					</button>
				</p>
			{:else if verdicts.length === 0}
				<p class="prose member" data-state="failed" role="status">
					{refused(current.decision, 'approve')}
				</p>
			{:else}
				<p class="actions">
					{#each verdicts as verdict (verdict)}
						<button class="act" type="button" onclick={() => arm(verdict)}>
							{VERDICT_WORD[verdict]}
						</button>
					{/each}
				</p>
				{#each EVERY_VERDICT.filter((v) => !verdicts.includes(v)) as missing (missing)}
					<p class="prose quiet member" data-state="slack">
						{VERDICT_WORD[missing]}: {refused(current.decision, missing)}
					</p>
				{/each}
			{/if}

			<p class="prose quiet foot">
				A verdict is recorded as the operator — the daemon is local and unauthenticated
				and this build has no identity to send. Retry, restart and recording a revised
				version are not built here.
			</p>
		</div>

		<!-- The expansion. Last, because by the time you want it you have read
		     down to here, and pressing it moves nothing you were reading. -->
		{#if more > CAP}
			<p class="actions expandrow">
				<button
					class="act"
					type="button"
					onclick={() => onexpand(!expanded)}
					aria-expanded={expanded}
				>
					{expanded ? 'Collapse the reader' : 'Expand to read in full'}
				</button>
				<span class="quiet">
					{expanded
						? 'Back to the summary, and to sheet width where there is room for it.'
						: 'Shows every list whole; where the window has room it also widens this sheet to reading measure, over the field.'}
				</span>
			</p>
		{/if}
	{/if}
</section>

<style>
	/* This surface adds no token, no third tone and no new geometry: it is the
	   drawer's own vocabulary — the ruled label, the readout grid, the member
	   states, the ghost button — applied to a manifest. Svelte scopes styles per
	   component, so the shared shapes are restated here exactly as PlanGate
	   restates them; the values are the system's, not this file's. */

	section {
		margin-top: 1.75rem;
	}

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
	/* Scoped to the plain ruled lists: a `.check` row is a full-width grid, so
	   a dashed bottom border on it reads as a section divider rather than as
	   one reading's slack state. The check's own state word carries it. */
	.prose.member[data-state='slack'],
	.paths .member[data-state='slack'],
	.lines > li.member[data-state='slack'] {
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
	.lead {
		margin: 0 0 0.5rem;
		color: var(--member-ink);
	}
	strong {
		color: var(--ink);
		font-weight: 500;
	}
	em {
		font-style: normal;
		color: var(--ink);
	}
	code {
		background: var(--ground);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
		overflow-wrap: anywhere;
	}

	/* --- the version plate, built as the drawer's attempt plate is ---------- */
	.version {
		--cut: 12px;
		border: 1px solid var(--rule);
		padding: 1rem 1rem 1.1rem;
		background: var(--plate);
	}
	.version .rule-label {
		margin-bottom: 0.75rem;
	}
	.block {
		margin-top: 1.5rem;
	}
	.block .rule-label {
		margin-bottom: 0.7rem;
	}

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
		align-items: baseline;
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
	.sub {
		margin: 0.9rem 0 0.4rem;
		color: var(--ink-2);
	}

	/* --- plain ruled lists -------------------------------------------------- */
	.lines {
		list-style: none;
		margin: 0.6rem 0 0;
		padding: 0;
	}
	.lines > li {
		padding: 0.4rem 0;
		border-bottom: 1px solid var(--rule);
		color: var(--ink-2);
		font-size: 0.8125rem;
		overflow-wrap: anywhere;
	}
	.lines > li:last-child {
		border-bottom: 0;
	}
	.paths-list > li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem;
	}
	.tag {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--member-ink, var(--ink-2));
	}
	/* The daemon writes a violation as `code: message`. The code is the break
	   and takes the red; the sentence explaining it stays graphite, because a
	   list of red paragraphs is exactly what this world refuses. */
	.consumer > * + * {
		margin-top: 0.2rem;
	}
	.consumer-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.3rem 0.5rem;
		margin: 0;
	}
	/* The one state on this list that changes what an operator does next, so it
	   is named in a word as well as coloured -- the tag inherits `--member-ink`
	   and the failed member makes that red. */
	.tag.taint {
		border-bottom: 1px solid var(--red);
	}
	.vcode {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--red);
		margin-right: 0.15rem;
	}

	/* --- checks: the seat-ring vocabulary, as the subtask chain uses it ------
	   A chain, not a list of rows: one 1px carbon run down the ring column that
	   overshoots the last ring, with each ring knocking out of it in plate. */
	.checks {
		position: relative;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.checks::before {
		content: '';
		position: absolute;
		left: 5px;
		top: 0.85rem;
		bottom: -0.55rem;
		width: 1px;
		background: var(--member-line);
	}
	.check {
		position: relative;
		display: grid;
		grid-template-columns: 11px minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.35rem 0.65rem;
		padding: 0.5rem 0;
		color: var(--ink-2);
	}
	.check-name {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.check-word {
		flex: none;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.check[data-state='failed'] .check-word {
		color: var(--red);
	}
	.check[data-state='slack'] .check-word {
		border-bottom: 1px dashed var(--ash);
	}
	/* Text decoration set on the row propagates into every block inside it, so
	   the slack rule would underline the check's own explanation. The state
	   word carries the dash; the row carries only the graphite. */
	.check.member[data-state='slack'] {
		text-decoration: none;
	}
	.check-why {
		grid-column: 2 / -1;
		margin: 0;
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
	.check[data-state='slack'] .ring {
		border-style: dashed;
	}
	.check[data-state='seated'] .ring {
		background: var(--seat);
	}
	/* A failed member's load path is discontinuous: the ring is cut open on
	   both sides rather than given a sixth colour. */
	.check[data-state='failed'] .ring {
		border-left-color: transparent;
		border-right-color: transparent;
	}

	/* --- earlier versions --------------------------------------------------- */
	.past {
		padding: 0.6rem 0;
		border-bottom: 1px solid var(--rule);
	}
	.past:last-of-type {
		border-bottom: 0;
	}
	.past-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.3rem 0.75rem;
		margin: 0 0 0.3rem;
		font-size: 0.8125rem;
	}

	/* --- the decision ------------------------------------------------------- */
	.impact {
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
		margin-bottom: 0.9rem;
	}
	.reasonrow {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin: 0 0 0.9rem;
		max-width: 68ch;
	}
	/* Geometry comes from the shared `.plate` class in `app.css`, chamfer and
	   `corner-shape` fallback included; this only says what a text field is. */
	textarea {
		--cut: 10px;
		font: inherit;
		font-size: 0.8125rem;
		color: var(--ink);
		background: var(--plate);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
		resize: vertical;
		width: 100%;
	}
	textarea:focus {
		border-color: var(--red);
	}
	textarea:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
	}
	.req {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		line-height: 1.5;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 0.9rem 0 0;
	}
	.expandrow {
		margin-top: 1.5rem;
	}
	.expandrow .quiet {
		flex: 1 1 16rem;
		min-width: 0;
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

	/* At reading width the manifest's readouts have room to sit three across.
	   Keyed on the sheet's own state rather than the viewport's, because the
	   viewport is wide long before this sheet is. */
	.reading .readout > div {
		flex-basis: 13rem;
	}
	/* A column of nine short paths down a 74rem sheet is width spent on nothing.
	   At reading width the path lists flow into as many columns as fit, each row
	   keeping its own hairline, so the reader reads wide rather than long. */
	.reading .paths-list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr));
		column-gap: 2rem;
	}
	/* In one column the last row drops its rule because the section ends there.
	   In a grid the DOM-last item is not the visual bottom of every column, so
	   every row keeps its own. */
	.reading .paths-list > li:last-child {
		border-bottom: 1px solid var(--rule);
	}
</style>
