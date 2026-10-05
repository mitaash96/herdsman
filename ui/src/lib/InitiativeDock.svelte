<script lang="ts" module>
	export type DockMode = 'docked' | 'collapsed' | 'max';
	const HEIGHT_KEY = 'herdsman.dock.h';
	const DEFAULT_H = 340;
	const HEADER = 49; // 48px header + 1px hairline: collapsed is header only
	const SNAP_COLLAPSE = 90;
	const SNAP_MAX = 40;
	const ACTIVITY_CAP = 200;
	const NEEDS_LINE = /needs|waiting|blocked|input|approval|dialog|login|trust/i;

	/** A unified diff cut into per-file sections; the daemon's `files` order is kept by the caller. */
	function splitPatch(text: string): { path: string; lines: string[] }[] {
		const sections: { path: string; lines: string[] }[] = [];
		for (const line of text.split('\n')) {
			if (line.startsWith('diff --git ')) {
				const at = line.lastIndexOf(' b/');
				sections.push({ path: at === -1 ? line.slice(11) : line.slice(at + 3), lines: [line] });
			} else sections.at(-1)?.lines.push(line);
		}
		return sections;
	}
	const lineKind = (line: string): 'add' | 'del' | 'hunk' | 'meta' | 'ctx' =>
		line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')
			? 'meta'
			: line.startsWith('@@')
				? 'hunk'
				: line.startsWith('+')
					? 'add'
					: line.startsWith('-')
						? 'del'
						: 'ctx';
</script>

