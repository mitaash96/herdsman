<script lang="ts">
	/*
	  Run → Plan (DS §9.18, views.md §2.2): the plan as a document, composed client-side
	  as markdown and read by `MarkdownReader` (no HTML), with the decision in the meta
	  column. Folds the old approval gate, revision review and recalibrate controls.
	  Writes: `approve()` and `recalibrate()` only; everything else is a read.
	*/
	import type { Snippet } from 'svelte';
	import MarkdownReader from './MarkdownReader.svelte';
	import Button from './Button.svelte';
	import IconButton from './IconButton.svelte';
	import Mark from './Mark.svelte';
	import StateMark from './StateMark.svelte';
	import Segmented from './Segmented.svelte';
	import Icon from './Icon.svelte';
	import { parseMarkdown, type Block } from './markdown';
	import { daemon, DaemonError, type CheckpointReport, type Plan, type PlanGraph, type RecalibrationReport, type RiskReport } from './daemon';
	import type { Resource } from './resource.svelte';
	import type { Field } from './field';
	import { approvalRefusal, budgetOf, calloutsOf, downstream, PROVENANCE } from './gate';
	import { bandsOf, formatCap, refusalMessage } from './revision';
	import { tokens } from './bank';

	let {
		planId,
		graph,
		field,
		risk,
		plan,
		revision,
		reviews,
		accounted = null,
		readonly = false,
		onselect,
		onapproved,
		onrevised
	}: {
		planId: string;
		graph: PlanGraph;
		field: Field;
		risk: Resource<RiskReport> | null;
		plan: Resource<Plan> | null;
		revision: Resource<RecalibrationReport> | null;
		reviews: Resource<CheckpointReport> | null;
		/** Tokens accounted so far, from the status bundle; `null` is unread. */
		accounted?: number | null;
		/** Replay: the document reads, the decisions do not. */
		readonly?: boolean;
		onselect: (id: string) => void;
		onapproved: () => void;
		onrevised: () => void;
	} = $props();

	const version = $derived(graph.version);
	const approved = $derived(graph.approval === 'approved');
	const doc = $derived(plan?.data ?? null);

	/* --- the document, as markdown source (parsed to tokens by markdown.ts) ---------- */
	const cell = (s: string) => s.replace(/\|/g, '/').replace(/\n+/g, ' ');
	const code = (s: string) => `\`${s.replace(/`/g, "'")}\``;
	const firstLine = (s: string) => s.split('\n').find((l) => l.trim())?.trim() ?? '';
	const title = $derived(doc ? (doc.title ?? firstLine(doc.brief)) : graph.plan_id);

	/** Feature-detected: the projection may or may not carry these yet (B5). */
	const extra = $derived(doc as (Plan & { acceptance?: string | string[]; planner_note?: string }) | null);

	const showRev = $state({ on: null as boolean | null });
	const revisionShown = $derived(showRev.on ?? (!approved && !!revision?.data));

	function source(): string {
		const out: string[] = [];
		out.push(`# ${title}`, '');
		if (extra?.planner_note) out.push(`> **Planner note**`, `> ${extra.planner_note.replace(/\n/g, '\n> ')}`, '');
		out.push('## Brief', '', doc ? doc.brief.trim() : '_The plan has not been read._', '');
		if (extra?.acceptance && (Array.isArray(extra.acceptance) ? extra.acceptance.length : extra.acceptance.trim())) {
			const items = Array.isArray(extra.acceptance) ? extra.acceptance : extra.acceptance.split('\n').filter((l) => l.trim());
			out.push('## Acceptance', '', ...items.map((i) => (/^\s*[-*]\s/.test(i) ? i.replace(/^\s*[-*]\s*(\[[ x]\]\s*)?/, '- [ ] ') : `- [ ] ${i}`)), '');
		}

		if (revisionShown && revision?.data) {
			const r = revision.data;
			out.push(`## Revision v${r.revision.from_version} → v${r.revision.to_version}`, '');
			out.push(Object.entries(r.revision.counts).map(([k, n]) => `${n} ${k}`).join(' · '), '');
			const rows = bandsOf(r, graph, doc, reviews?.data ?? null).flatMap((b) => b.rows.map((row) => [b.label, row] as const));
			if (rows.length) {
				out.push('| Band | Change | Member | Reading |', '| --- | --- | --- | --- |');
				for (const [label, row] of rows) out.push(`| ${cell(label)} | ${row.node.change} | ${code(row.selectableId ?? row.node.new_ids[0] ?? row.node.old_ids[0] ?? '—')} | ${cell(row.message)} |`);
				out.push('');
			}
		}

		out.push('## Approach', '', '### Lanes', '');
		out.push('| Lane | Chain | Critical |', '| --- | --- | --- |');
		field.lanes.forEach((lane, i) => {
			const crit = lane.filter((m) => m.onCriticalPath).length;
			out.push(`| ${String(i + 1).padStart(2, '0')} | ${code(lane.map((m) => m.node.initiative_id).join(' → '))} | ${crit ? `${crit} on the path` : '—'} |`);
		});
		out.push('');
		if (doc) {
			const writes = new Map<string, string[]>();
			for (const m of field.members) for (const path of doc.initiatives[m.node.initiative_id]?.spec.routes.writes ?? []) writes.set(path, [...(writes.get(path) ?? []), m.node.initiative_id]);
			const conflictPaths = new Set((risk?.data?.conflicts ?? []).flatMap((c) => c.paths));
			if (writes.size) {
				const w = Math.max(...[...writes.keys()].map((p) => p.length)) + 3;
				out.push('### Write routes', '', '```routes');
				for (const [path, ids] of writes) out.push(`${path.padEnd(w)}${ids.join(' · ')}${conflictPaths.has(path) ? '   # conflict: the daemon serialises these' : ''}`);
				out.push('```', '');
			}
		}

		out.push('## Initiatives', '', '| ID | Initiative | Role | Waits on |', '| --- | --- | --- | --- |');
		for (const m of field.members)
			out.push(`| ${code(m.node.initiative_id)} | ${cell(m.node.name)} | ${cell(doc?.initiatives[m.node.initiative_id]?.spec.contract?.role ?? '—')} | ${m.node.depends_on.length ? m.node.depends_on.map(code).join(', ') : '—'} |`);
		out.push('');

		out.push('## Risks', '');
		const callouts = calloutsOf(graph, risk?.data ?? null, downstream(graph));
		if (callouts === null) out.push('- The risk report is unread, so conflicts, chokepoints and overlap are unknown, not none.');
		else if (callouts.length === 0) out.push('- No write conflict, chokepoint or unordered overlap in this revision.');
		else {
			const hard = callouts.filter((c) => !c.advisory);
			const soft = callouts.filter((c) => c.advisory);
			const blocking = hard.filter((c) => c.kind === 'scope');
			for (const c of hard)
				if (c.kind === 'scope' && !approved) out.push(`> [!NEEDS] ${c.text}`, '');
				else out.push(`- **${c.about.join(' and ')}** ${c.text}`);
			if (hard.length && blocking.length === 0) out.push('');
			for (const c of soft.slice(0, 6)) out.push(`- ${c.text}`);
			if (soft.length > 6) out.push(`- … and ${soft.length - 6} more advisory overlaps; the schedule lists every one.`);
		}
		out.push('');

		out.push('## Budget', '');
		const cap = doc?.token_cap ?? null;
		const planning = budgetOf(doc).planningTokens;
		const bits = [`Cap **${cap === null ? '—' : tokens(cap)}** tokens${cap === null ? ' (none declared; that is not unlimited)' : ''}`];
		if (planning !== null && doc?.planner_usage) bits.push(`the planner reported **${tokens(planning)}** (${PROVENANCE[doc.planner_usage.source]})`);
		bits.push(accounted === null ? 'accounted so far is unread' : `**${tokens(accounted)}** accounted so far`);
		out.push(`${bits.join('. ')}.`);
		return out.join('\n');
	}
	const md = $derived(source());

	let raw = $state(false);
	let wide = $state(false);
	let copied = $state(false);
	const blocks = $derived<Block[]>(
		raw ? [{ kind: 'code', language: 'markdown', text: md, lines: md.split('\n') }] : parseMarkdown(md)
	);
	async function copyMd() {
		try {
			await navigator.clipboard.writeText(md);
			copied = true;
			setTimeout(() => (copied = false), 1400);
		} catch {
			/* clipboard refused: nothing claimed */
		}
	}

	/* --- approve (R3 logic: refusal first, outcome beside the control) ---------------- */
	let decide = $state<{ phase: 'idle' | 'sending' | 'done' | 'failed'; message: string }>({ phase: 'idle', message: '' });
	let decidedFor = '';
	$effect(() => {
		const key = `${planId}@${version}`;
		if (decidedFor === key) return;
		decidedFor = key;
		decide = { phase: 'idle', message: '' };
	});
	const refusal = $derived(
		approvalRefusal(doc, risk?.data ?? null, version) ??
			(plan?.stale || risk?.stale ? 'Read the current plan and risk report before approving.' : null)
	);
	async function approve() {
		if (approved || refusal || decide.phase === 'sending') return;
		decide = { phase: 'sending', message: '' };
		try {
			const result = await daemon.approve(planId, version, true);
			decide = { phase: 'done', message: `Revision ${result.version} approved. The run started.` };
			onapproved();
		} catch (cause) {
			decide = {
				phase: 'failed',
				message:
					cause instanceof DaemonError
						? cause.status === 409 && /run.*(active|already|progress)|already.*run/i.test(cause.message)
							? `A run is already going. ${cause.message}`
							: cause.message
						: 'Something in this build failed while asking the daemon to approve and run.'
			};
		}
	}

	/* --- recalibrate (R-revise logic, folded in) ------------------------------------- */
	type Phase = 'idle' | 'armed' | 'sending' | 'done' | 'failed';
	let rphase = $state<Phase>('idle');
	let reason = $state('');
	let actionId: string | null = null;
	let rmessage = $state('');
	let slow = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	function arm() {
		actionId = crypto.randomUUID();
		rphase = 'armed';
		rmessage = '';
	}
	function disarm() {
		if (rphase === 'sending') return;
		rphase = 'idle';
		actionId = null;
		rmessage = '';
		slow = false;
		if (timer) clearTimeout(timer);
	}
	async function recalibrate() {
		if (rphase !== 'armed') return;
		rphase = 'sending';
		slow = false;
		timer = setTimeout(() => (slow = true), 20_000);
		try {
			const result = await daemon.recalibrate(planId, { reason: reason.trim() || null, action_id: actionId ?? undefined });
			rphase = 'done';
			rmessage =
				result.to_version === version + 1
					? `Revision ${result.to_version} recorded. Nothing runs until you approve it.`
					: `Already answered: this is revision ${result.to_version}; the planner was not called again.`;
			actionId = null;
			showRev.on = true;
			onrevised();
		} catch (cause) {
			const error = cause instanceof DaemonError ? cause : new DaemonError('bad_response', 'The daemon refused this request.');
			const kind = refusalMessage(error.status, error.message);
			rphase = 'failed';
			const raced = kind === 'refusal' && error.message.toLowerCase().includes('plan changed');
			rmessage = raced
				? 'The plan changed while the planner was thinking, so the daemon refused the answer. Nothing was appended; read the plan as it is now, then ask again.'
				: error.status === 400
					? `${error.message} Nothing was appended: the plan is still at revision ${version}.`
					: error.message;
			if (raced) onrevised();
			if (error.status !== null && error.status >= 400 && error.status < 500) actionId = null;
		} finally {
			if (timer) clearTimeout(timer);
		}
	}

	const planner = $derived(doc?.planner ?? null);
	const ago = (iso: string) => new Date(iso).toLocaleString();
	const approvedAt = $derived((doc as (Plan & { approved_at?: string | null; approved_by?: string }) | null)?.approved_at ?? null);
	const approvedBy = $derived((doc as (Plan & { approved_by?: string }) | null)?.approved_by ?? null);
	const branches = $derived(
		doc as (Plan & { start_branch?: string | null; target_branch?: string | null }) | null
	);
