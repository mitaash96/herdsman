<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { Resource } from '$lib/resource.svelte';
	import { daemon, type AssetSummary, type Kitchen, type KitchenAssignment, type LibraryIssue } from '$lib/daemon';
	import { activeAssets, assignmentKey, assignments, ready } from '$lib/dispatch';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	const roles = new Resource<AssetSummary[]>((signal) => daemon.libraryRoles(signal));
	const contracts = new Resource<AssetSummary[]>((signal) => daemon.libraryContracts(signal));
	let loaded = $state(false);
	let brief = $state('');
	let acceptance = $state('');
	let refs = $state<string[]>([]);
	let planner = $state('');
	let assigned = $state<Record<string, string>>({});
	let cap = $state('');
	let query = $state('');
	let issues = $state<LibraryIssue[]>([]);
	let pending = $state(false);
	let elapsed = $state(0);
	let error = $state('');
	const step = $derived(Math.min(4, Math.max(1, Number(page.url.searchParams.get('step') ?? 1) || 1)));
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
		else planner = assignmentKey(catalog[0] ?? { harness: '', model: '' });
	});
	$effect(() => {
		if (!refs.length) { issues = []; return; }
		const controller = new AbortController();
		void daemon.validateAssets(refs, 'Dispatch', controller.signal).then((result) => { issues = result.issues; }).catch(() => {
			if (!controller.signal.aborted) issues = [{ code: 'reference-missing', severity: 'error', ref: 'Dispatch', message: 'Library validation unavailable', detail: 'Read the Library before dispatching.' }];
		});
		return () => controller.abort();
	});
	function navigate(next: number) { void goto(`/home/dispatch?step=${next}`); }
	function toggle(ref: string) { refs = refs.includes(ref) ? refs.filter((value) => value !== ref) : [...refs, ref]; }
	function select(value: string): KitchenAssignment | undefined { return catalog.find((entry) => assignmentKey(entry) === value); }
	function roleChoice(role: AssetSummary): string {
		const desired = kitchen.data?.defaults.roles[role.name] ?? kitchen.data?.defaults.initiative;
		return assigned[role.name] ?? (desired && kitchen.data && ready(kitchen.data, desired) ? assignmentKey(desired) : assignmentKey(catalog[0] ?? { harness: '', model: '' }));
	}
	const steps = [
		{ tab: 'Brief', title: 'Brief and acceptance' },
		{ tab: 'Roles & contracts', title: 'Roles and contracts' },
		{ tab: 'Assignments', title: 'Assignments' },
		{ tab: 'Plan', title: 'Plan' }
	];
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
			const plan = await daemon.createPlan({ brief, acceptance, assets: refs, planner: select(planner)!, roles: Object.fromEntries(selectedRoles.map((role) => [role.name, select(roleChoice(role))!])), token_cap: cap ? Number(cap) : null });
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
			<span>Step {step} of {steps.length}</span>
		</p>
		<a class="plate tab" href="/home">← Fleet</a>
	</div>
	<p class="prose lead">Plan first. No worker starts until you approve the proposal in Run.</p>

	<!-- The flow drawn as a member: steps are seats on one run that overshoots
	     both ends. Where you stand is location, so it takes the carbon halo and
	     never red; a step behind you is seated. -->
	<nav aria-label="Dispatch steps">
		<ol class="steps">
			{#each steps as item, index (item.tab)}
				<li>
					<button
						type="button"
						class="step"
						data-at={index + 1 < step ? 'passed' : index + 1 === step ? 'current' : 'ahead'}
						aria-current={step === index + 1 ? 'step' : undefined}
						onclick={() => navigate(index + 1)}
					>
						<span class="ring" aria-hidden="true"></span>
						<span class="n">{index + 1}</span>
						<span class="tab-name">{item.tab}</span>
					</button>
				</li>
			{/each}
		</ol>
	</nav>

	<h2 class="headline">{steps[step - 1].title}</h2>

	{#if step === 1}
		<p class="field">
			<label class="label" for="dispatch-brief">Brief</label>
			<textarea class="plate" id="dispatch-brief" rows="7" bind:value={brief} placeholder="What should the pipeline accomplish?" aria-describedby="brief-note"></textarea>
		</p>
		<p id="brief-note" class="req">Required. The planner reads this as the whole task.</p>
		<p class="field">
			<label class="label" for="dispatch-acceptance">Acceptance criteria</label>
			<textarea class="plate" id="dispatch-acceptance" rows="4" bind:value={acceptance} placeholder="What will count as done?"></textarea>
		</p>
	{:else if step === 2}
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
	{:else if step === 3}
		{#if kitchen.error}<p class="finding" role="alert"><span class="label member" data-state="failed">Unread</span> {kitchen.error.message}</p>{/if}
		{#if kitchen.phase === 'loading'}<p class="prose quiet">Reading Kitchen…</p>{/if}
		{#if kitchen.data && catalog.length === 0}<p class="prose">No ready model assignment in Kitchen. <a href="/kitchen">Configure Kitchen →</a></p>{/if}
		{#each kitchen.data?.blockers ?? [] as blocker}
			<p class="finding"><span class="label member" data-state="failed">Blocked</span> <span class="prose">{blocker}</span></p>
		{/each}
		<p class="field">
			<label class="label" for="dispatch-planner">Planner</label>
			<span class="pick">
				<select class="plate" id="dispatch-planner" bind:value={planner} disabled={!kitchen.data?.models.length}>
					{#if !select(planner)}<option value={planner} disabled>No ready planner…</option>{/if}
					{@render models()}
				</select>
			</span>
		</p>
		{#each selectedRoles as role (role.ref)}
			<p class="field">
				<label class="label" for={`role-${role.ref}`}>{role.title || role.name}</label>
				<span class="pick">
					<select class="plate" id={`role-${role.ref}`} value={roleChoice(role)} onchange={(event) => (assigned = { ...assigned, [role.name]: event.currentTarget.value })}>
						{@render models()}
					</select>
				</span>
			</p>
		{/each}
		<p class="field">
			<label class="label" for="dispatch-cap">Plan token cap</label>
			<input class="plate" id="dispatch-cap" type="number" min="0" step="1" bind:value={cap} aria-describedby="cap-note" />
		</p>
		<p id="cap-note" class="req">Optional. Enforced at admission.</p>
	{:else}
		<dl class="readout plate">
			<div class="wide"><dt class="label">Brief</dt><dd class="text member" data-state={brief.trim() ? 'seated' : 'slack'}>{brief.trim() || '—'}</dd></div>
			<div class="wide"><dt class="label">Acceptance</dt><dd class="text">{acceptance.trim() || '—'}</dd></div>
			<div><dt class="label">Library</dt><dd class="value">{refs.length ? refs.join(', ') : 'No selected refs'}</dd></div>
			<div><dt class="label">Planner</dt><dd class="value member" data-state={select(planner) ? 'seated' : 'slack'}>{select(planner) ? planner : 'Not configured'}</dd></div>
			{#each selectedRoles as role (role.ref)}
				<div><dt class="label">{role.title || role.name}</dt><dd class="value member" data-state={select(roleChoice(role)) ? 'seated' : 'slack'}>{select(roleChoice(role)) ? roleChoice(role) : 'Not configured'}</dd></div>
			{/each}
			<div><dt class="label">Token cap</dt><dd class="value">{cap || 'None declared'}</dd></div>
		</dl>
		<p class="prose">Approval remains in Run. No worker starts here.</p>
		<!-- Live regions from first paint: present and empty until they speak. -->
		<p class="outcome member" data-state="balanced" role="status">{#if pending}<span class="label">Planning</span> with {planner} · {elapsed}s elapsed. No progress reported by daemon.{/if}</p>
		<p class="outcome member" data-state="failed" role="alert" tabindex="-1" bind:this={failure}>{#if error}<span class="label">Failed</span> <span class="prose">{error} · Your draft is preserved.</span> <button type="button" class="act plate" onclick={() => void submit()}>Try again</button>{/if}</p>
	{/if}

	<div class="foot">
		{#if step > 1}<button type="button" class="act plate" onclick={() => navigate(step - 1)}>← Back</button>{/if}
		<span class="spacer"></span>
		{#if step === 1 && !brief.trim()}<span class="req">Needs a brief</span>
		{:else if step === 4 && missing.length}<span class="req">Needs {missing.join(' · ')}</span>{/if}
		{#if step < 4}
			<button type="button" class="act plate" disabled={step === 1 && !brief.trim()} onclick={() => navigate(step + 1)}>Continue →</button>
		{:else}
			<button type="button" class="act plate" disabled={pending || missing.length > 0} onclick={() => void submit()}>{pending ? 'Planning…' : 'Create plan →'}</button>
		{/if}
	</div>
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

	/* --- the step rail ------------------------------------------------------- */
	.steps {
		position: relative;
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		list-style: none;
		margin: 2.25rem 0 0;
		padding: 0;
	}
	/* The member runs through: past the first seat and on past the last. */
	.steps::before {
		content: '';
		position: absolute;
		top: calc(0.3rem + 5px);
		left: -0.75rem;
		right: 0;
		height: 1px;
		background: var(--member-line);
	}
	.step {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		column-gap: 0.5rem;
		row-gap: 0.55rem;
		width: 100%;
		font: inherit;
		text-align: left;
		color: var(--ink-2);
		background: none;
		border: 0;
		padding: 0.3rem 0.75rem 0.3rem 0;
		cursor: pointer;
	}
	.ring {
		position: relative;
		grid-column: 1 / -1;
		width: 11px;
		height: 11px;
		border: 1.5px solid var(--member-line);
		border-radius: 50%;
		background: var(--plate);
	}
	.step[data-at='passed'] .ring {
		background: var(--seat);
		border-color: var(--seat);
	}
	.step[data-at='current'] {
		color: var(--ink);
	}
	.step[data-at='current'] .ring {
		border-color: var(--ink);
		box-shadow: 0 0 0 3px var(--plate), 0 0 0 4px var(--member-line);
	}
	.n {
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		line-height: 1.8;
	}
	.tab-name {
		font-weight: 500;
		line-height: 1.35;
	}
	.step:hover {
		color: var(--red);
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
	.prose + .prose,
	.headline + .prose {
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

	/* --- the plan readout ---------------------------------------------------- */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		margin: 0 0 1.25rem;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		flex: 1 1 11rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.75rem 1rem;
	}
	.readout > .wide {
		flex-basis: 100%;
	}
	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		overflow-wrap: anywhere;
		color: var(--member-ink, var(--ink));
	}
	dd.text {
		max-width: 68ch;
		white-space: pre-wrap;
	}
	dd[data-state='slack'] {
		color: var(--ink-2);
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
		.tab-name {
			font-size: 0.75rem;
		}
	}
	/* Four seats in a phone's width: the caption already says which step of four,
	   so the numeral goes and the name may break rather than run into its neighbour. */
	@media (max-width: 48rem) {
		.step {
			padding-right: 0.25rem;
		}
		.n {
			display: none;
		}
		.tab-name {
			font-size: 0.625rem;
			overflow-wrap: anywhere;
		}
	}
</style>