<script lang="ts">
	/*
	  The Run dock (DS §9.14, redesign U3). It replaces the initiative drawer: the
	  same reads, the same writes, none of the drawer's seat. Logic that used to
	  live in InitiativeDrawer is folded in here; the interventions, checkpoint
	  review and packet inspector are its Overview, Review and Packet panes.

	  Geometry: in flow only as its docked/collapsed height. Maximized it is
	  absolutely positioned over the whole Run region (nearest positioned
	  ancestor) and moves by transform, so the field never reflows for it; the
	  wrapper keeps the flow height while the dock is away from it.
	*/
	import { tick, untrack, type Snippet } from 'svelte';
	import AsyncField from './AsyncField.svelte';
	import Button from './Button.svelte';
	import CheckpointReview from './CheckpointReview.svelte';
	import EffortTicks from './EffortTicks.svelte';
	import Icon from './Icon.svelte';
	import IconButton from './IconButton.svelte';
	import Interventions from './Interventions.svelte';
	import Mark from './Mark.svelte';
	import MarkdownReader from './MarkdownReader.svelte';
	import PacketInspector from './PacketInspector.svelte';
	import SessionList from './SessionList.svelte';
	import StateMark from './StateMark.svelte';
	import Tabs, { panelId, tabId, type TabItem } from './Tabs.svelte';
	import {
		daemon,
		DaemonError,
		type Attempt,
		type AttemptPatch,
		type CheckpointReport,
		type Initiative,
		type Kitchen,
		type MemoryStatus,
		type Plan,
		type PlanGraph,
		type RecoveryAttempt,
		type Subtask
	} from './daemon';
	import { Resource } from './resource.svelte';
	import { currentBriefVersion } from './interventions';
	import { withEffort } from './dispatch';
	import { reviewOf } from './review';
	import { memberTone, TONE_WORD, type Tone } from './tones';
	import type { Member } from './field';

	let {
		planId,
		id,
		member,
		plan,
		graph,
		report,
		approved,
		activity,
		waiting = false,
		starting = false,
		failure,
		targetCheckpointId,
		staleAttempt,
		focusOnOpen,
		memoryStatus,
		kitchen,
		ondecided,
		onrecovery,
		historical = false,
		dtab = 'overview',
		ondtab,
		onstep,
		onlocate,
		mode = $bindable('docked')
	}: {
		planId: string;
		/** The initiative the dock was opened for; null shows the empty header. */
		id: string | null;
		/** That initiative's member in the current revision, or null once dropped. */
		member: Member | null;
		plan: Resource<Plan> | null;
		graph: PlanGraph;
		report: Resource<CheckpointReport> | null;
		approved: boolean;
		/** Runtime observations seen on the live stream since the page opened. */
		activity: { at: string; kind: string }[];
		waiting?: boolean;
		starting?: boolean;
		failure: string | null;
		targetCheckpointId: string | null;
		staleAttempt: RecoveryAttempt | null;
		focusOnOpen: boolean;
		memoryStatus: Resource<MemoryStatus> | null;
		kitchen: Resource<Kitchen> | null;
		ondecided: () => void;
		onrecovery: () => void;
		/** Accepted for the drawer's old contract; collapsing is `mode`, not closing. */
		onclose?: () => void;
		historical?: boolean;
		dtab?: string;
		ondtab?: (tab: string) => void;
		onstep?: (delta: 1 | -1) => void;
		onlocate?: () => void;
		mode?: DockMode;
	} = $props();

	const uid = $props.id();
	const TABS = ['overview', 'brief', 'review', 'diff', 'packet', 'attempts', 'activity'] as const;
	type TabKey = (typeof TABS)[number];

	/* --- tab: local copy of the deep-linked one, so the dock can switch tabs itself ---- */
	let tab = $state<TabKey>('overview');
	$effect(() => {
		const next = (TABS as readonly string[]).includes(dtab) ? (dtab as TabKey) : 'overview';
		untrack(() => (tab = next));
	});
	function setTab(next: string) {
		if (!(TABS as readonly string[]).includes(next)) return;
		tab = next as TabKey;
		ondtab?.(next);
	}

	/* --- reads ------------------------------------------------------------------ */
	const initiative = $derived<Initiative | null>(id && plan?.data ? (plan.data.initiatives[id] ?? null) : null);
	const attempts = $derived<Attempt[]>(initiative?.attempts ?? []);
	const pane = $derived([...attempts].reverse().find((a) => a.pane_ref)?.pane_ref ?? null);
	const view = $derived(historical ? null : reviewOf(report?.data ?? null, id));
	const versionCount = $derived(view?.versions.length ?? initiative?.checkpoint_versions.length ?? 0);
	const latestVersion = $derived(view?.versions.at(-1)?.version ?? (versionCount > 0 ? versionCount : null));
	const awaiting = $derived(view?.awaiting_review ? latestVersion : null);
	const needsYou = $derived(awaiting !== null || (waiting && !historical));
	const tone = $derived<Tone>(member ? memberTone(member.node, needsYou) : 'idle');
	const briefVersion = $derived(initiative ? currentBriefVersion(initiative) : 1);
	const name = $derived(member ? member.node.name : (id ?? ''));
	const shortModel = (model: string) => model.split('/').pop() ?? model;

	/* --- focus pane --------------------------------------------------------------- */
	type Focus = { phase: 'idle' | 'working' | 'done' | 'failed'; message: string };
	let focus = $state<Focus>({ phase: 'idle', message: '' });
	$effect(() => {
		void id;
		focus = { phase: 'idle', message: '' };
	});
	async function focusPane() {
		if (!id || historical) return;
		focus = { phase: 'working', message: '' };
		try {
			focus = { phase: 'done', message: (await daemon.focus(planId, id)).pane_ref };
		} catch (cause) {
			focus = {
				phase: 'failed',
				message: cause instanceof DaemonError ? cause.message : 'Something in this build failed while asking the daemon to focus.'
			};
		}
	}

	let copied = $state<string | null>(null);
	async function copy(value: string) {
		try {
			await navigator.clipboard.writeText(value);
			copied = value;
			setTimeout(() => copied === value && (copied = null), 1600);
		} catch {
			copied = null;
		}
	}

	/* --- what is in the way (the statement) ----------------------------------------- */
	type Stmt = { tone: Tone; title: string; text: string };
	function statement(m: Member): Stmt {
		const node = m.node;
		const last = initiative?.failures.at(-1);
		const harness = initiative?.assignment_override?.harness ?? initiative?.spec.assignment.harness ?? node.harness;
		if (m.cancelled) return { tone: 'cancelled', title: 'Cancelled', text: 'Struck out of the structure. It carries no load and nothing waits on it.' };
		if (node.state === 'failed') {
			const n = initiative?.failures.length ?? 0;
			return {
				tone: 'failed',
				title: n > 1 ? `Failed ${n} times` : 'This member failed',
				text: last?.reason ?? failure ?? 'No reason was recorded against this failure and none reached this page — unread, not causeless.'
			};
		}
		if (awaiting !== null) {
			const manifest = initiative?.checkpoint_versions.at(-1);
			const required = initiative?.spec.contract?.required_checks ?? [];
			const passing = (manifest?.checks ?? []).filter((c) => c.passed);
			const checks =
				required.length > 0
					? `${required.filter((r) => passing.some((c) => c.name === r)).length} of ${required.length} required checks pass`
					: manifest && manifest.checks.length > 0
						? `${passing.length} of ${manifest.checks.length} checks pass`
						: 'no checks were recorded';
			const caveats = manifest?.caveats.length ?? 0;
			return {
				tone: 'needs',
				title: `Checkpoint v${awaiting} waits for your review`,
				text: `${harness} submitted v${awaiting}${briefVersion > 1 ? ` after your redirect` : ''}. ${checks[0].toUpperCase()}${checks.slice(1)}${caveats > 0 ? `; ${caveats === 1 ? 'there is 1 caveat' : `there are ${caveats} caveats`} to read` : ''}.`
			};
		}
		if (node.state === 'paused') return { tone: 'paused', title: 'Held', text: 'It keeps its place and carries no load; no new attempt starts until it is released.' };
		if (node.state === 'settled') return { tone: 'settled', title: 'Settled', text: 'Its load transferred and its dependents were released.' };
		if (node.state === 'running' && starting) return { tone: 'starting', title: 'Starting', text: 'The agent was launched; the daemon confirms a turn began about 3 seconds after launch. If the prompt did not take, the attempt fails with that reason.' };
		if (node.state === 'running' && waiting) return { tone: 'needs', title: 'Needs input', text: 'The agent stopped at an approval, trust or login dialog and waits on you. Answer below or focus its pane.' };
		if (node.state === 'running') return { tone: 'running', title: 'Attempt under way', text: 'Nothing recorded as in the way. If the agent has asked you something it asked in its pane.' };
		if (!approved) return { tone: 'waiting', title: 'Plan not approved', text: 'No member may start until the revision is approved.' };
		if (m.blockedBy.length > 0) return { tone: 'waiting', title: `Waiting on ${m.blockedBy.join(', ')}`, text: 'Nothing starts here until every one of them has settled.' };
		return { tone: 'ready', title: 'Ready to run', text: 'Every dependency has settled; the daemon starts it when a lane is free.' };
	}
	const stmt = $derived(member ? statement(member) : null);
	const ruleLabel = $derived(stmt?.tone === 'needs' ? 'Waiting on you' : stmt?.tone === 'failed' ? 'In the way' : 'Status');

	/* --- assignment ------------------------------------------------------------------- */
	const effective = $derived(initiative ? (initiative.assignment_override ?? initiative.spec.assignment) : null);
	const roleName = $derived(initiative?.spec.contract?.role ?? null);
	const tier = $derived(
		effective && kitchen?.data ? (kitchen.data.models.find((m) => m.harness === effective.harness && m.model === effective.model)?.tier ?? null) : null
	);
	const ladder = $derived(effective && kitchen?.data ? (kitchen.data.effort_levels[`${effective.harness}/${effective.model}`] ?? undefined) : undefined);
	/** Where the pair came from; `null` when the Kitchen is unread (never guessed). */
	const resolved = $derived.by((): { text: string; tone?: 'violet' } | null => {
		if (!initiative || !effective) return null;
		if (initiative.assignment_override) return { text: 'reassigned by you' };
		const defaults = kitchen?.data?.defaults;
		if (!defaults) return null;
		const base = (roleName ? defaults.roles[roleName] : null) ?? defaults.initiative;
		if (!base) return null;
		const same = base.harness === effective.harness && base.model === effective.model;
		return same ? { text: `role default · ${roleName ?? 'initiative'}` } : { text: 'plan override', tone: 'violet' };
	});
	let iv = $state<{ openAction(action: 'reassign' | 'redirect'): void; status(action: 'reassign' | 'redirect'): { available: boolean; refused: string | null } } | null>(null);
	const reassign = $derived(iv?.status('reassign') ?? { available: false, refused: 'The folded plan has not answered, so what can be done is unread.' });
	let reviewer = $state<{ armVerdict(v: 'approve' | 'changes' | 'reject'): void } | null>(null);
	let packetInspector = $state<{ selectAttempt(attemptId: string): Promise<void> } | null>(null);

	async function verdict(v: 'approve' | 'changes') {
		setTab('review');
		await tick();
		await tick();
		reviewer?.armVerdict(v);
	}
	async function redirect() {
		setTab('overview');
		await tick();
		iv?.openAction('redirect');
	}

	const SUBTASK_TONE: Record<Subtask['state'], Tone> = { todo: 'waiting', doing: 'running', done: 'settled', skipped: 'cancelled' };
	const SUBTASK_WORD: Record<Subtask['state'], string> = { todo: 'Not started', doing: 'Doing', done: 'Done', skipped: 'Skipped' };

	/* --- brief ---------------------------------------------------------------------- */
	const when = (value: string | null): string => {
		if (!value) return '—';
		const at = new Date(value);
		return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
	};
	const clock = (value: string): string => {
		const at = new Date(value);
		return Number.isNaN(at.getTime()) ? value : at.toLocaleTimeString([], { hour12: false });
	};
	const currentBrief = $derived(initiative ? (initiative.brief_versions.at(-1)?.brief ?? initiative.spec.brief) : '');
	const previousBrief = $derived(
		initiative && initiative.brief_versions.length > 0
			? (initiative.brief_versions.length > 1 ? initiative.brief_versions[initiative.brief_versions.length - 2].brief : initiative.spec.brief)
			: null
	);
	const briefSource = $derived.by(() => {
		if (!initiative) return '';
		const parts = [currentBrief.trim()];
		if (initiative.subtasks.length > 0)
			parts.push('## Subtasks\n\n' + initiative.subtasks.map((s) => `- ${s.brief} — ${SUBTASK_WORD[s.state].toLowerCase()}`).join('\n'));
		if (initiative.brief_versions.length > 0)
			parts.push(
				'## Redirects\n\n' +
					initiative.brief_versions
						.map((v) => `> **v${v.version} · ${v.by} · ${when(v.at)}**\n>\n> ${v.reason || 'No reason was recorded with this redirect.'}`)
						.join('\n\n')
			);
		return parts.join('\n\n');
	});
	let compareBrief = $state(false);
	$effect(() => {
		void id;
		compareBrief = false;
	});

	/* --- diff (B1 gate: the client may lack attemptPatch; the route may 404) ------------- */
	const patchable = typeof (daemon as { attemptPatch?: unknown }).attemptPatch === 'function';
	const withCheckpoint = $derived(attempts.filter((a) => a.checkpoint));
	let diffAttemptId = $state<string | null>(null);
	$effect(() => {
		void id;
		diffAttemptId = null;
	});
	const diffAttempt = $derived(
		withCheckpoint.find((a) => a.id === diffAttemptId) ?? withCheckpoint.at(-1) ?? null
	);
	let patch = $state<Resource<AttemptPatch> | null>(null);
	let patchKey = '';
	$effect(() => {
		const key = tab === 'diff' && patchable && diffAttempt ? `${planId}/${diffAttempt.id}` : '';
		if (key === patchKey) return;
		patchKey = key;
		const previous = untrack(() => patch);
		patch = key && diffAttempt ? new Resource<AttemptPatch>((signal) => daemon.attemptPatch(planId, diffAttempt.id, signal)) : null;
		previous?.dispose();
		if (patch) void patch.load();
	});
	let diffFile = $state<string | null>(null);
	$effect(() => {
		void diffAttempt?.id;
		diffFile = null;
	});
	const sections = $derived(patch?.data ? splitPatch(patch.data.text) : []);
	const shown = $derived(
		diffFile ? sections.filter((s) => s.path === diffFile) : sections
	);
	const patchTotals = $derived(
		patch?.data && patch.data.files.length > 0
			? { a: patch.data.files.reduce((n, f) => n + f.added, 0), d: patch.data.files.reduce((n, f) => n + f.deleted, 0) }
			: null
	);
	/* A daemon without the route answers its SPA fallback (200 + HTML → bad_response) or 404. */
	const patchGone = $derived(
		patch?.error instanceof DaemonError && !patch.data && (patch.error.kind === 'not_found' || patch.error.kind === 'bad_response' || patch.error.status === 404 || patch.error.status === 405)
	);

	/* --- attempts ------------------------------------------------------------------- */
	function outcome(a: Attempt): { tone: Tone | 'pass'; word: string } {
		if (!a.checkpoint) return a.ended_at ? { tone: 'settled', word: 'Ended' } : { tone: 'running', word: 'In progress' };
		if (a.checkpoint.exit_code === null) return { tone: 'needs', word: 'Checkpoint' };
		return a.checkpoint.exit_code === 0 ? { tone: 'pass', word: 'Exit 0' } : { tone: 'failed', word: `Exit ${a.checkpoint.exit_code}` };
	}
	function ranFor(a: Attempt): string {
		if (!a.ended_at) return '—';
		const s = Math.round((new Date(a.ended_at).getTime() - new Date(a.started_at).getTime()) / 1000);
		if (Number.isNaN(s) || s < 0) return '—';
		if (s < 60) return `${s}s`;
		const m = Math.floor(s / 60);
		return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
	}
	const tokensOf = (a: Attempt): string =>
		a.checkpoint?.usage ? (a.checkpoint.usage.input_tokens + a.checkpoint.usage.output_tokens).toLocaleString() : a.packet_tokens > 0 ? a.packet_tokens.toLocaleString() : '—';
	function seeDiff(a: Attempt) {
		diffAttemptId = a.id;
		setTab('diff');
	}
	async function seePacket(a: Attempt) {
		setTab('packet');
		await tick();
		await tick();
		void packetInspector?.selectAttempt(a.id);
	}

	/* --- activity --------------------------------------------------------------------- */
	const log = $derived(activity.slice(-ACTIVITY_CAP));

	/* --- deep-linked checkpoint -------------------------------------------------------- */
	const checkpointKnown = $derived(
		!!targetCheckpointId &&
			(report?.data?.initiatives ?? []).some((v) => v.initiative_id === id && v.versions.some((x) => x.checkpoint_id === targetCheckpointId))
	);
	const checkpointCurrent = $derived(
		!!targetCheckpointId &&
			(report?.data?.initiatives ?? []).some((v) => v.initiative_id === id && v.versions.at(-1)?.checkpoint_id === targetCheckpointId)
	);
	let addressed = $state<string | null>(null);
	$effect(() => {
		const target = targetCheckpointId;
		if (!target) {
			addressed = null;
			return;
		}
		if (target === untrack(() => addressed) || !checkpointCurrent) return;
		addressed = target;
		untrack(() => setTab('review'));
	});

	/* --- tabs ---------------------------------------------------------------------------- */
	const items = $derived<TabItem[]>([
		{ id: 'overview', label: 'Overview' },
		{ id: 'brief', label: 'Brief', count: briefVersion > 1 ? `v${briefVersion}` : undefined },
		{ id: 'review', label: 'Review', count: latestVersion !== null ? `v${latestVersion}` : undefined, countTone: awaiting !== null ? 'nd' : undefined },
		{ id: 'diff', label: 'Diff', count: patchTotals ? `+${patchTotals.a} −${patchTotals.d}` : undefined },
		{ id: 'packet', label: 'Packet' },
		{ id: 'attempts', label: 'Attempts', count: attempts.length > 0 ? attempts.length : undefined },
		{ id: 'activity', label: 'Activity' }
	]);

	/* --- geometry --------------------------------------------------------------------------- */
	const readHeight = (): number => {
		try {
			const n = Number(localStorage.getItem(HEIGHT_KEY));
			return Number.isFinite(n) && n >= SNAP_COLLAPSE ? n : DEFAULT_H;
		} catch {
			return DEFAULT_H;
		}
	};
	let h = $state(readHeight());
	let dragging = $state(false);
	let wrap = $state<HTMLElement | null>(null);
	let dock = $state<HTMLElement | null>(null);
	const eff = $derived<DockMode>(id === null ? 'collapsed' : mode);
	const collapsed = $derived(eff === 'collapsed');
	const maxed = $derived(eff === 'max');
	/* The flow box holds the docked/collapsed height while the dock is maximized. */
	let flowH = $state(HEADER);
	$effect(() => {
		if (eff !== 'max') flowH = eff === 'collapsed' ? HEADER : h;
	});
	const persist = () => {
		try {
			localStorage.setItem(HEIGHT_KEY, String(Math.round(h)));
		} catch {
			/* a private window: the height just is not remembered */
		}
	};
	const region = (): number => (wrap?.offsetParent as HTMLElement | null)?.clientHeight ?? innerHeight;

	let before = '';
	let grip = 0;
	function dragStart(event: PointerEvent) {
		if (event.button !== 0 || maxed) return;
		try { (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
		grip = h;
		dragging = true;
		if (mode === 'collapsed') h = HEADER;
		mode = 'docked';
	}
	function dragMove(event: PointerEvent) {
		if (!dragging || !wrap) return;
		const parent = wrap.offsetParent as HTMLElement | null;
		const bottom = parent ? parent.getBoundingClientRect().bottom : innerHeight;
		h = Math.max(HEADER, Math.min(region(), bottom - event.clientY));
	}
	function dragEnd(event: PointerEvent) {
		if (!dragging) return;
		dragging = false;
		(event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
		if (h < SNAP_COLLAPSE) {
			h = grip >= SNAP_COLLAPSE ? grip : DEFAULT_H;
			mode = 'collapsed';
		} else if (h > region() - SNAP_MAX) {
			h = grip >= SNAP_COLLAPSE ? grip : DEFAULT_H;
			mode = 'max';
		} else persist();
	}
	function gripKey(event: KeyboardEvent) {
		const step = event.key === 'ArrowUp' ? 24 : event.key === 'ArrowDown' ? -24 : 0;
		if (!step) return;
		event.preventDefault();
		const next = h + step;
		if (next < SNAP_COLLAPSE) mode = 'collapsed';
		else {
			h = Math.min(next, region());
			mode = 'docked';
			persist();
		}
	}
	const toggleMax = () => (mode = mode === 'max' ? 'docked' : 'max');

	/* Maximize/restore move by transform, clipped to the box so nothing hangs below the region. */
	let lastEff: DockMode = 'docked';
	$effect(() => {
		const now = eff;
		const was = untrack(() => lastEff);
		if (now === was) return;
		lastEff = now;
		if (!dock || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		const h1 = dock.offsetHeight;
		const timing = { duration: 260, easing: 'cubic-bezier(.2,.7,.2,1)' };
		if (now === 'max') {
			const dy = h1 - (was === 'collapsed' ? HEADER : untrack(() => h));
			dock.animate([{ transform: `translateY(${dy}px)`, clipPath: `inset(0 0 ${dy}px 0)` }, { transform: 'none', clipPath: 'inset(0)' }], timing);
		} else if (was === 'max') {
			const dy = region() - h1;
			dock.animate([{ transform: `translateY(${-dy}px)` }, { transform: 'none' }], timing);
		}
	});

	/* --- keys: never while typing or with modifiers ------------------------------------------- */
	function keydown(event: KeyboardEvent) {
		if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
		const t = event.target as HTMLElement | null;
		if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
		if (document.querySelector('dialog[open], [role="dialog"]')) return;
		const key = event.key.toLowerCase();
		if (event.key === 'Escape') {
			if (id === null) return;
			if (mode === 'max') mode = 'docked';
			else if (mode === 'docked') mode = 'collapsed';
			else return;
			event.preventDefault();
		} else if (key === 'm' && id !== null) {
			toggleMax();
			event.preventDefault();
		} else if (key === 'j' || key === 'k') {
			if (mode === 'collapsed') mode = 'docked';
			onstep?.(key === 'j' ? 1 : -1);
			event.preventDefault();
		} else if (key === 'l' && id !== null) {
			onlocate?.();
			event.preventDefault();
		}
	}

	/* Arrival focus: claimed only when the address opened the dock, never a click. */
	let nameEl = $state<HTMLHeadingElement | null>(null);
	$effect(() => {
		if (id && focusOnOpen) void tick().then(() => nameEl?.focus());
	});
	const announce = $derived(id ? `${name} selected` : '');
</script>

<svelte:window onkeydown={keydown} />

{#snippet statementBlock()}
	{#if stmt}
		<div class="rule-h"><span class="lbl">{ruleLabel}</span></div>
		<div class="stmt" data-tone={stmt.tone}>
			<b>{stmt.title}</b>
			<p>{stmt.text}</p>
		</div>
		{#if staleAttempt}
			<p class="line">
				<span class="state" data-tone="waiting">Stale</span>
				<span class="dk-note">Attempt <code>{staleAttempt.attempt_id}</code> is no longer owned by this daemon; whether its pane is alive is unknown.</span>
				<Button icon="life-buoy" small onclick={onrecovery}>Recovery</Button>
			</p>
		{/if}
		{#if focus.phase !== 'idle' && !historical}
			{#if focus.phase === 'working'}<p class="state" data-tone="running" role="status">Focusing pane…</p>
			{:else if focus.phase === 'done'}<p class="state" data-tone="pass" role="status">herdr focused {focus.message}</p>
			{:else}<p role="alert"><span class="state" data-tone="failed">Not focused</span> <span class="dk-note">{focus.message}</span></p>{/if}
		{/if}
	{/if}
{/snippet}

{#snippet planned(body: Snippet)}
	{#if plan}
		<AsyncField resource={plan} reading="this initiative" onretry={() => void plan?.load()}>
			{#snippet children()}
				{#if initiative}{@render body()}
				{:else}<p class="dk-note">{id} is not in this revision of the plan; the field and the plan were read a moment apart.</p>{/if}
			{/snippet}
		</AsyncField>
	{:else}
		<p class="dk-note">No plan is addressed, so there is nothing to read this initiative from.</p>
	{/if}
{/snippet}

{#snippet briefPane()}
	{#if initiative}
		<div class="brief" class:two={compareBrief && previousBrief !== null}>
			<MarkdownReader source={briefSource} toc compact>
				{#snippet toolbar()}
					<span class="lbl">Brief{briefVersion > 1 ? ` · v${briefVersion}` : ' · as planned'}</span>
					<span class="ibar">
						<IconButton small icon="copy" label="Copy markdown" onclick={() => void copy(currentBrief)} />
						<IconButton small icon="git-compare" label="Compare with previous version" pressed={compareBrief} disabled={previousBrief === null}
							reason="No redirect has been recorded, so there is no earlier version." onclick={() => (compareBrief = !compareBrief)} />
						<IconButton small icon="route" label="Redirect" disabled={historical || !member} onclick={() => void redirect()} />
						<IconButton small icon={maxed ? 'minimize-2' : 'maximize-2'} label={maxed ? 'Restore dock' : 'Maximize dock'} shortcut="M" onclick={toggleMax} />
					</span>
					{#if copied === currentBrief}<span class="state" data-tone="pass" role="status">Copied</span>{/if}
				{/snippet}
			</MarkdownReader>
			{#if compareBrief && previousBrief !== null}
				<div class="prev">
					<div class="rule-h"><span class="lbl">Previous · v{briefVersion - 1}</span></div>
					<MarkdownReader source={previousBrief} compact />
				</div>
			{/if}
		</div>
	{/if}
{/snippet}

{#snippet diffPane()}
	{#if withCheckpoint.length === 0}
		<p class="dk-note">No attempt has recorded a checkpoint yet, so there is no diff to read.</p>
	{:else if diffAttempt}
		{@const cp = diffAttempt.checkpoint}
		<div class="diff">
			<div class="files">
				<div class="rule-h">
					<span class="lbl">Changed</span>
					<span class="r">{#if patchTotals}<span class="diffstat"><span class="a">+{patchTotals.a}</span><span class="d">−{patchTotals.d}</span></span>{:else}<span class="count">{cp?.changed_paths.length ?? 0}</span>{/if}</span>
				</div>
				{#if withCheckpoint.length > 1}
					<select class="sel" aria-label="Attempt" bind:value={diffAttemptId}>
						{#each withCheckpoint as a (a.id)}<option value={a.id}>Attempt {attempts.indexOf(a) + 1} · {a.origin === 'retry' ? 'retry' : 'run'}</option>{/each}
					</select>
				{/if}
				{#if patch?.data}
					<table class="tbl">
						<tbody>
							<tr class="click" aria-current={diffFile === null ? 'true' : undefined} onclick={() => (diffFile = null)}>
								<td class="ell">All files</td><td class="n">{patch.data.files.length}</td>
							</tr>
							{#each patch.data.files as file (file.path)}
								<tr class="click" aria-current={diffFile === file.path ? 'true' : undefined} onclick={() => (diffFile = file.path)}>
									<td class="ell"><code>{file.path}</code></td>
									<td class="n"><span class="diffstat"><span class="a">+{file.added}</span><span class="d">−{file.deleted}</span></span></td>
								</tr>
							{/each}
						</tbody>
					</table>
				{:else}
					<ul class="dk-list">
						{#each cp?.changed_paths ?? [] as path (path)}<li><code>{path}</code></li>{/each}
						{#if (cp?.changed_paths.length ?? 0) === 0}<li class="muted">No changed path recorded.</li>{/if}
					</ul>
				{/if}
			</div>
			<div class="hunks">
				{#if !patchable || patchGone}
					<div class="rule-h"><span class="lbl">Patch</span></div>
					<p class="dk-note">This daemon serves no route that returns a patch, so the changes themselves cannot be shown — only what changed and where the patch is stored.</p>
					{#if cp?.patch_path}
						<p class="line"><code>{cp.patch_path}</code> <IconButton small icon="copy" label="Copy patch path" onclick={() => void copy(cp.patch_path ?? '')} />
							{#if copied === cp.patch_path}<span class="state" data-tone="pass" role="status">Copied</span>{/if}</p>
					{:else}
						<p class="dk-note">This checkpoint recorded no patch.</p>
					{/if}
				{:else if patch}
					<AsyncField resource={patch} reading="the patch" onretry={() => void patch?.load()}>
						{#snippet children(data: AttemptPatch)}
							{#if data.truncated}<p class="dk-note"><span class="state" data-tone="waiting">Truncated</span> The patch is capped; the rest is in <code>{data.path}</code>.</p>{/if}
							{#if data.text.trim() === ''}
								<p class="dk-note">The patch is empty — nothing was changed.</p>
							{:else}
								<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
								<pre class="patch" tabindex="0" aria-label="Unified diff">{#each shown as section (section.path)}{#each section.lines as line, at (at)}<span class="ln" data-k={lineKind(line)}>{line}
</span>{/each}{/each}</pre>
							{/if}
						{/snippet}
					</AsyncField>
				{/if}
			</div>
		</div>
	{/if}
{/snippet}

{#snippet attemptsPane()}
	{#if initiative}
		{@const ceiling = initiative.spec.policy.max_attempts}
		{#if attempts.length === 0}
			<p class="dk-note">None of {ceiling.toLocaleString()} started — no worktree, pane or usage to read.</p>
		{:else}
			<table class="tbl">
				<thead>
					<tr><th>#</th><th>Ran as</th><th>Brief</th><th>Worktree</th><th>Started</th><th class="n">Ran for</th><th class="n">Tokens</th><th>Outcome</th><th><span class="sr-only">Actions</span></th></tr>
				</thead>
				<tbody>
					{#each attempts as a, at (a.id)}
						{@const out = outcome(a)}
						<tr>
							<td class="mono">{at + 1}{a.origin === 'retry' ? ' · retry' : ''}</td>
							<td><span class="cell"><Mark harness={a.assignment.harness} size={14} />{a.assignment.harness}<Icon name="chevron-right" size={12} /><Mark model={a.assignment.model} size={14} /><span class="mono">{shortModel(a.assignment.model)}</span><EffortTicks effort={a.assignment.effort} /></span></td>
							<td class="mono">v{a.brief_version}</td>
							<td class="mono ell wt" title={a.worktree_ref ?? undefined}>{a.worktree_ref ?? '—'}</td>
							<td class="mono">{when(a.started_at)}</td>
							<td class="n">{ranFor(a)}</td>
							<td class="n">{tokensOf(a)}</td>
							<td><StateMark tone={out.tone} word={out.word} /></td>
							<td>
								<span class="ibar">
									<IconButton small icon="git-compare" label="Diff" disabled={!a.checkpoint} reason="This attempt recorded no checkpoint." onclick={() => seeDiff(a)} />
									{#if a.packet_snapshot}<IconButton small icon="eye" label="Read this packet" onclick={() => void seePacket(a)} />{/if}
									<IconButton small icon="copy" label="Copy worktree path" disabled={!a.worktree_ref} reason="This attempt recorded no worktree." onclick={() => void copy(a.worktree_ref ?? '')} />
								</span>
							</td>
						</tr>
						{#if (a.sessions ?? []).length > 0}
							<tr><td></td><td colspan="8"><SessionList sessions={a.sessions ?? []} label="Sessions for attempt {a.id}" /></td></tr>
						{/if}
					{/each}
				</tbody>
			</table>
		{/if}
		<p class="line foot">
			<span class="count">{attempts.length} of {ceiling}</span>
			{#if initiative.assignment_override}<span class="dk-note">The next attempt runs on {withEffort(`${initiative.assignment_override.harness}/${initiative.assignment_override.model}`, initiative.assignment_override.effort)}, not the planner's {withEffort(`${initiative.spec.assignment.harness}/${initiative.spec.assignment.model}`, initiative.spec.assignment.effort)}.</span>{/if}
			{#if attempts.length >= ceiling}<span class="dk-note">All attempts used; no retry remains.</span>{/if}
		</p>
		{#if initiative.failures.length > 0}
			<div class="rule-h sp"><span class="lbl">Failures</span><span class="r"><span class="count bad">{initiative.failures.length}</span></span></div>
			<ul class="chain">
				{#each initiative.failures as f, at (at)}
					<li>
						<span class="dot" data-tone="failed"></span>
						<span class="ell">{f.reason}{#if f.evidence.length > 0}<span class="why">{#each f.evidence as path (path)}<code>{path}</code> {/each}</span>{:else}<span class="why">No evidence was preserved with it.</span>{/if}</span>
						<span class="state" data-tone="failed">Failed</span>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
{/snippet}

<div class="dockwrap" bind:this={wrap} style:height="{flowH}px" style:--flow-h="{flowH}px" class:dragging style:grid-column="1 / -1">
	<section
		class="dock"
		class:max={maxed}
		class:collapsed
		bind:this={dock}
		aria-labelledby={id ? `${uid}-name` : undefined}
		aria-label={id ? undefined : 'Member detail'}
		aria-busy={dragging || undefined}
	>
		{#if !maxed}
			<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
			<div
				class="grip"
				role="separator"
				aria-orientation="horizontal"
				aria-label="Resize dock"
				aria-valuemin={HEADER}
				aria-valuenow={Math.round(collapsed ? HEADER : h)}
				tabindex="0"
				title="Drag to resize · double-click to maximize"
				onpointerdown={dragStart}
				onpointermove={dragMove}
				onpointerup={dragEnd}
				onpointercancel={dragEnd}
				ondblclick={toggleMax}
				onkeydown={gripKey}
			></div>
		{/if}

		<header class="head">
			{#if id === null}
				<span class="who"><span class="lbl idle" id="{uid}-name">No member selected — click one in the field or press J.</span></span>
			{:else}
				<span class="who">
					<StateMark {tone} word={member?.cancelled ? 'Cancelled' : TONE_WORD[tone]} />
					<span class="mono muted">{id}{historical ? ' · historical' : ''}</span>
					<h2 class="h-sec" id="{uid}-name" tabindex="-1" bind:this={nameEl}>{name}</h2>
					{#if effective}
						<span class="assign mono muted"><Mark harness={effective.harness} size={14} />{effective.harness}<Icon name="chevron-right" size={12} /><Mark model={effective.model} size={14} />{shortModel(effective.model)}</span>
					{/if}
				</span>
			{/if}
			<span class="ibar">
				<IconButton icon="chevron-up" label="Previous" shortcut="K" onclick={() => { if (mode === 'collapsed') mode = 'docked'; onstep?.(-1); }} />
				<IconButton icon="chevron-down" label="Next" shortcut="J" onclick={() => { if (mode === 'collapsed') mode = 'docked'; onstep?.(1); }} />
				<span class="sep"></span>
				<IconButton icon="crosshair" label="Locate in field" shortcut="L" disabled={id === null} onclick={() => onlocate?.()} />
				<IconButton icon="square-terminal" label="Focus pane" disabled={id === null || !pane || historical} reason={historical ? 'Historical replay is read-only.' : 'No attempt recorded a pane.'} onclick={() => void focusPane()} />
				<IconButton icon="copy" label="Copy id" disabled={id === null} onclick={() => id && void copy(id)} />
				<span class="sep"></span>
				<IconButton icon={maxed ? 'minimize-2' : 'maximize-2'} label={maxed ? 'Restore' : 'Maximize'} shortcut="M" disabled={id === null} onclick={toggleMax} />
				{#if collapsed}
					<IconButton icon="chevron-up" label="Open dock" disabled={id === null} onclick={() => (mode = 'docked')} />
				{:else}
					<IconButton icon="x" label="Collapse" shortcut="Esc" onclick={() => (mode = 'collapsed')} />
				{/if}
			</span>
			{#if copied === id && id}<span class="state copied" data-tone="pass" role="status">Copied</span>{/if}
		</header>

		<div class="tabs">
			<Tabs {items} selected={tab} prefix="dtab" label="Member detail" small onselect={setTab} />
		</div>

		<div class="dk-body">
			{#if id !== null}
				{#if !member}
					<p class="dk-note">{id} is no longer in this revision of the plan; a live re-read replaced the graph, so there is nothing left to read.</p>
				{:else}
					<div class="pane" role="tabpanel" id={panelId('dtab', 'overview')} aria-labelledby={tabId('dtab', 'overview')} hidden={tab !== 'overview'}>
						<div class="cols">
							<div class="col">
								<Interventions
									bind:this={iv}
									{historical}
									{planId}
									{id}
									{initiative}
									plan={plan?.data ?? null}
									{memoryStatus}
									{approved}
									{awaiting}
									asking={waiting && !historical}
									ready={member.node.ready}
									{pane}
									onfocus={() => void focusPane()}
									ontab={setTab}
									onverdict={(v) => void verdict(v)}
									statement={statementBlock}
									onchanged={ondecided}
									onreview={() => setTab('review')}
								/>
							</div>
							<div class="col">
								<div class="rule-h">
									<span class="lbl">Assignment</span>
									<span class="r"><IconButton small icon="pencil" label="Change for next attempt" disabled={historical || !reassign.available} reason={historical ? 'Historical replay is read-only.' : (reassign.refused ?? undefined)} onclick={() => iv?.openAction('reassign')} /></span>
								</div>
								{#if effective}
									<dl class="kv">
										<dt>Harness</dt><dd><Mark harness={effective.harness} size={16} />{effective.harness}</dd>
										<dt>Model</dt><dd class="ell"><Mark model={effective.model} size={16} /><span class="mono">{effective.model}</span></dd>
										<dt>Effort</dt><dd><EffortTicks effort={effective.effort} {ladder} /><span>{effective.effort ?? '—'}</span></dd>
										<dt>Tier</dt><dd>{tier ?? '—'}</dd>
										<dt>Resolved</dt><dd class:violet={resolved?.tone === 'violet'}>{resolved?.text ?? '—'}</dd>
									</dl>
								{:else}
									<p class="dk-note">The folded plan has not answered, so the assignment is unread.</p>
								{/if}
							</div>
							<div class="col">
								{#if initiative}
									{@const done = initiative.subtasks.filter((s) => s.state === 'done').length}
									{@const contract = initiative.spec.contract}
									<div class="rule-h"><span class="lbl">Subtasks</span><span class="r"><span class="count">{initiative.subtasks.length === 0 ? '—' : `${done} of ${initiative.subtasks.length}`}</span></span></div>
									{#if initiative.subtasks.length === 0}
										<p class="dk-note">None declared for this initiative.</p>
									{:else}
										<ul class="chain">
											{#each initiative.subtasks as sub (sub.id)}
												<li><span class="dot" data-tone={SUBTASK_TONE[sub.state]}></span><span class="ell">{sub.brief}</span><span class="state" data-tone={SUBTASK_TONE[sub.state]}>{SUBTASK_WORD[sub.state]}</span></li>
											{/each}
										</ul>
									{/if}
									<div class="rule-h sp"><span class="lbl">Contract</span><span class="r"><span class="count">{contract ? contract.role : 'none'}</span></span></div>
									<dl class="kv">
										<dt>Writes</dt>
										<dd class="paths">{#if contract && !contract.allow_writes}<span class="state" data-tone="failed">No writes</span>{:else if initiative.spec.routes.writes.length === 0}—{:else}{#each initiative.spec.routes.writes as path (path)}<code>{path}</code>{/each}{/if}</dd>
										<dt>Checks</dt>
										<dd class="paths">{#if !contract || contract.required_checks.length === 0}—{:else}{#each contract.required_checks as check (check)}<code>{check}</code>{/each}{/if}</dd>
									</dl>
								{:else}
									<div class="rule-h"><span class="lbl">Subtasks</span></div>
									<p class="dk-note">Unread until the folded plan answers.</p>
								{/if}
							</div>
						</div>
					</div>

					{#key tab}
						{#if tab === 'brief'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'brief')} aria-labelledby={tabId('dtab', 'brief')}>{@render planned(briefPane)}</div>
						{:else if tab === 'review'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'review')} aria-labelledby={tabId('dtab', 'review')}>
								{#if targetCheckpointId && report?.data && !checkpointKnown}
									<p class="dk-note"><code>{targetCheckpointId}</code> is not recorded against this member in the checkpoint report; the member's own versions are below.</p>
								{:else if targetCheckpointId && report?.data && !checkpointCurrent}
									<p class="dk-note"><code>{targetCheckpointId}</code> is an earlier version of this member; the current version is shown, earlier ones are listed.</p>
								{/if}
								<CheckpointReview
									bind:this={reviewer}
									{historical}
									{planId}
									{id}
									{initiative}
									{graph}
									{report}
									expanded={maxed}
									onexpand={(next) => (mode = next ? 'max' : 'docked')}
									oncompare={() => setTab('diff')}
									{ondecided}
								/>
							</div>
						{:else if tab === 'diff'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'diff')} aria-labelledby={tabId('dtab', 'diff')}>{@render planned(diffPane)}</div>
						{:else if tab === 'packet'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'packet')} aria-labelledby={tabId('dtab', 'packet')}>
								{#snippet packet()}
									<PacketInspector
										bind:this={packetInspector}
										{historical}
										{planId}
										{id}
										{initiative}
										plan={plan?.data ?? null}
										expanded={maxed}
										onexpand={(next) => (mode = next ? 'max' : 'docked')}
										{memoryStatus}
										{kitchen}
									/>
								{/snippet}
								{@render planned(packet)}
							</div>
						{:else if tab === 'attempts'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'attempts')} aria-labelledby={tabId('dtab', 'attempts')}>{@render planned(attemptsPane)}</div>
						{:else if tab === 'activity'}
							<div class="pane dk-pane" role="tabpanel" id={panelId('dtab', 'activity')} aria-labelledby={tabId('dtab', 'activity')}>
								{#if historical || log.length === 0}
									<p class="dk-note">{historical ? 'Activity is not replayed.' : 'Nothing has reached this page since it opened — earlier activity is unread, not absent.'}</p>
								{:else}
									<ul class="log" aria-label="Activity for {name}">
										{#each log as event (event.at + event.kind)}
											<li class:needs={NEEDS_LINE.test(event.kind)}><time datetime={event.at}>{clock(event.at)}</time><span>{event.kind}</span></li>
										{/each}
									</ul>
								{/if}
							</div>
						{/if}
					{/key}
				{/if}
			{/if}
		</div>
	</section>
	<div class="sr-only" role="status" aria-live="polite">{announce}</div>
</div>

<style>
	/* Height changes snap (DS §12: only the sidebar and needs-you column animate size);
	   maximize/restore animates with transform from the docked box. */
	.dockwrap { position: static; min-height: 0; }
	.dockwrap.dragging { user-select: none; }
	.dock {
		position: relative; height: 100%; display: grid; grid-template-rows: auto auto minmax(0, 1fr);
		background: var(--p1); border-top: 1px solid var(--ln2); overflow: hidden;
	}
	.dock.max { position: absolute; inset: 0; height: auto; z-index: 20; border-top: 0; animation: dock-max var(--t-dock) var(--ease); }
	@keyframes dock-max { from { transform: translateY(calc(100% - var(--flow-h, 340px))); } }
	.grip { position: absolute; top: -4px; left: 0; right: 0; height: 10px; cursor: ns-resize; z-index: 2; touch-action: none; }
	.grip::after {
		content: ''; position: absolute; left: 50%; top: 7px; width: 28px; height: 1px; margin-left: -14px; background: var(--ln2);
		transition: background var(--t-fast), width var(--t-fast), margin var(--t-fast);
	}
	.grip:hover::after, .grip:focus-visible::after, .dockwrap.dragging .grip::after { background: var(--tx); width: 44px; margin-left: -22px; }
	.grip:focus-visible { outline-offset: -2px; }

	.head { display: flex; align-items: center; gap: 14px; padding: 0 12px 0 24px; height: 48px; border-bottom: 1px solid var(--ln); min-width: 0; }
	.who { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }
	.who h2 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
	.who h2:focus { outline: none; }
	.assign { display: inline-flex; align-items: center; gap: 6px; flex: none; }
	.idle { color: var(--dim); }
	.copied { flex: none; }
	/* collapsed is header only: the rest leaves the tab order but keeps its state */
	.dock.collapsed .tabs, .dock.collapsed .dk-body { visibility: hidden; }
	.tabs { padding: 0 24px; border-bottom: 1px solid var(--ln); }
	.dk-body { overflow: auto; padding: 18px 24px 22px; min-height: 0; }
	.dock.max .dk-body { padding: 22px 32px 40px; }
	.pane[hidden] { display: none; }
	.dk-pane { animation: pane var(--t-pane) var(--ease); }

	.cols { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1fr); gap: 32px; align-items: start; }
	.col { min-width: 0; }
	.sp { margin-top: 20px; }
	.kv .violet { color: var(--l405); }
	.kv dd.paths { flex-wrap: wrap; }
	.ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
	.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
	.foot { margin-top: 12px; }
	.why { display: block; font: 400 12px/1.4 var(--f-ui); color: var(--dim); white-space: normal; overflow-wrap: anywhere; }
	.chain .ell { white-space: normal; }

	.brief { height: 100%; }
	.brief.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 32px; }
	.prev { min-width: 0; }

	.diff { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(0, 3fr); gap: 32px; align-items: start; }
	.files { display: grid; gap: 12px; min-width: 0; }
	.files .rule-h { margin-bottom: 0; }
	.files :global(.tbl td) { padding: 6px 10px; }
	.files .tbl .ell { max-width: 0; width: 100%; }
	.hunks { min-width: 0; }
	.patch {
		font: 400 12px/1.7 var(--f-mono); overflow: auto; border: 1px solid var(--ln); background: var(--bg); padding: 8px 0; white-space: pre; tab-size: 4;
		max-height: 100%;
	}
	.ln { display: block; padding: 0 12px; color: var(--tx2); }
	.ln[data-k='add'] { color: var(--l546); background: color-mix(in srgb, var(--l546) 8%, transparent); }
	.ln[data-k='del'] { color: var(--l656); background: color-mix(in srgb, var(--l656) 8%, transparent); }
	.ln[data-k='hunk'], .ln[data-k='meta'] { color: var(--dim); }

	.tbl .wt { max-width: 200px; }
	.log { font: 400 12px/1.7 var(--f-mono); }
	.log li { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 12px; color: var(--tx2); }
	.log time { color: var(--dim); }
	.log li.needs span { color: var(--l589); }

	/* the shared dock-pane vocabulary the three panes and the forms use */
	.dock :global(.dk-note) { font: 400 12.5px/1.5 var(--f-ui); color: var(--dim); max-width: 72ch; }
	.dock :global(.dk-note strong) { color: var(--tx2); font-weight: 500; }
	.dock :global(.dk-list) { display: grid; gap: 3px; font: 400 12px/1.5 var(--f-mono); color: var(--tx2); }
	.dock :global(.dk-list li) { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; overflow-wrap: anywhere; }
	.dock :global(.state[data-tone='pass']::before) { background: var(--l546); }
	/* in a chain the state is a right-aligned word; the line is the dot */
	.dock :global(.chain .state::before) { display: none; }
	.dock :global(.rule-h .r) { margin: -5px 0; display: inline-flex; align-items: center; }


	/* app.css sets --sc on .stmt/.dot after the tone rules; restate the tone so the rail and dot take it */
	.dock :global(:is(.stmt, .chain .dot)[data-tone='running']) { --sc: var(--state-running); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='needs']) { --sc: var(--state-needs); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='failed']) { --sc: var(--state-failed); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='ready']) { --sc: var(--state-ready); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='waiting']) { --sc: var(--state-waiting); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='settled']) { --sc: var(--state-settled); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='paused']) { --sc: var(--state-paused); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='cancelled']) { --sc: var(--state-cancelled); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='starting']) { --sc: var(--dim); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='idle']) { --sc: var(--dim); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='pass']) { --sc: var(--l546); }
	.dock :global(:is(.stmt, .chain .dot)[data-tone='violet']) { --sc: var(--l405); }

	@media (max-width: 1100px) {
		.cols, .diff { grid-template-columns: minmax(0, 1fr); gap: 24px; }
		.brief.two { grid-template-columns: minmax(0, 1fr); }
	}
</style>
