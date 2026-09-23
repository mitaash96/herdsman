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
	async function submit() {
		if (!kitchen.data || !brief.trim() || !select(planner) || issues.some((item) => item.severity === 'error') || selectedRoles.some((role) => !select(roleChoice(role)))) return;
		pending = true; error = ''; elapsed = 0;
		const timer = setInterval(() => elapsed++, 1000);
		try {
			const plan = await daemon.createPlan({ brief, acceptance, assets: refs, planner: select(planner)!, roles: Object.fromEntries(selectedRoles.map((role) => [role.name, select(roleChoice(role))!])), token_cap: cap ? Number(cap) : null });
			localStorage.removeItem('herdsman-dispatch-draft');
			await goto(`/run?plan=${encodeURIComponent(plan.id)}`);
		} catch (cause) { error = cause instanceof Error ? cause.message : 'Planning failed.'; }
		finally { clearInterval(timer); pending = false; }
	}
</script>

<div class="dispatch-sheet">
	<a href="/home">← Fleet</a>
	<h1>Dispatch</h1>
	<p>Plan first. No worker starts until you approve the proposal in Run.</p>
	<nav aria-label="Dispatch steps" class="steps">
		{#each ['Brief', 'Roles & contracts', 'Assignments', 'Plan'] as title, index}
			<button type="button" aria-current={step === index + 1 ? 'step' : undefined} onclick={() => navigate(index + 1)}>{index + 1}. {title}</button>
		{/each}
	</nav>
	{#if step === 1}
		<h2>1. Brief and acceptance</h2>
		<label for="dispatch-brief">Brief</label>
		<textarea id="dispatch-brief" rows="7" bind:value={brief} placeholder="What should the pipeline accomplish?"></textarea>
		<label for="dispatch-acceptance">Acceptance criteria</label>
		<textarea id="dispatch-acceptance" rows="4" bind:value={acceptance} placeholder="What will count as done?"></textarea>
	{:else if step === 2}
		<h2>2. Roles and contracts</h2>
		<p>These Library refs are frozen at approval. Warnings below come from Library validation.</p>
		<label for="asset-filter">Filter choices</label>
		<input id="asset-filter" type="search" bind:value={query} />
		{#each [{ name: 'Roles', data: roles }, { name: 'Contracts', data: contracts }] as group (group.name)}
			<fieldset>
				<legend>{group.name}</legend>
				{#if group.data.error}<p role="alert">{group.data.error.message}</p>{/if}
				{#if group.data.phase === 'loading'}<p>Reading {group.name.toLowerCase()}…</p>{/if}
				{#if group.data.data?.length === 0}<p>No {group.name.toLowerCase()} in Library. Add one there first.</p>{/if}
				<div class="choices">
					{#each activeAssets(group.data.data ?? [], group.name === 'Roles' ? 'role' : 'contract').filter((item) => `${item.title} ${item.ref}`.toLowerCase().includes(query.toLowerCase())) as asset (asset.ref)}
						<label><input type="checkbox" checked={refs.includes(asset.ref)} onchange={() => toggle(asset.ref)} /> {asset.title || asset.name} <span>{asset.ref} · {asset.tokens} tokens</span></label>
					{/each}
				</div>
			</fieldset>
		{/each}
		{#each issues as issue}<p class:warning={issue.severity === 'warning'} role={issue.severity === 'error' ? 'alert' : 'status'}>{issue.severity}: {issue.message} · {issue.detail}</p>{/each}
	{:else if step === 3}
		<h2>3. Assignments</h2>
		{#if kitchen.error}<p role="alert">{kitchen.error.message}</p>{/if}
		{#if kitchen.phase === 'loading'}<p>Reading Kitchen…</p>{/if}
		{#if catalog.length === 0}<p>No ready model assignment in Kitchen. <a href="/kitchen">Configure Kitchen →</a></p>{/if}
		{#if kitchen.data?.blockers.length}<p>{kitchen.data.blockers.join(' · ')}</p>{/if}
		<label for="dispatch-planner">Planner</label>
		<select id="dispatch-planner" bind:value={planner}>
			{#each kitchen.data?.models ?? [] as option (assignmentKey(option))}
				{@const readiness = kitchen.data?.readiness.find((item) => item.harness === option.harness)}
				<option value={assignmentKey(option)} disabled={readiness?.state !== 'ready'}>{option.harness} / {option.model} · {readiness?.state ?? 'unknown'}{readiness?.state !== 'ready' ? ` — ${readiness?.reason ?? 'Not ready'}` : ''}</option>
			{/each}
		</select>
		{#each selectedRoles as role (role.ref)}
			<label for={`role-${role.ref}`}>{role.title || role.name}</label>
			<select id={`role-${role.ref}`} value={roleChoice(role)} onchange={(event) => (assigned = { ...assigned, [role.name]: event.currentTarget.value })}>
				{#each kitchen.data?.models ?? [] as option (assignmentKey(option))}
					{@const readiness = kitchen.data?.readiness.find((item) => item.harness === option.harness)}
					<option value={assignmentKey(option)} disabled={readiness?.state !== 'ready'}>{option.harness} / {option.model} · {readiness?.state ?? 'unknown'}{readiness?.state !== 'ready' ? ` — ${readiness?.reason ?? 'Not ready'}` : ''}</option>
				{/each}
			</select>
		{/each}
		<label for="dispatch-cap">Plan token cap (optional · enforced at admission)</label>
		<input id="dispatch-cap" type="number" min="0" step="1" bind:value={cap} />
	{:else}
		<h2>4. Plan</h2>
		<p>Brief: {brief || '—'}</p><p>Acceptance: {acceptance || '—'}</p>
		<p>Library: {refs.join(', ') || 'No selected refs'}</p>
		<p>Planner: {planner || 'Not configured'}</p>
		<p>Approval remains in Run. No worker starts here.</p>
		<button type="button" disabled={pending || !brief.trim() || !select(planner) || issues.some((item) => item.severity === 'error')} onclick={() => void submit()}>{pending ? 'Planning…' : 'Create plan →'}</button>
		{#if pending}<p role="status">Planning with {planner} · {elapsed}s elapsed. No progress reported by daemon.</p>{/if}
		{#if error}<p role="alert" tabindex="-1">{error} · Your draft is preserved. <button type="button" onclick={() => void submit()}>Try again</button></p>{/if}
	{/if}
	<div class="foot">
		{#if step > 1}<button type="button" onclick={() => navigate(step - 1)}>← Back</button>{/if}
		{#if step < 4}<button type="button" disabled={step === 1 && !brief.trim()} onclick={() => navigate(step + 1)}>Continue →</button>{/if}
	</div>
</div>
<style>
	.dispatch-sheet { max-width: 56rem; margin: 0 auto; padding: 1rem 0 4rem; }
	h1 { margin: 1.5rem 0 .5rem; }
	h2 { font-size: 1.3rem; margin: 2.5rem 0 1rem; }
	.steps, .foot { display: flex; gap: .5rem; flex-wrap: wrap; border-top: 1px solid var(--rule); padding-top: 1rem; margin-top: 2rem; }
	.steps button[aria-current='step'] { border-color: var(--member-line); font-weight: 600; }
	button, select, textarea, input:not([type='checkbox']) { font: inherit; color: var(--ink); background: var(--plate); border: 1px solid var(--rule-strong); padding: .5rem .7rem; }
	button { cursor: pointer; }
	button:disabled { opacity: .55; cursor: not-allowed; }
	textarea, select, input:not([type='checkbox']) { display: block; width: 100%; max-width: 48rem; margin: .4rem 0 1.5rem; }
	label { display: block; }
	fieldset { border: 0; border-top: 1px solid var(--rule); margin: 1rem 0; padding: 1rem 0; }
	.choices { max-height: 16rem; overflow: auto; }
	.choices label { padding: .5rem 0; border-bottom: 1px solid var(--rule); }
	.choices span { color: var(--ink-2); font-size: .75rem; }
	.warning { color: var(--ink-2); }
</style>
