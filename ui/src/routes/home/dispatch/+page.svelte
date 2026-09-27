<script lang="ts">
	import { goto } from '$app/navigation';
	import { Resource } from '$lib/resource.svelte';
	import { daemon, type AssetSummary, type Kitchen, type KitchenAssignment, type LibraryIssue } from '$lib/daemon';
	import { activeAssets, assignmentKey, assignments, ready } from '$lib/dispatch';
	import { PLANNER_SLOT, clearSlotEffort, effortPool, pickSlotEffort, roleSlot, slotEffort } from '$lib/kitchen';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	const roles = new Resource<AssetSummary[]>((signal) => daemon.libraryRoles(signal));
	const contracts = new Resource<AssetSummary[]>((signal) => daemon.libraryContracts(signal));
	let loaded = $state(false);
	let brief = $state('');
	let acceptance = $state('');
	let refs = $state<string[]>([]);
	let planner = $state('');
	let assigned = $state<Record<string, string>>({});
	/* One explicit level per assignment SLOT — the planner, or one role by name —
	   never per pair: two slots may run the same pair at different levels. A slot
	   whose model changes drops its pick and starts at the new pair's highest. */
	let efforts = $state<Record<string, string>>({});
	let cap = $state('');
	let query = $state('');
	let issues = $state<LibraryIssue[]>([]);
	let pending = $state(false);
	let elapsed = $state(0);
	let error = $state('');
	let adjust = $state(false);
	const catalog = $derived(kitchen.data ? assignments(kitchen.data) : []);
	const selectedRoles = $derived((roles.data ?? []).filter((item) => refs.includes(item.ref)));
	$effect(() => {
		try {
			const draft = JSON.parse(localStorage.getItem('herdsman-dispatch-draft') ?? '{}');
			brief = draft.brief ?? '';
			acceptance = draft.acceptance ?? '';
			refs = draft.refs ?? [];
			planner = draft.planner ?? '';
			assigned = draft.assigned ?? {};
			cap = draft.cap ?? '';
		} catch { /* An invalid or blocked draft does not prevent starting afresh. */ }
		loaded = true;
		void kitchen.load(); void roles.load(); void contracts.load();
	});
	$effect(() => {
		if (!loaded) return;
		localStorage.setItem('herdsman-dispatch-draft', JSON.stringify({ brief, acceptance, refs, planner, assigned, cap }));
	});
	$effect(() => {
		if (!kitchen.data || planner) return;
		const preferred = kitchen.data.defaults.planner;
		if (preferred && ready(kitchen.data, preferred)) planner = assignmentKey(preferred);

	});
	$effect(() => {
		if (!refs.length) { issues = []; return; }
		const controller = new AbortController();
		void daemon.validateAssets(refs, 'Dispatch', controller.signal).then((result) => { issues = result.issues; }).catch(() => {
			if (!controller.signal.aborted) issues = [{ code: 'reference-missing', severity: 'error', ref: 'Dispatch', message: 'Library validation unavailable', detail: 'Read the Library before dispatching.' }];
		});
		return () => controller.abort();
	});
	function toggle(ref: string) { refs = refs.includes(ref) ? refs.filter((value) => value !== ref) : [...refs, ref]; }
	function select(value: string): KitchenAssignment | undefined { return catalog.find((entry) => assignmentKey(entry) === value); }
	const poolOf = (key: string): string[] => (kitchen.data ? effortPool(kitchen.data, key) : []);
	const levelOf = (slot: string, key: string): string | null => slotEffort(poolOf(key), efforts[slot]);
	function pickEffort(slot: string, level: string) { efforts = pickSlotEffort(efforts, slot, level); }
	function modelChanged(slot: string) { efforts = clearSlotEffort(efforts, slot); }
	/** The assignment as it is sent: the pair plus the one level it runs under.
	 * The key is omitted when the harness reports no levels — the daemon launches
	 * it with its own default, and a level this build invented would be refused. */
	function assignedFor(slot: string, key: string): KitchenAssignment {
		const assignment: KitchenAssignment = { ...select(key)! };
		const level = levelOf(slot, key);
		if (level !== null) assignment.effort = level;
		return assignment;
	}
	function roleChoice(role: AssetSummary): string {
		const desired = kitchen.data?.defaults.roles[role.name] ?? kitchen.data?.defaults.initiative;
		return assigned[role.name] ?? (desired && kitchen.data && ready(kitchen.data, desired) ? assignmentKey(desired) : assignmentKey(catalog[0] ?? { harness: '', model: '' }));
	}
	const blocking = $derived(issues.some((item) => item.severity === 'error'));
	/* What still stands between the operator and a plan, said beside the disabled
	   control rather than left for them to reverse-engineer from a grey button. */
	const missing = $derived([
		...(brief.trim() ? [] : ['a brief']),
		...(select(planner) ? [] : ['a ready planner']),
		...(selectedRoles.some((role) => !select(roleChoice(role))) ? ['a ready model for every role'] : []),
		...(blocking ? ['Library errors resolved'] : [])
	]);
	let failure = $state<HTMLElement>();
	async function submit() {
		if (!kitchen.data || missing.length) return;
		pending = true; error = ''; elapsed = 0;
		const timer = setInterval(() => elapsed++, 1000);
		try {
			const plan = await daemon.createPlan({ brief, acceptance, assets: refs, planner: assignedFor(PLANNER_SLOT, planner), roles: Object.fromEntries(selectedRoles.map((role) => [role.name, assignedFor(roleSlot(role.name), roleChoice(role))])), token_cap: cap ? Number(cap) : null });
			localStorage.removeItem('herdsman-dispatch-draft');
			await goto(`/run?plan=${encodeURIComponent(plan.id)}`);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Planning failed.';
			queueMicrotask(() => failure?.focus());
		}
		finally { clearInterval(timer); pending = false; }
	}