</script>

{#snippet toolbar()}
	<StateMark tone={approved ? 'settled' : 'needs'} word="Revision {version} · {approved ? 'approved' : 'proposed'}" />
	<span class="ibar rt">
		<IconButton icon={copied ? 'check' : 'copy'} label={copied ? 'Copied' : 'Copy markdown'} onclick={() => void copyMd()} />
		<IconButton icon="file-text" label="Raw markdown" pressed={raw} onclick={() => (raw = !raw)} />
		<IconButton icon="text-wrap" label="Reading width" pressed={wide} onclick={() => (wide = !wide)} />
		{#if revision?.data}<IconButton icon="history" label="Revision comparison" pressed={revisionShown} onclick={() => (showRev.on = !revisionShown)} />{/if}
	</span>
{/snippet}

{#snippet meta()}
	<div>
		<span class="lbl">Revision</span>
		<div class="seg-wrap"><Segmented label="Revision" options={[{ id: String(version), label: `v${version}` }]} value={String(version)} onchange={() => {}} /></div>
	</div>
	<dl class="kv">
		<dt>Approval</dt>
		<dd>{approved ? (approvedBy || approvedAt ? `${approvedBy ?? 'approved'}${approvedAt ? ` · ${ago(approvedAt)}` : ''}` : 'approved') : 'awaiting you'}</dd>
		<dt>Planner</dt>
		<dd>{#if planner}<Mark harness={planner.harness} size={16} /><Mark model={planner.model} size={16} /><span class="mono ellipsis" title="{planner.harness} › {planner.model}">{planner.model}</span>{:else}<span class="muted">—</span>{/if}</dd>
		<dt>Members</dt>
		<dd class="mono">{field.members.length} · {field.lanes.length} {field.lanes.length === 1 ? 'lane' : 'lanes'}</dd>
		<dt>Cap</dt>
		<dd class="mono">{doc ? formatCap(doc.token_cap) : '—'}</dd>
		{#if branches && (branches.start_branch || branches.target_branch)}
			<dt>Branches</dt>
			<dd class="branch"><Icon name="git-branch" size={14} />{branches.start_branch ?? '—'}<Icon name="chevron-right" size={14} />{branches.target_branch ?? '—'}</dd>
		{/if}
	</dl>

	{#if !readonly}
		<div class="decide">
			{#if !approved}
				<div class="btnrow nomt">
					<Button
						icon="check-check"
						kind="primary"
						busy={decide.phase === 'sending'}
						disabled={refusal !== null || decide.phase === 'done'}
						title="Approves revision {version} and starts the whole-plan run. Approval cannot be withdrawn."
						onclick={() => void approve()}
					>Approve v{version}</Button>
					<Button icon="x" disabled title="Rejecting a whole proposal is not available yet. Recalibrate asks the planner to revise it.">Reject</Button>
				</div>
				{#if refusal}<p class="state" data-tone="waiting" role="status">{refusal}</p>{/if}
				{#if decide.phase === 'failed'}<p class="state" data-tone="failed" role="alert">Refused: {decide.message}</p>{/if}
				{#if decide.phase === 'done'}<p class="state" data-tone="pass" role="status">{decide.message}</p>{/if}
			{:else if decide.phase === 'done'}
				<p class="state" data-tone="pass" role="status">{decide.message}</p>
			{/if}

			{#if rphase === 'idle' || rphase === 'done'}
				<div class="btnrow nomt"><Button icon="refresh-cw" onclick={arm}>Recalibrate</Button></div>
				{#if rphase === 'done'}<p class="state" data-tone="pass" role="status">{rmessage}</p>{/if}
			{:else if rphase === 'failed'}
				<p class="state" data-tone="failed" role="alert">Not revised: {rmessage}</p>
				<div class="btnrow nomt"><Button icon="rotate-ccw" small onclick={arm}>Try again</Button><Button icon="x" small onclick={disarm}>Cancel</Button></div>
			{:else}
				<div class="field-l">
					<label class="lbl" for="recal-reason">Why this plan is wrong</label>
					<textarea id="recal-reason" class="ta short" rows="3" maxlength="400" bind:value={reason} disabled={rphase === 'sending'} placeholder="Optional. One line reaches the planner."></textarea>
				</div>
				<div class="btnrow nomt">
					<Button icon="refresh-cw" kind="primary" busy={rphase === 'sending'} title="Calls the planner and spends planner tokens whether or not you approve the result. Completed work comes back unchanged; running members are not interrupted." onclick={() => void recalibrate()}>Call planner</Button>
					<Button icon="x" disabled={rphase === 'sending'} onclick={disarm}>Cancel</Button>
				</div>
				{#if slow}<p class="state" data-tone="waiting" role="status">The planner has up to two minutes; leaving does not cancel it.</p>{/if}
			{/if}
		</div>
	{/if}
{/snippet}

<div class="plan-scroll">
	{#if plan && !doc}
		<p class="empty-line" aria-busy={plan.phase === 'loading' || undefined}>{plan.phase === 'error' ? `The plan could not be read: ${plan.error?.message ?? ''}` : 'Reading the plan…'}</p>
	{:else}
		<MarkdownReader {blocks} toc {wide} {toolbar} {meta} />
	{/if}
</div>

<style>
	.plan-scroll { height: 100%; overflow: auto; min-height: 0; }
	.rt { margin-left: auto; }
	.seg-wrap { margin-top: 8px; }
	.decide { display: grid; gap: 12px; }
	.nomt { margin-top: 0; }
	.ta.short { min-height: 72px; }
	.kv dd { overflow: hidden; }
	.state { white-space: normal; align-items: flex-start; line-height: 1.3; }
</style>
