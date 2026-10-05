<!--
  Run — one plan (views.md §2). Presentation rewrite of the R1–R12 Run page: every read,
  the event stream, replay, selection and the edge-state handling are kept; the field,
  schedule, plan, burn, recovery and salvage are tabs, and the initiative detail is the
  bottom dock (`InitiativeDock`). Deep links: ?plan=&tab=&initiative=&dtab=&checkpoint=&at=.
-->
<script lang="ts">
	import { getContext, untrack } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import AsyncField from '$lib/AsyncField.svelte';
	import Button from '$lib/Button.svelte';
	import IconButton from '$lib/IconButton.svelte';
	import Icon from '$lib/Icon.svelte';
	import Mark from '$lib/Mark.svelte';
	import Spectrum from '$lib/Spectrum.svelte';
	import StateMark from '$lib/StateMark.svelte';
	import Tabs, { panelId, tabId, type TabItem } from '$lib/Tabs.svelte';
	import ContentionField, { clock, liveSeconds, runningClock, spentSeconds } from '$lib/ContentionField.svelte';
	import RunPlan from '$lib/RunPlan.svelte';
	import BurnTab from '$lib/BurnTab.svelte';
	import Salvage from '$lib/Salvage.svelte';
	import ReplayBar from '$lib/ReplayBar.svelte';
	import ReplayRegister from '$lib/ReplayRegister.svelte';
	import InitiativeDock from '$lib/InitiativeDock.svelte';
	import Recovery from '$lib/Recovery.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { harnessHue } from '$lib/marks';
	import { memberTone, type Tone } from '$lib/tones';
	import { useTitleActions } from '$lib/shell.svelte';
	import { ago, tokens } from '$lib/bank';
	import {
		daemon,
		type CheckpointReport,
		type Fleet,
		type InitiativeFailedFrame,
		type Kitchen,
		type MemoryStatus,
		type Plan,
		type PlanGraph,
		type RecoveryReport,
		type RecalibrationReport,
		type RiskReport,
		type RuntimeObservedFrame,
		type StatusBundle,
		type TokenLedger
	} from '$lib/daemon';
	import { buildField, contentionIndex, launching, needsInput, phaseOf, runTarget, step, type Field, type Member, type Touch } from '$lib/field';
	import {
		boundOf,
		nearestStop,
		qualifies,
		qualificationSentence,
		runPhase,
		setHistorical as setHistoricalLatch,
		stopsOf,
		stopReading,
		type ReplayStop
	} from '$lib/replay';

	const plan = getContext<{
		readonly resource: Resource<PlanGraph> | null;
		readonly id: string | null;
		reload: () => void;
	}>('plan');

	/* An unaddressed Run picks from the shell's fleet read (polled there; no second poll here). */
	const fleetCtx = getContext<{ readonly resource: Resource<Fleet>; reload: () => void }>('fleet');

	/* Contention is a second read: the graph draws without it, so a risk report
	   that fails leaves the field standing with its cords explicitly unread.
	   R2 adds a third — the folded plan, which carries the planner-authored
	   content the graph deliberately omits. R4 adds a fourth — the checkpoint
	   report, which carries the review lifecycle the fold projects nowhere
	   else. Each stands alone: a failed plan read leaves the field and the
	   schedule drawn, and a failed review read leaves the manifests readable
	   with their verdicts explicitly unread. */
	let risk = $state<Resource<RiskReport> | null>(null);
	let folded = $state<Resource<Plan> | null>(null);
	let reviews = $state<Resource<CheckpointReport> | null>(null);
	/* R8's fifth and sixth reads. `/status` bundles the graph, overhead,
	   burn-down, ETA and anomalies into one request; `/tokens` is the separate
	   ledger the category attribution reads from, because the bundle carries no
	   `by_category` and a failed read there leaves the category string
	   explicitly unread rather than absent. Each stands alone like the others. */
	let status = $state<Resource<StatusBundle> | null>(null);
	let ledger = $state<Resource<TokenLedger> | null>(null);
	let recovery = $state<Resource<RecoveryReport> | null>(null);
	const recoveryHasProbe = $derived(!!recovery?.data && Object.keys(recovery.data.outcomes).length > 0);
	let revision = $state<Resource<RecalibrationReport> | null>(null);
	let memoryStatus = $state<Resource<MemoryStatus> | null>(null);
	let kitchen = $state<Resource<Kitchen> | null>(null);
	let requested = $state<string | null>(null);
	let previousPlanId = $state<string | null>(null);
	let replayed = $state<Resource<Plan> | null>(null);
	let stops = $state<ReplayStop[]>([]);
	let replayIndex = $state(0);
	let replayKey = $state('');
	let replayNotice = $state('');
	let historical = $state(false);
	const setReplay = (value: boolean) => {
		historical = value;
		setHistoricalLatch(value);
	};
	$effect(() => {
		const id = plan.id;
		if (id === requested) return;
		requested = id;
		if (previousPlanId !== null && previousPlanId !== id) {
			const url = new URL(page.url);
			url.searchParams.delete('at');
			replaceState(url, {});
		}
		previousPlanId = id;
		risk?.dispose();
		folded?.dispose();
		reviews?.dispose();
		status?.dispose();
		ledger?.dispose();
		recovery?.dispose();
		revision?.dispose();
		memoryStatus?.dispose();
		kitchen?.dispose();
		replayed?.dispose();
		replayed = null;
		stops = [];
		replayKey = '';
		setReplay(false);
		activity = [];
		failures = {};
		selectedId = null;
		targetCheckpointId = null;
		focusOnOpen = false;
		if (!id) {
			risk = null;
			folded = null;
			reviews = null;
			status = null;
			ledger = null;
			recovery = null;
			revision = null;
			memoryStatus = null;
			kitchen = null;
			return;
		}
		const report = new Resource<RiskReport>((signal) => daemon.risk(id, signal));
		risk = report;
		void report.load();
		const document = new Resource<Plan>((signal) => daemon.plan(id, signal));
		folded = document;
		void document.load();
		const checkpoints = new Resource<CheckpointReport>((signal) =>
			daemon.checkpoints(id, signal)
		);
		reviews = checkpoints;
		void checkpoints.load();
		const bundle = new Resource<StatusBundle>((signal) => daemon.status(id, signal));
		status = bundle;
		void bundle.load();
		const ledgerRead = new Resource<TokenLedger>((signal) => daemon.tokens(id, signal));
		ledger = ledgerRead;
		void ledgerRead.load();
		const recoveryReport = new Resource<RecoveryReport>((signal) => daemon.recovery(id, signal));
		recovery = recoveryReport;
		void recoveryReport.load();
		const comparison = new Resource<RecalibrationReport>((signal) => daemon.revision(id, signal));
		revision = comparison;
		void comparison.load();
		const memoryRead = new Resource<MemoryStatus>((signal) => daemon.memoryStatus(id, signal));
		memoryStatus = memoryRead;
		void memoryRead.load();
		const kitchenRead = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
		kitchen = kitchenRead;
		void kitchenRead.load();
	});

	/* Replay is a second Plan read. The live graph remains the structural source;
	   this resource supplies only historical member state and fold-backed readers. */
	$effect(() => {
		const id = plan.id;
		const livePlan = folded?.data;
		const requestedAt = page.url.searchParams.get('at');
		if (!id || !livePlan || !requestedAt) return;
		const phase = runPhase(livePlan);
		if (!qualifies(phase)) {
			setReplay(false);
			replayNotice = 'This address asks for a past state of a run that has not stopped. It is showing the run as it stands.';
			return;
		}
		const nextStops = stops.length > 0 ? stops : stopsOf(livePlan);
		if (stops.length === 0) stops = nextStops;
		const nextIndex = nearestStop(nextStops, requestedAt);
		const resolvedAt = boundOf(nextStops, nextIndex);
		replayNotice = requestedAt === resolvedAt ? '' : 'No record was written at the moment this address names. Reading the last one before it.';
		if (replayIndex !== nextIndex) replayIndex = nextIndex;
		if (!historical) {
			setReplay(true);
			activity = [];
			failures = {};
		}
	});

	$effect(() => {
		const id = plan.id;
		const at = historical && stops.length > 0 ? boundOf(stops, replayIndex) : null;
		const key = id && at ? `${id}@${at}` : '';
		if (!key || key === untrack(() => replayKey)) return;
		/* Untracked: this effect owns `replayKey` and `replayed`; reading them as dependencies would
		   re-run it on its own writes and its cleanup would cancel the load it just scheduled. */
		const resource = untrack(() => {
			replayKey = key;
			const previous = replayed;
			previous?.dispose();
			const next = new Resource<Plan>((signal) => daemon.replay(id!, at!, signal));
			if (previous?.data) {
				next.data = previous.data;
				next.phase = 'ready';
				next.stale = true;
			}
			replayed = next;
			return next;
		});
		const timer = setTimeout(() => void resource.load(), 120);
		return () => clearTimeout(timer);
	});

	function moveReplay(index: number): void {
		if (stops.length === 0) return;
		const next = Math.max(0, Math.min(stops.length - 1, index));
		replayIndex = next;
		const url = new URL(page.url);
		url.searchParams.set('at', boundOf(stops, next));
		replaceState(url, {});
	}

	function enterReplay(): void {
		const document = folded?.data;
		if (!document || !qualifies(runPhase(document))) return;
		stops = stopsOf(document);
		replayIndex = stops.length - 1;
		setReplay(true);
		activity = [];
		failures = {};
		const url = new URL(page.url);
		url.searchParams.set('at', boundOf(stops, replayIndex));
		replaceState(url, {});
	}

	function returnToLive(): void {
		setReplay(false);
		replayed?.dispose();
		replayed = null;
		stops = [];
		replayKey = '';
		replayNotice = '';
		activity = [];
		failures = {};
		const url = new URL(page.url);
		url.searchParams.delete('at');
		replaceState(url, {});
		plan.reload();
		void risk?.load();
		void folded?.load();
		void reviews?.load();
		void status?.load();
		void ledger?.load();
		queueMicrotask(() => document.getElementById('replay-entry')?.focus());
	}

	/* What the fold does not keep, this page keeps for as long as it is open —
	   and says plainly that it starts empty. `RuntimeObserved` carries no
	   projected state, and `InitiativeFailed.reason` is dropped by the fold, so
	   the live stream is the only place either exists. */
	let activity = $state<RuntimeObservedFrame[]>([]);
	let failures = $state<Record<string, string>>({});

	function remember(type: string, data: unknown) {
		if (type === 'runtime_observed') {
			const frame = data as RuntimeObservedFrame;
			if (typeof frame?.attempt_id === 'string') activity = [...activity, frame];
		} else if (type === 'initiative_failed') {
			const frame = data as InitiativeFailedFrame;
			if (typeof frame?.initiative_id === 'string' && typeof frame.reason === 'string') {
				failures = { ...failures, [frame.initiative_id]: frame.reason };
			}
		}
	}

	/* Live only: the stream is the sole source, and a replay has none. */
	const launchingIds = $derived(
		historical || !folded?.data
			? new Set<string>()
			: launching(
					Object.fromEntries(
						Object.entries(folded.data.initiatives).map(([id, node]) => [id, node.attempts])
					),
					activity
				)
	);
	const needsInputIds = $derived(
		historical || !folded?.data
			? new Set<string>()
			: needsInput(
					Object.fromEntries(
						Object.entries(folded.data.initiatives).map(([id, node]) => [id, node.attempts])
					),
					activity
				)
	);

	/** This initiative's observations, in arrival order. Attempts hold the link. */
	function activityFor(id: string): { at: string; kind: string }[] {
		const attempts = new Set(
			(folded?.data?.initiatives[id]?.attempts ?? []).map((attempt) => attempt.id)
		);
		return activity.filter((frame) => attempts.has(frame.attempt_id));
	}

	/* The shell wired SSE and left it unconsumed; this is its first consumer.
	   A burst of events costs one re-read, and a dropped stream marks what is on
	   screen stale rather than freezing a projection that looks live. */
	/* null until the stream answers: connecting is not the same as dropped. */
	let live = $state<boolean | null>(null);
	$effect(() => {
		const id = plan.id;
		if (!id || historical) return;
		let timer: ReturnType<typeof setTimeout> | undefined;
		const stop = daemon.events(
			id,
			(type, data) => {
				remember(type, data);
				clearTimeout(timer);
				timer = setTimeout(() => {
					plan.reload();
					void risk?.load();
					void folded?.load();
					void reviews?.load();
					void status?.load();
					void ledger?.load();
					void recovery?.load();
					void revision?.load();
					void memoryStatus?.load();
				}, 120);
			},
			(connected) => {
				live = connected;
				if (!connected) {
					plan.resource?.markStale();
					risk?.markStale();
					folded?.markStale();
					reviews?.markStale();
					status?.markStale();
					ledger?.markStale();
					recovery?.markStale();
					revision?.markStale();
					memoryStatus?.markStale();
					kitchen?.markStale();
				}
			}
		);
		return () => {
			clearTimeout(timer);
			live = null;
			stop();
		};
	});

	/* --- tabs, selection and the dock ------------------------------------------------
	   Selection is an initiative id and nothing positional, so a live update that
	   reorders the field cannot move what you were reading. The address names the
	   tab, the member, the dock tab and (for fleet links) the checkpoint. */
	type TabId = 'field' | 'plan' | 'schedule' | 'burn' | 'recovery' | 'salvage' | 'stops';
	const TABS: TabId[] = ['field', 'plan', 'schedule', 'burn', 'recovery', 'salvage', 'stops'];
	const isTab = (value: string | null): value is TabId => !!value && (TABS as string[]).includes(value);
	const DTABS = ['overview', 'brief', 'review', 'diff', 'packet', 'attempts', 'activity'];

	let tab = $state<TabId>('field');
	let dtab = $state('overview');
	let dockMode = $state<'docked' | 'collapsed' | 'max'>('collapsed');
	let selectedId = $state<string | null>(null);
	let targetCheckpointId = $state<string | null>(null);
	/* True only when the *address* opened the member, never a click: arrival focus is
	   claimed by the dock's own heading then, and a click must not steal the caret. */
	let focusOnOpen = $state(false);
	let fit = $state(false);
	let keyOpen = $state(false);

	function writeUrl(change: (url: URL) => void): void {
		const url = new URL(page.url);
		change(url);
		replaceState(url, {});
	}

	function applyTab(next: TabId, write: boolean): void {
		tab = next;
		if (next === 'plan') dockMode = 'collapsed';
		if (write) writeUrl((url) => url.searchParams.set('tab', next));
	}

	/* The first graph of a plan picks its tab (a proposed plan opens on Plan); after
	   that the address wins whenever it changes. */
	let tabbedPlan = '';
	$effect(() => {
		const graph = plan.resource?.data;
		if (!graph || tabbedPlan === graph.plan_id) return;
		tabbedPlan = graph.plan_id;
		const asked = page.url.searchParams.get('tab');
		const narrow = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches;
		applyTab(
			isTab(asked) ? asked : graph.approval !== 'approved' ? 'plan' : narrow ? 'schedule' : 'field',
			false
		);
		const asked_d = page.url.searchParams.get('dtab');
		if (asked_d && DTABS.includes(asked_d)) dtab = asked_d;
	});
	$effect(() => {
		const asked = page.url.searchParams.get('tab');
		if (isTab(asked) && asked !== tab) applyTab(asked, false);
	});

	function setDtab(next: string): void {
		dtab = next;
		writeUrl((url) => url.searchParams.set('dtab', next));
	}

	/* Which address `select` itself wrote, so the effect that address triggers can tell a
	   click from an arrival. A plain value: no render reads it. */
	let clickWrote: string | null = null;
	const select = (id: string) => {
		selectedId = id;
		targetCheckpointId = null;
		focusOnOpen = false;
		if (dockMode === 'collapsed' && tab !== 'plan') dockMode = 'docked';
		clickWrote = `${plan.id ?? ''}\u0000${id}\u0000`;
		writeUrl((url) => {
			url.searchParams.set('initiative', id);
			url.searchParams.delete('checkpoint');
		});
	};

	/* Fleet links address the member (and a checkpoint) the dock reads. A checkpoint
	   link opens the Review tab. */
	let addressedLink = $state('');
	$effect(() => {
		const { initiative, checkpoint } = runTarget(page.url.searchParams);
		const address = `${plan.id ?? ''}\u0000${initiative ?? ''}\u0000${checkpoint ?? ''}`;
		const wrote = clickWrote;
		clickWrote = null;
		if (address === addressedLink) return;
		addressedLink = address;
		if (!plan.id || !initiative) return;
		const byAddress = address !== wrote;
		selectedId = initiative;
		targetCheckpointId = checkpoint;
		focusOnOpen = byAddress;
		if (checkpoint) dtab = 'review';
		if (dockMode === 'collapsed' && tab !== 'plan') dockMode = 'docked';
	});

	/** Stepping is in schedule order, from anywhere in Run. */
	function stepMember(order: string[], delta: 1 | -1): void {
		const next = step(order, selectedId, delta);
		if (next && next !== selectedId) {
			select(next);
			reveal(next);
		}
	}
	function reveal(id: string): void {
		if (tab !== 'field') applyTab('field', true);
		queueMicrotask(() =>
			document.getElementById(`seat-${id}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' })
		);
	}
	function typing(target: EventTarget | null): boolean {
		const el = target as HTMLElement | null;
		return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
	}
	function onkeydown(event: KeyboardEvent) {
		if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) return;
		if (event.key === 'j' || event.key === 'J') { event.preventDefault(); stepMember(order, 1); }
		else if (event.key === 'k' || event.key === 'K') { event.preventDefault(); stepMember(order, -1); }
		else if (event.key === '?') { event.preventDefault(); keyOpen = !keyOpen; }
		else if (event.key === 'Escape' && keyOpen) keyOpen = false;
	}

	function focusRecovery() {
		applyTab('recovery', true);
		queueMicrotask(() => {
			const target = document.getElementById('recovery-label');
			target?.scrollIntoView({ block: 'start', behavior: 'auto' });
			target?.focus();
		});
	}

	/* --- the clock: coarse on purpose, so a still field does not change under the eye -- */
	let now = $state(Date.now());
	$effect(() => {
		const timer = setInterval(() => (now = Date.now()), 5000);
		return () => clearInterval(timer);
	});

	/* --- titleblock actions (DS §9.2) -------------------------------------------------- */
	let copied = $state(false);
	async function copyPlanId(id: string) {
		try {
			await navigator.clipboard.writeText(id);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			/* clipboard refused: nothing claimed */
		}
	}
	let confirmDelete = $state(false);
	let rowBusy = $state(false);
	let rowError = $state('');
	async function archiveRun(id: string) {
		if (rowBusy) return;
		rowBusy = true;
		rowError = '';
		try {
			await daemon.archive(id, 'archived from Run', crypto.randomUUID());
			fleetCtx.reload();
			void goto('/home');
		} catch (cause) {
			rowError = cause instanceof Error ? cause.message : 'The archive failed.';
		} finally {
			rowBusy = false;
		}
	}
	async function eraseRun(id: string) {
		if (rowBusy) return;
		rowBusy = true;
		rowError = '';
		try {
			await daemon.deletePlan(id);
			fleetCtx.reload();
			void goto('/home');
		} catch (cause) {
			rowError = cause instanceof Error ? cause.message : 'The delete failed.';
			confirmDelete = false;
		} finally {
			rowBusy = false;
		}
	}
	$effect(() => {
		void plan.id;
		confirmDelete = false;
		rowError = '';
	});
	useTitleActions(() => (plan.id && plan.resource?.data ? titleActions : null));

	function historicalField(field: Field, document: Plan): Field {
		const settled = new Set(
			Object.values(document.initiatives)
				.filter((initiative) => initiative.state === 'settled' || initiative.state === 'cancelled')
				.map((initiative) => initiative.spec.id)
		);
		const members = field.members.map((member) => {
			const initiative = document.initiatives[member.node.initiative_id];
			const state = initiative?.state ?? 'pending';
			const mapped: Member['state'] = state === 'settled' ? 'seated' : state === 'running' ? 'loaded' : state === 'failed' ? 'failed' : 'slack';
			return {
				...member,
				node: { ...member.node, state, ready: false },
				state: mapped,
				cancelled: state === 'cancelled',
				blockedBy: member.node.depends_on.filter((dependency) => !settled.has(dependency))
			};
		});
		const byId = new Map(members.map((member) => [member.node.initiative_id, member]));
		const lanes = field.lanes.map((lane) => lane.map((member) => byId.get(member.node.initiative_id)!));
		return {
			...field,
			members,
			byId,
			lanes,
			crossings: field.crossings.map(({ from, to }) => ({ from: byId.get(from.node.initiative_id)!, to: byId.get(to.node.initiative_id)! })),
			criticalPath: field.criticalPath.map((member) => byId.get(member.node.initiative_id)!).filter(Boolean)
		};
	}
	/* --- what the page draws from (one derivation, so the tabs share a selection) -------- */
	const graph = $derived(plan.resource?.data ?? null);
	const baseField = $derived(graph && graph.nodes.length > 0 ? buildField(graph) : null);
	const replayPlan = $derived(replayed?.data ?? null);
	const structureMatches = $derived(
		!historical ||
			!replayPlan ||
			!graph ||
			(replayPlan.version === graph.version &&
				Object.keys(replayPlan.initiatives).length === graph.nodes.length &&
				graph.nodes.every((node) => replayPlan.initiatives[node.initiative_id] !== undefined))
	);
	const field = $derived(baseField ? (historical && replayPlan ? historicalField(baseField, replayPlan) : baseField) : null);
	const contention = $derived(historical ? new Map<string, Touch[]>() : contentionIndex(risk?.data ?? null));
	const phase = $derived(graph ? phaseOf(graph) : 'running');
	const liveFoldPhase = $derived(runPhase(folded?.data));
	const replayFoldPhase = $derived(runPhase(replayPlan));
	const conflicts = $derived(risk?.data?.conflicts.length ?? null);
	const order = $derived(field ? field.members.map((m) => m.node.initiative_id) : []);
	const selected = $derived(field && selectedId ? (field.byId.get(selectedId) ?? null) : null);
	const shownPlan = $derived(historical ? replayPlan : (folded?.data ?? null));
	const staleCount = $derived(recovery?.data?.stale.length ?? 0);
	const hasSalvage = $derived(
		!!folded?.data &&
			(folded.data.memory_receipts.some((receipt) => receipt.operation === 'salvage') ||
				[...Object.values(folded.data.initiatives), ...folded.data.retired].some((initiative) => initiative.failures.length > 0))
	);
	const runRow = $derived(fleetCtx.resource.data?.runs.find((run) => run.plan_id === plan.id) ?? null);
	/* Needs-you is the operator being the blocker: the run's blocking attention items, plus
	   any member whose agent is stopped at a dialog (live only). */
	const needsIds = $derived(
		new Set<string>([
			...needsInputIds,
			...(historical ? [] : (runRow?.attention ?? []).filter((item) => item.blocking !== false && item.initiative_id).map((item) => item.initiative_id as string))
		])
	);
	const needsCount = $derived(
		historical ? null : (runRow?.attention ? runRow.attention.filter((item) => item.blocking !== false).length : needsInputIds.size)
	);

	const counts = $derived.by(() => {
		const c = { settled: 0, running: 0, needs: 0, failed: 0, waiting: 0, total: 0 };
		for (const m of field?.members ?? []) {
			c.total++;
			const s = m.node.state;
			if (s === 'failed') c.failed++;
			else if (needsIds.has(m.node.initiative_id)) c.needs++;
			else if (s === 'settled') c.settled++;
			else if (s === 'running') c.running++;
			else c.waiting++;
		}
		return c;
	});
	const runTone = $derived.by((): { tone: Tone; word: string } => {
		if (!graph) return { tone: 'idle', word: 'Idle' };
		if (graph.approval !== 'approved') return { tone: 'needs', word: 'Awaiting approval' };
		if (!field) return { tone: 'idle', word: 'Empty' };
		if (counts.running + counts.needs > 0) return { tone: counts.needs > 0 ? 'needs' : 'running', word: counts.needs > 0 ? 'Needs you' : 'Running' };
		if (counts.failed > 0) return { tone: 'failed', word: 'Failed' };
		return counts.settled === counts.total ? { tone: 'settled', word: 'Settled' } : { tone: 'idle', word: 'Idle' };
	});
	const elapsed = $derived.by((): string | null => {
		const doc = folded?.data;
		if (historical || !doc) return null;
		const attempts = Object.values(doc.initiatives).flatMap((initiative) => initiative.attempts);
		const starts = attempts.map((a) => Date.parse(a.started_at)).filter((n) => !Number.isNaN(n));
		if (starts.length === 0) return null;
		const start = Math.min(...starts);
		const open = attempts.some((a) => !a.ended_at);
		const ends = attempts.map((a) => Date.parse(a.ended_at ?? '')).filter((n) => !Number.isNaN(n));
		const end = open || ends.length === 0 ? now : Math.max(...ends);
		const text = clock(Math.max(0, (end - start) / 1000));
		return text === '—' ? '0:00' : text;
	});
	const spent = $derived(historical ? null : (status?.data?.burn_down.accounted_tokens ?? null));
	const title = $derived(folded?.data ? (folded.data.title ?? folded.data.brief.split('\n').find((l) => l.trim())?.trim() ?? graph?.plan_id) : (graph?.plan_id ?? ''));
	const branches = $derived(folded?.data as (Plan & { start_branch?: string | null; target_branch?: string | null }) | null);

	const tabItems = $derived.by((): TabItem[] => {
		const items: TabItem[] = [
			{ id: 'field', label: 'Field', icon: 'network' },
			{ id: 'plan', label: 'Plan', icon: 'file-text', count: graph ? `v${graph.version}` : undefined, countTone: graph && graph.approval !== 'approved' ? 'nd' : undefined },
			{ id: 'schedule', label: 'Schedule', icon: 'list-tree', count: field?.members.length },
			{ id: 'burn', label: 'Burn', icon: 'flame' }
		];
		if (!historical && phase !== 'proposed') items.push({ id: 'recovery', label: 'Recovery', icon: 'wrench', count: staleCount || undefined, countTone: staleCount ? 'bad' : undefined });
		if (!historical && hasSalvage) items.push({ id: 'salvage', label: 'Salvage', icon: 'life-buoy' });
		if (historical) items.push({ id: 'stops', label: 'Stops', icon: 'history', count: stops.length });
		return items;
	});
	const shownTab = $derived<TabId>(tabItems.some((item) => item.id === tab) ? tab : 'field');

	function waiting(m: Member): string {
		if (m.node.state !== 'pending') return '—';
		return m.node.ready ? 'nothing — ready' : m.blockedBy.join(', ');
	}
	const harnessNode = (m: Member) => m.node;
	const effortOf = (id: string): string =>
		shownPlan?.initiatives[id]?.assignment_override?.effort ?? shownPlan?.initiatives[id]?.spec.assignment.effort ?? '—';
	function memberTime(m: Member): string {
		const attempts = shownPlan?.initiatives[m.node.initiative_id]?.attempts ?? [];
		if (m.node.state === 'running') {
			const live = liveSeconds(attempts, now);
			if (live !== null) return `▸ ${runningClock(live)}`;
		}
		return attempts.length ? clock(spentSeconds(attempts)) : '—';
	}
	function onScheduleKey(event: KeyboardEvent) {
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		const next = step(order, selectedId, event.key === 'ArrowDown' ? 1 : -1);
		if (!next) return;
		event.preventDefault();
		select(next);
		document.getElementById(`row-${next}`)?.focus();
	}

	const RUN_TONE: Record<string, { tone: Tone; word: string }> = {
		running: { tone: 'running', word: 'Running' },
		awaiting_approval: { tone: 'needs', word: 'Awaiting approval' },
		settled: { tone: 'settled', word: 'Settled' },
		failed: { tone: 'failed', word: 'Failed' }
	};
	const runWord = (status: string) => RUN_TONE[status] ?? { tone: 'idle' as Tone, word: status.replace(/_/g, ' ') };

</script>

<svelte:head><title>{title ? `${title} — Run` : 'Run'} — Herdsman</title></svelte:head>
<svelte:window {onkeydown} />

{#snippet titleActions()}
	{#if graph}
		<span class="ibar">
			{#if rowError}<span class="state" data-tone="failed" role="alert">{rowError}</span>{/if}
			<IconButton icon="pause" label="Pause run" disabled reason="Pausing a whole run is not available yet" />
			<span id="replay-entry-wrap">
				<IconButton
					icon="history"
					label="Replay"
					pressed={historical}
					disabled={!historical && !(folded?.data && qualifies(liveFoldPhase))}
					reason={qualificationSentence(liveFoldPhase)}
					onclick={historical ? returnToLive : enterReplay}
				/>
			</span>
			<span class="sep"></span>
			<IconButton icon={copied ? 'check' : 'copy'} label={copied ? 'Copied' : 'Copy plan id'} onclick={() => void copyPlanId(graph.plan_id)} />
			{#if confirmDelete}
				<span class="confirm" role="group" aria-label="Confirm delete">
					<span class="lbl">Delete?</span>
					<Button icon="trash-2" kind="danger" small busy={rowBusy} onclick={() => void eraseRun(graph.plan_id)}>Delete</Button>
					<Button icon="x" small onclick={() => (confirmDelete = false)}>Keep</Button>
				</span>
			{:else}
				<IconButton icon="archive" label="Archive" disabled={historical || rowBusy} reason="Return to live first" onclick={() => void archiveRun(graph.plan_id)} />
				<IconButton icon="trash-2" label="Delete" danger disabled={historical} reason="Return to live first" onclick={() => (confirmDelete = true)} />
			{/if}
		</span>
	{/if}
{/snippet}

{#snippet stateForms()}
	{#each [['settled', 'Settled'], ['running', 'Running'], ['needs', 'Needs you'], ['failed', 'Failed'], ['ready', 'Ready'], ['waiting', 'Waiting'], ['paused', 'Paused'], ['cancelled', 'Cancelled']] as [t, word] (t)}
		<li><span class="sw" data-tone={t}><i></i></span><span class="state" data-tone={t}>{word}</span></li>
	{/each}
{/snippet}

{#if !plan.id}
	<!-- A plan is chosen from the plans that exist; no id is typed here. -->
	<div class="run picker">
		<section class="ph">
			<div class="title"><h1 class="h-title">Run</h1><div class="meta"><span class="lbl">Choose a plan</span></div></div>
		</section>
		<div class="scroll">
			<AsyncField resource={fleetCtx.resource} reading="the fleet" onretry={fleetCtx.reload}>
				{#snippet children(fleet: Fleet)}
					{#if fleet.runs.length === 0}
						<p class="empty-line">No runs yet. New dispatch starts one.</p>
					{:else}
						<table class="tbl">
							<caption class="sr-only">Runs, newest first</caption>
							<colgroup><col /><col style="width: 170px" /><col style="width: 110px" /><col class="plan-col" style="width: 280px" /></colgroup>
							<thead><tr><th>Run</th><th>State</th><th>Updated</th><th class="plan-col">Plan</th></tr></thead>
							<tbody>
								{#each fleet.runs as run (run.plan_id)}
									{@const w = runWord(run.status)}
									<tr class="click" onclick={() => void goto(run.link.path)}>
										<td class="cut"><a class="pick ellipsis" href={run.link.path} onclick={(e) => e.stopPropagation()}>{run.title ?? run.brief.split('\n')[0]}</a></td>
										<td><StateMark tone={w.tone} word={w.word} /></td>
										<td class="mono">{ago(run.updated_at, now)}</td>
										<td class="mono muted plan-col cut">{run.plan_id}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
					{@const broken = fleet.unreadable ?? []}
					{#if broken.length > 0}
						<p class="async-strip" role="status"><span class="state" data-tone="failed">Unreadable</span><span class="mono">{broken.join(', ')} on disk and could not be folded, so {broken.length === 1 ? 'it is' : 'they are'} in no count and cannot be opened.</span></p>
					{/if}
				{/snippet}
			</AsyncField>
		</div>
	</div>
{:else if !graph}
	<div class="run picker">
		{#if plan.resource}<AsyncField resource={plan.resource} reading="the plan projection" onretry={plan.reload}>{#snippet children(_: PlanGraph)}{/snippet}</AsyncField>{/if}
	</div>
{:else}
	<div class="run" class:replaying={historical && stops.length > 0}>
		<div class="top">
			{#if plan.resource?.stale}
				<p class="async-strip" role="status">
					<span class="state" data-tone="waiting">Stale</span>
					<span class="mono muted">last confirmed {plan.resource.loadedAt ? plan.resource.loadedAt.toLocaleTimeString() : '—'}</span>
					<Button icon="rotate-ccw" small onclick={plan.reload}>Read again</Button>
				</p>
			{/if}
			<section class="ph">
				<div class="title">
					<h1 class="h-title" title={title}>{title}</h1>
					<div class="meta">
						<StateMark tone={runTone.tone} word={runTone.word} />
						{#if branches && (branches.start_branch || branches.target_branch)}
							<span class="branch"><Icon name="git-branch" size={14} />{branches.start_branch ?? '—'}<Icon name="chevron-right" size={14} />{branches.target_branch ?? '—'}</span>
						{/if}
						<span class="mono muted ellipsis planid" title={graph.plan_id}>{graph.plan_id}</span>
						<span class="lbl">Rev {graph.version} · {graph.approval === 'approved' ? 'Approved' : 'Proposed'}</span>
					</div>
				</div>
				<div class="tele" aria-label="Run telemetry">
					<div>
						<span class="lbl">Settled</span>
						<span class="num">{counts.settled}<small>/{counts.total}</small></span>
						<Spectrum settled={counts.settled} running={counts.running} needs={counts.needs} failed={counts.failed} waiting={counts.waiting} />
					</div>
					<div class="t-minor"><span class="lbl">Running</span><span class="num">{counts.running}</span></div>
					<div><span class="lbl">Needs you</span><span class="num" class:nd={(needsCount ?? 0) > 0}>{needsCount ?? '—'}</span></div>
					<div><span class="lbl">Failed</span><span class="num" class:bad={counts.failed > 0}>{counts.failed}</span></div>
					<div><span class="lbl">Tokens · cap</span><span class="num">{spent === null ? '—' : tokens(spent)}<small>&nbsp;/&nbsp;{folded?.data?.token_cap != null ? tokens(folded.data.token_cap) : '—'}</small></span></div>
					<div class="t-elapsed"><span class="lbl">Elapsed</span><span class="num">{elapsed ?? '—'}</span></div>
				</div>
			</section>
			{#if replayNotice}<p class="async-strip" role="status"><span class="state" data-tone="waiting">Replay</span><span>{replayNotice}</span></p>{/if}
			{#if historical && replayed?.data}
				{@const notice = stopReading(stops, replayIndex, replayFoldPhase, liveFoldPhase)}
				{#if notice}<p class="async-strip" role="status"><span class="state" data-tone="waiting">Replay</span><span>{notice}</span></p>{/if}
			{/if}
			{#if field && !historical && !field.agrees}
				<p class="async-strip" role="alert"><span class="state" data-tone="failed">Lanes disagree</span><span>This build drew {field.lanes.length} lanes where the daemon computes a maximum concurrency of {graph.max_concurrency}; treat the lanes as unreliable until they agree.</span></p>
			{/if}
			{#if !historical && risk?.phase === 'error' && risk.error}
				<p class="async-strip" role="status"><span class="state" data-tone="waiting">Contention unread</span><span>{risk.error.message} The field is drawn without its conflict cords; that is unknown, not none.</span><Button icon="rotate-ccw" small onclick={() => void risk?.load()}>Read again</Button></p>
			{/if}
		</div>

		<nav class="subnav" aria-label="Run sections">
			<Tabs items={tabItems} selected={shownTab} prefix="run" label="Run sections" onselect={(id) => applyTab(id as TabId, true)} />
			<div class="tools">
				{#if field}<span class="lbl">{field.lanes.length} {field.lanes.length === 1 ? 'lane' : 'lanes'} · critical path {graph.critical_path.length || '—'}</span>{/if}
				<span class="ibar">
					<IconButton icon="maximize-2" label="Fit field" small pressed={fit} disabled={shownTab !== 'field'} onclick={() => (fit = !fit)} />
					<IconButton icon="circle-help" label="Key" shortcut="?" small pressed={keyOpen} onclick={() => (keyOpen = !keyOpen)} />
				</span>
			</div>
		</nav>

		{#if historical && stops.length > 0}
			<ReplayBar {stops} index={replayIndex} {historical} onindex={moveReplay} onreturn={returnToLive} stale={replayed?.stale ?? false} />
		{/if}

		<section class="stage">
			{#key shownTab}
				{#if shownTab === 'field'}
					<div class="tabpanel fill" role="tabpanel" id={panelId('run', 'field')} aria-labelledby={tabId('run', 'field')}>
						{#if !field}
							<p class="empty-line">Plan {graph.plan_id} is at revision {graph.version} and its planner proposed no initiatives. There is no structure to draw.</p>
						{:else if historical && !replayed?.data}
							<p class="empty-line" aria-busy="true">Reading the historical plan projection. The live plan is not substituted for a missing record.</p>
						{:else if !historical || structureMatches}
							<ContentionField {field} {contention} waiting={needsIds} starting={launchingIds} contentionRead={!historical && risk?.data != null} selected={selectedId} planId={graph.plan_id} plan={shownPlan} kitchen={kitchen?.data ?? null} {now} {fit} onselect={select} />
						{:else}
							<p class="empty-line">The plan's structure changed after this moment. The drawing shows the structure this run finished with, which is not the one that existed here, so it is not drawn against this bound.</p>
						{/if}
					</div>
				{:else if shownTab === 'plan'}
					<div class="tabpanel fill" role="tabpanel" id={panelId('run', 'plan')} aria-labelledby={tabId('run', 'plan')}>
						{#if field}
							<RunPlan
								planId={graph.plan_id}
								{graph}
								{field}
								{risk}
								plan={folded}
								{revision}
								{reviews}
								accounted={spent}
								readonly={historical}
								onselect={select}
								onapproved={() => {
									plan.reload();
									void folded?.load();
									void revision?.load();
									void status?.load();
									applyTab('field', true);
								}}
								onrevised={() => {
									plan.reload();
									void risk?.load();
									void folded?.load();
									void reviews?.load();
									void revision?.load();
								}}
							/>
						{:else}
							<p class="empty-line">This plan has no initiatives, so there is nothing to approve.</p>
						{/if}
					</div>
				{:else if shownTab === 'schedule'}
					<div class="tabpanel scroll pane-in" role="tabpanel" id={panelId('run', 'schedule')} aria-labelledby={tabId('run', 'schedule')}>
						{#if field}
							<table class="tbl sched">
								<caption class="sr-only">Initiatives in lane and rank order, with state, assignment, what each waits on, and what it contends with. Arrow keys move between rows.</caption>
								<thead>
									<tr>
										<th scope="col">Member</th>
										<th scope="col">State</th>
										<th scope="col" class="c-assign">Harness › Model</th>
										<th scope="col" class="c-effort">Effort</th>
										<th scope="col" class="n c-place">Lane</th>
										<th scope="col" class="c-waits">Waits on</th>
										<th scope="col" class="c-contend">Contends with</th>
										<th scope="col" class="n">Time</th>
									</tr>
								</thead>
								<tbody>
									{#each field.members as m (m.node.initiative_id)}
										{@const id = m.node.initiative_id}
										{@const touches = contention.get(id) ?? []}
										{@const tone = memberTone(m.node, needsIds.has(id))}
										<tr class="click" aria-current={selectedId === id ? 'true' : undefined} onclick={() => select(id)}>
											<td>
												<button id="row-{id}" type="button" class="pick cell" tabindex={id === (selectedId ?? order[0]) ? 0 : -1} onclick={(e) => { e.stopPropagation(); select(id); }} onkeydown={onScheduleKey}>
													<span class="mono">{id}</span><span class="ellipsis nm">{m.node.name}</span>{#if m.onCriticalPath}<span class="chip cp">critical</span>{/if}
												</button>
											</td>
											<td><StateMark {tone} /></td>
											<td class="c-assign"><div class="cell"><Mark harness={m.node.harness} /><span>{m.node.harness}</span><Icon name="chevron-right" size={14} /><Mark model={m.node.model} /><span class="mono ellipsis">{m.node.model}</span></div></td>
											<td class="c-effort">{effortOf(id)}</td>
											<td class="n c-place">{String(m.lane + 1).padStart(2, '0')}·{m.depth}</td>
											<td class="mono c-waits">{waiting(m)}</td>
											<td class="c-contend">
												{#if !risk?.data}<span class="muted">unread</span>
												{:else if touches.length === 0}—
												{:else}
													{#each touches as touch (touch.peer + touch.kind)}
														<span class="touch"><span class="state" data-tone={touch.kind === 'write_write' ? 'failed' : 'waiting'}>{touch.peer}</span><span class="sr-only">{touch.kind === 'write_write' ? 'write conflict' : 'missing edge'} on</span> <span class="mono muted">{touch.paths.join(', ')}</span></span>
													{/each}
												{/if}
											</td>
											<td class="n">{memberTime(m)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						{:else}
							<p class="empty-line">This plan has no initiatives.</p>
						{/if}
					</div>
				{:else if shownTab === 'burn'}
					<div class="tabpanel scroll" role="tabpanel" id={panelId('run', 'burn')} aria-labelledby={tabId('run', 'burn')}>
						{#if !historical && status && ledger}
							<BurnTab {status} {ledger} planCap={folded?.data?.token_cap ?? null} members={(field?.members ?? []).map((m) => ({ id: m.node.initiative_id, name: m.node.name, harness: m.node.harness, model: m.node.model }))} selected={selectedId} onselect={select} />
						{:else}
							<p class="empty-line">Token and timing instruments are not replayed. They are served for the run as it stands, and reading them beside a past state would date them wrongly.</p>
						{/if}
					</div>
				{:else if shownTab === 'recovery'}
					<div class="tabpanel scroll" role="tabpanel" id={panelId('run', 'recovery')} aria-labelledby={tabId('run', 'recovery')}>
						{#if recovery}<Recovery planId={graph.plan_id} resource={recovery} onretry={() => void recovery?.load()} onselect={select} />{/if}
					</div>
				{:else if shownTab === 'salvage'}
					<div class="tabpanel scroll" role="tabpanel" id={panelId('run', 'salvage')} aria-labelledby={tabId('run', 'salvage')}>
						<Salvage plan={folded?.data ?? null} onchanged={() => { void folded?.load(); void memoryStatus?.load(); }} />
					</div>
				{:else if shownTab === 'stops'}
					<div class="tabpanel scroll" role="tabpanel" id={panelId('run', 'stops')} aria-labelledby={tabId('run', 'stops')}>
						{#if historical && replayed?.data}<ReplayRegister {stops} index={replayIndex} plan={replayed.data} onstop={moveReplay} onselect={select} />{/if}
					</div>
				{/if}
			{/key}
		</section>

		{#if keyOpen}
			<div class="keypop pane-in" role="dialog" aria-label="Field key">
				<div class="rule-h"><span class="lbl">Line form is state</span></div>
				<ul>{@render stateForms()}</ul>
				<div class="rule-h gap"><span class="lbl">Line colour is harness</span></div>
				<ul>
					{#each ['claude-code', 'codex', 'pi', 'opencode'] as h (h)}
						<li><span class="sw"><i style:background={harnessHue(h)}></i></span><Mark harness={h} size={16} /><span>{h}</span></li>
					{/each}
				</ul>
				<div class="btnrow"><IconButton icon="x" label="Close key" shortcut="Esc" small onclick={() => (keyOpen = false)} /></div>
			</div>
		{/if}

		<InitiativeDock
			planId={graph.plan_id}
			id={selectedId}
			member={selectedId ? (field?.byId.get(selectedId) ?? null) : null}
			plan={historical ? replayed : folded}
			{historical}
			{graph}
			report={reviews}
			approved={graph.approval === 'approved'}
			{memoryStatus}
			{kitchen}
			activity={selectedId ? activityFor(selectedId) : []}
			waiting={selectedId !== null && !historical && needsInputIds.has(selectedId)}
			starting={selectedId !== null && !historical && launchingIds.has(selectedId)}
			failure={selectedId ? (failures[selectedId] ?? null) : null}
			staleAttempt={selectedId ? (recovery?.data?.stale.find((attempt) => attempt.initiative_id === selectedId) ?? null) : null}
			{targetCheckpointId}
			{focusOnOpen}
			{dtab}
			ondtab={setDtab}
			onstep={(delta: 1 | -1) => stepMember(order, delta)}
			onlocate={() => selectedId && reveal(selectedId)}
			onrecovery={focusRecovery}
			ondecided={() => {
				plan.reload();
				void risk?.load();
				void folded?.load();
				void reviews?.load();
				void recovery?.load();
			}}
			onclose={() => (dockMode = 'collapsed')}
			bind:mode={dockMode}
		/>
	</div>
{/if}

<style>
	.run {
		position: relative; display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; min-height: 0; min-width: 0;
		height: 100%;
	}
	.run.replaying { grid-template-rows: auto auto auto minmax(0, 1fr) auto; }
	.run.picker { display: block; overflow: auto; }
	.top { min-width: 0; }
	.planid { max-width: 22ch; }
	.stage { position: relative; min-height: 0; min-width: 0; overflow: hidden; display: grid; }
	.tabpanel { min-height: 0; min-width: 0; }
	.tabpanel.fill { height: 100%; }
	.scroll { overflow: auto; min-height: 0; height: 100%; }
	.picker .scroll { height: auto; }
	.tbl { margin: 0; }
	.picker .tbl { table-layout: fixed; }
	.picker .tbl th:first-child, .picker .tbl td:first-child { padding-left: 24px; }
	.picker .tbl th:last-child, .picker .tbl td:last-child { padding-right: 24px; }
	.cut { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.cut > a { display: block; }
	.sched { margin: 8px 0 24px; }
	.sched th:first-child, .sched td:first-child { padding-left: 24px; }
	.sched th:last-child, .sched td:last-child { padding-right: 24px; }
	.pick { background: none; border: 0; padding: 0; color: var(--tx); text-align: left; cursor: pointer; text-decoration: none; }
	button.pick { max-width: 100%; }
	.pick:hover { color: var(--tx); }
	.nm { max-width: 32ch; color: var(--tx2); }
	.sched th.n { font-family: var(--f-label); font-size: 11px; }
	.c-assign .cell { white-space: nowrap; }
	.c-assign .mono { max-width: 18ch; }
	.c-contend { max-width: 34ch; }
	.cp { height: 18px; font-size: 10px; padding: 0 5px; }
	.touch { display: flex; gap: 8px; align-items: baseline; }
	.touch + .touch { margin-top: 4px; }

	.confirm { display: inline-flex; align-items: center; gap: 8px; padding-left: 6px; }
	.tele .num.nd { color: var(--l589); }

	.keypop {
		position: absolute; right: 24px; top: 96px; z-index: 30; padding: 16px; width: 320px; background: var(--p2);
		border: 1px solid var(--ln2);
	}
	.keypop ul { list-style: none; display: grid; gap: 9px; }
	.keypop li { display: flex; gap: 12px; align-items: center; }
	.keypop .gap { margin-top: 14px; }
	.keypop .btnrow { margin-top: 10px; justify-content: flex-end; }
	.sw { width: 26px; height: 16px; position: relative; flex: none; }
	.sw i { position: absolute; left: 12px; top: 0; width: 2px; height: 16px; background: var(--sc, var(--tx)); }
	.sw[data-tone='settled'] i { opacity: 0.5; background: var(--dim); }
	.sw[data-tone='running'] i { background: var(--tx); }
	.sw[data-tone='needs'] i { background: var(--l589); box-shadow: 4px 0 0 var(--l589); }
	.sw[data-tone='failed'] i { background: linear-gradient(var(--l656) 0 5px, transparent 5px 10px, var(--l656) 10px); }
	.sw[data-tone='ready'] i, .sw[data-tone='waiting'] i { background: repeating-linear-gradient(var(--tx) 0 3px, transparent 3px 6px); }
	.sw[data-tone='waiting'] i { opacity: 0.6; }
	.sw[data-tone='paused'] i { top: 8px; height: 8px; background: var(--tx2); }
	.sw[data-tone='cancelled'] i { background: var(--fnt); }

	@media (max-width: 1279px) {
		.t-elapsed { display: none; }
	}
	@media (max-width: 1023px) {
		.t-minor { display: none; }
		.c-contend, .c-effort { display: none; }
	}
	@media (max-width: 767px) {
		.tele { display: none; }
		.c-assign, .c-waits, .plan-col { display: none; }
	}
</style>