</script>

{#snippet models()}
	{#each kitchen.data?.models ?? [] as option (assignmentKey(option))}
		{@const readiness = kitchen.data?.readiness.find((item) => item.harness === option.harness)}
		<option value={assignmentKey(option)} disabled={readiness?.state !== 'ready'}>{option.harness} / {option.model} · {readiness?.state ?? 'unknown'}{readiness?.state !== 'ready' ? ` — ${readiness?.reason ?? 'Not ready'}` : ''}</option>
	{/each}
{/snippet}

<div class="dispatch">
	<div class="cap-line">
		<p class="label rule-label">
			<span>Dispatch</span>
			<span class="rule"></span>
			<span>Plan proposal</span>
		</p>
		<a class="plate tab" href="/home">← Fleet</a>
	</div>
	<p class="prose lead">Plan first. No worker starts until you approve the proposal in Run.</p>

	<h2 class="headline">Brief and acceptance</h2>

		<p class="field">
			<label class="label" for="dispatch-brief">Brief</label>
			<textarea class="plate" id="dispatch-brief" rows="7" bind:value={brief} placeholder="What should the pipeline accomplish?" aria-describedby="brief-note"></textarea>
		</p>
		<p id="brief-note" class="req">Required. The planner reads this as the whole task.</p>
		<p class="field">
			<label class="label" for="dispatch-acceptance">Acceptance criteria</label>
			<textarea class="plate" id="dispatch-acceptance" rows="4" bind:value={acceptance} placeholder="What will count as done?"></textarea>
		</p>

	<div class="foot">
		<span class="spacer"></span>
		<button type="button" class="act plate" disabled={pending || missing.length > 0} onclick={() => void submit()}>{pending ? 'Planning…' : 'Create plan'}</button>
		{#if missing.length}<span class="req">Needs {missing.join(' · ')}</span>{/if}
	</div>
	<!-- Live regions stay beside the action from first paint. -->
	<p class="outcome member" data-state="balanced" role="status">{#if pending}<span class="label">Planning</span> with {planner} · {elapsed}s elapsed. No progress reported by daemon.{/if}</p>
	<p class="outcome member" data-state="failed" role="alert" tabindex="-1" bind:this={failure}>{#if error}<span class="label">Failed</span> <span class="prose">{error} · Your draft is preserved.</span> <button type="button" class="act plate" onclick={() => void submit()}>Try again</button>{/if}</p>

	<details class="adjust" bind:open={adjust}>
		<summary class="label">Adjust</summary>
		<p class="prose quiet">Uses Kitchen’s default planner and every active role unless you choose roles below.</p>
		<h3 class="section-title">Roles &amp; contracts</h3>
		<p class="prose">These Library refs are frozen at approval. Warnings below come from Library validation.</p>
		<p class="field">
			<label class="label" for="asset-filter">Filter choices</label>
			<input class="plate" id="asset-filter" type="search" bind:value={query} />
		</p>
		{#each [{ name: 'Roles', data: roles }, { name: 'Contracts', data: contracts }] as group (group.name)}
			{@const shown = activeAssets(group.data.data ?? [], group.name === 'Roles' ? 'role' : 'contract').filter((item) => `${item.title} ${item.ref}`.toLowerCase().includes(query.toLowerCase()))}
			{@const chosen = (group.data.data ?? []).filter((item) => refs.includes(item.ref)).length}
			<fieldset>
				<legend class="label rule-label">
					<span>{group.name}</span>
					<span class="rule"></span>
					<span>{chosen} chosen</span>
				</legend>
				{#if group.data.error}<p class="finding" role="alert"><span class="label member" data-state="failed">Unread</span> {group.data.error.message}</p>{/if}
				{#if group.data.phase === 'loading'}<p class="prose quiet">Reading {group.name.toLowerCase()}…</p>{/if}
				{#if group.data.data?.length === 0}<p class="prose quiet">No {group.name.toLowerCase()} in Library. Add one there first.</p>
				{:else if group.data.data && shown.length === 0}<p class="prose quiet">Nothing in {group.name.toLowerCase()} matches “{query}”.</p>{/if}
				<ul class="choices">
					{#each shown as asset (asset.ref)}
						<li>
							<label class="choice">
								<input type="checkbox" checked={refs.includes(asset.ref)} onchange={() => toggle(asset.ref)} />
								<span class="choice-name">{asset.title || asset.name}</span>
								<span class="label dim">{asset.ref} · {asset.tokens} tokens</span>
							</label>
						</li>
					{/each}
				</ul>
			</fieldset>
		{/each}
		{#each issues as issue}
			<p class="finding" role={issue.severity === 'error' ? 'alert' : 'status'}>
				<span class="label member" data-state={issue.severity === 'error' ? 'failed' : 'slack'}>{issue.severity}</span>
				<span class="finding-message">{issue.message}</span>
				<span class="prose">{issue.detail}</span>
			</p>
		{/each}
		<h3 class="section-title">Assignments</h3>
		{#if kitchen.error}<p class="finding" role="alert"><span class="label member" data-state="failed">Unread</span> {kitchen.error.message}</p>{/if}
		{#if kitchen.phase === 'loading'}<p class="prose quiet">Reading Kitchen…</p>{/if}
		{#if kitchen.data && catalog.length === 0}<p class="prose">No ready model assignment in Kitchen. <a href="/kitchen">Configure Kitchen →</a></p>{/if}
		{#each kitchen.data?.blockers ?? [] as blocker}
			<p class="finding"><span class="label member" data-state="failed">Blocked</span> <span class="prose">{blocker}</span></p>
		{/each}
		<p class="field">
			<label class="label" for="dispatch-planner">Planner</label>
			<span class="pick">
				<select class="plate" id="dispatch-planner" bind:value={planner} onchange={() => modelChanged(PLANNER_SLOT)} disabled={!kitchen.data?.models.length}>
					{#if !select(planner)}<option value={planner} disabled>No ready planner…</option>{/if}
					{@render models()}
				</select>
			</span>
			{#if poolOf(planner).length > 0}
				<span class="chips" role="group" aria-label={`Effort for ${planner}`}>
					{#each poolOf(planner) as level (level)}
						<button type="button" class="chip" aria-pressed={levelOf(PLANNER_SLOT, planner) === level}
							onclick={() => pickEffort(PLANNER_SLOT, level)}>{level}</button>
					{/each}
				</span>
			{/if}
		</p>
		{#each selectedRoles as role (role.ref)}
			<p class="field">
				<label class="label" for={`role-${role.ref}`}>{role.title || role.name}</label>
				<span class="pick">
					<select class="plate" id={`role-${role.ref}`} value={roleChoice(role)} onchange={(event) => { assigned = { ...assigned, [role.name]: event.currentTarget.value }; modelChanged(roleSlot(role.name)); }}>
						{@render models()}
					</select>
				</span>
				{#if poolOf(roleChoice(role)).length > 0}
					<span class="chips" role="group" aria-label={`Effort for ${roleChoice(role)}`}>
						{#each poolOf(roleChoice(role)) as level (level)}
							<button type="button" class="chip" aria-pressed={levelOf(roleSlot(role.name), roleChoice(role)) === level}
								onclick={() => pickEffort(roleSlot(role.name), level)}>{level}</button>
						{/each}
					</span>
				{/if}
			</p>
		{/each}
		<p class="field">
			<label class="label" for="dispatch-cap">Plan token cap</label>
			<input class="plate" id="dispatch-cap" type="number" min="0" step="1" bind:value={cap} aria-describedby="cap-note" />
		</p>
		<p id="cap-note" class="req">Optional. Enforced at admission.</p>
	</details>
</div>

<style>
	.dispatch {
		max-width: 52rem;
	}

	/* --- the caption: Home's ridden label, with the way back riding its end -- */
	.cap-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1.25rem;
	}
	.cap-line .rule-label {
		flex: 1 1 16rem;
		margin: 0;
	}
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.tab {
		--cut: 9px;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		text-decoration: none;
		white-space: nowrap;
		color: var(--ink-2);
		border: 1px solid var(--rule);
		padding: 0.35rem 0.85rem;
	}
	.tab:hover {
		border-color: var(--red);
		color: var(--red);
	}
	.lead {
		margin: 1.25rem 0 0;
	}

	.adjust {
		margin-top: 1.5rem;
		border-top: 1px solid var(--rule);
		padding-top: 1rem;
	}
	.adjust summary {
		cursor: pointer;
		width: fit-content;
	}
	.adjust summary:hover { color: var(--red); }
	.section-title {
		font: inherit;
		font-weight: 500;
		margin: 2rem 0 0.75rem;
	}

	.headline {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 70, 'wght' 620;
		font-weight: 620;
		font-size: 2rem;
		line-height: 1;
		letter-spacing: -0.01em;
		text-transform: uppercase;
		text-wrap: balance;
		margin: 2.75rem 0 1.25rem;
	}

	/* --- fields -------------------------------------------------------------- */
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin: 1.5rem 0 0;
	}
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
	/* The pair's effort pool, one level at a time: the Memory shelf's chips,
	   copied because this row picks one of a harness's own levels and the
	   shelf's block filters leaves. Hidden entirely when the pair reports none. */
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		margin-top: 0.1rem;
	}
	.chip {
		font: inherit;
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
		background: transparent;
		border: 0;
		border-bottom: 1px solid transparent;
		padding: 0.2rem 0.5rem 0.25rem;
		cursor: pointer;
	}
	.chip:hover {
		color: var(--red);
	}
	.chip[aria-pressed='true'] {
		color: var(--ink);
		border-bottom-color: var(--member-line);
	}
	input.plate,
	textarea,
	select {
		--cut: 10px;
		font: inherit;
		width: 100%;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	textarea {
		resize: vertical;
		line-height: 1.6;
	}
	textarea::placeholder,
	input::placeholder {
		color: var(--ink-2);
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
	input:focus-visible,
	textarea:focus-visible,
	select:focus-visible {
		border-color: var(--red);
	}
	.req {
		margin: 0.4rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.1em;
		line-height: 1.5;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.prose {
		margin: 0;
	}
	.prose + .prose {
		margin-top: 0.75rem;
	}
	.quiet {
		margin-top: 0.75rem;
	}

	/* --- library choices: the register's entry, with a box to tick ----------- */
	fieldset {
		border: 0;
		margin: 2rem 0 0;
		padding: 0;
		min-width: 0;
	}
	legend {
		float: left;
		width: 100%;
		padding: 0;
		margin: 0 0 0.5rem;
	}
	legend + * {
		clear: left;
	}
	.choices {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.choices li {
		border-bottom: 1px solid var(--rule);
	}
	.choice {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		column-gap: 0.75rem;
		align-items: baseline;
		padding: 0.6rem 0;
		cursor: pointer;
	}
	.choice input {
		grid-row: span 2;
		margin: 0;
		/* Carbon, not red: a ticked box is a choice seated in the structure. */
		accent-color: var(--ink);
	}
	.choice-name {
		font-weight: 500;
	}
	.choice:hover .choice-name {
		color: var(--red);
	}
	.dim {
		overflow-wrap: anywhere;
	}

	/* --- findings: the daemon's verdict, a member label over its sentence ---- */
	.finding {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		margin: 0.75rem 0 0;
		padding-top: 0.6rem;
		border-top: 1px solid var(--rule);
	}
	.finding .label {
		color: var(--member-ink);
	}
	.finding .label[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash) 1px;
		text-underline-offset: 0.3em;
	}
	.finding-message {
		color: var(--ink);
	}

	/* --- outcome lines: in the document from first paint, no space until they speak */
	.outcome {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		margin: 1rem 0 0;
		color: var(--member-ink);
	}
	.outcome .label {
		color: var(--member-ink);
	}
	.outcome:empty {
		height: 0;
		margin: 0;
		overflow: hidden;
	}

	/* --- the foot ------------------------------------------------------------ */
	.foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
		margin-top: 2.5rem;
		padding-top: 1.25rem;
		border-top: 1px solid var(--rule);
	}
	.foot .spacer {
		flex: 1;
	}
	/* The reason a control is closed sits under it, never between two controls. */
	.foot .req {
		order: 3;
		flex-basis: 100%;
		margin: 0;
		text-align: right;
	}

	@media (max-width: 60rem) {
		.headline {
			font-size: 1.625rem;
			margin-top: 2.25rem;
		}
	}
</style>
