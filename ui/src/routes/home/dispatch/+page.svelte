<script lang="ts">
	import { goto } from '$app/navigation';
	import { Resource } from '$lib/resource.svelte';
	import { daemon, type AssetSummary, type Kitchen, type KitchenAssignment, type LibraryIssue } from '$lib/daemon';
	import { activeAssets, assignmentKey, assignments, ready } from '$lib/dispatch';
	import Tabs, { panelId } from '$lib/Tabs.svelte';
	import RichSelect, { type RichOption } from '$lib/RichSelect.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import Button from '$lib/Button.svelte';
	import Icon from '$lib/Icon.svelte';
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
	let tab = $state<'launch' | 'roles' | 'budget'>('launch');
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
	/* An empty or stale planner (e.g. from an old draft) falls back to Kitchen's
	   default, else the first ready model — never a greyed button with no choice. */
	$effect(() => {
		if (!kitchen.data || select(planner)) return;
		const preferred = kitchen.data.defaults.planner;
		const fallback = preferred && ready(kitchen.data, preferred) ? preferred : catalog[0];
		if (fallback) planner = assignmentKey(fallback);
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
	let planId = $state('');
	let focusNote = $state('');
	async function focusPlanner() {
		try { await daemon.focusPlanner(planId); focusNote = ''; }
		catch (cause) { focusNote = cause instanceof Error ? cause.message : 'Pane not focusable.'; }
	}
	async function submit() {
		if (!kitchen.data || missing.length) return;
		pending = true; error = ''; elapsed = 0; focusNote = '';
		planId = `plan_${crypto.randomUUID().replaceAll('-', '')}`;
		const timer = setInterval(() => elapsed++, 1000);
		try {
			const plan = await daemon.createPlan({ plan_id: planId, brief, acceptance, assets: refs, planner: assignedFor(PLANNER_SLOT, planner), roles: Object.fromEntries(selectedRoles.map((role) => [role.name, assignedFor(roleSlot(role.name), roleChoice(role))])), token_cap: cap ? Number(cap) : null });
			localStorage.removeItem('herdsman-dispatch-draft');
			await goto(`/run?plan=${encodeURIComponent(plan.id)}&tab=plan`);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Planning failed.';
			queueMicrotask(() => failure?.focus());
		}
		finally { clearInterval(timer); pending = false; }
	}

	/* Every kitchen pair as a rich option; a pair whose harness is not ready is shown, not choosable. */
	const options = $derived<RichOption[]>((kitchen.data?.models ?? []).map((option) => {
		const readiness = kitchen.data?.readiness.find((item) => item.harness === option.harness);
		const ok = readiness?.state === 'ready';
		return { value: assignmentKey(option), label: assignmentKey(option), harness: option.harness, model: option.model,
			word: ok ? 'ready' : (readiness?.state ?? 'unknown'), wordTone: ok ? 'pass' : 'waiting', disabled: !ok };
	}));
	const levelOptions = (key: string) => poolOf(key).map((level) => ({ id: level, label: level }));
	function discard() {
		localStorage.removeItem('herdsman-dispatch-draft');
		void goto('/home');
	}

</script>

<svelte:head><title>New dispatch · Herdsman</title></svelte:head>

<div class="dispatch">
	<section class="ph">
		<div class="title">
			<h1 class="h-title">New dispatch</h1>
			<div class="meta"><span class="prose">Plan first. No worker starts until you approve the proposal in Run.</span></div>
		</div>
	</section>

	<div class="disp">
		<div class="form">
			<label class="field-l"><span class="lbl">Brief</span>
				<textarea class="ta brief" bind:value={brief} placeholder="What should the pipeline accomplish?" aria-describedby="brief-note" required></textarea>
				<span class="hint" id="brief-note">Required. The planner reads this as the whole task.</span>
			</label>
			<label class="field-l"><span class="lbl">Acceptance criteria</span>
				<textarea class="ta" bind:value={acceptance} placeholder="What will count as done?"></textarea>
			</label>
		</div>

		<aside class="launch" aria-label="Launch">
			<div class="tabbar">
				<Tabs small prefix="dp" label="Launch settings" selected={tab} onselect={(id) => (tab = id as typeof tab)}
					items={[{ id: 'launch', label: 'Launch' }, { id: 'roles', label: 'Roles', count: refs.length || undefined }, { id: 'budget', label: 'Budget' }]} />
			</div>
			<div class="lb" role="tabpanel" id={panelId('dp', tab)} aria-labelledby="dp-tab-{tab}">
				{#key tab}
				<div class="pane pane-in">
				{#if tab === 'launch'}
					<div class="field-l"><span class="lbl">Planner</span>
						<RichSelect label="Planner model" {options} value={select(planner) ? planner : null} placeholder={kitchen.data ? 'No ready planner…' : 'Reading Kitchen…'}
							disabled={pending || !options.length} onchange={(v) => { planner = v; modelChanged(PLANNER_SLOT); }} />
						{#if poolOf(planner).length > 0}
							<Segmented label={`Effort for ${planner}`} options={levelOptions(planner)} value={levelOf(PLANNER_SLOT, planner) ?? ''} onchange={(level) => pickEffort(PLANNER_SLOT, level)} />
						{/if}
						{#if kitchen.error}<span class="hint bad" role="alert">{kitchen.error.message}</span>{/if}
						{#if kitchen.data && catalog.length === 0}<span class="hint">No ready model assignment. <a href="/kitchen">Configure Kitchen</a></span>{/if}
						{#each kitchen.data?.blockers ?? [] as blocker}<span class="hint bad">{blocker}</span>{/each}
					</div>
					<div class="field-l"><span class="lbl">Branches</span>
						<div class="br-flow">
							<div class="field-l"><span class="hint">Start from</span><select class="sel" aria-label="Start from" disabled><option>—</option></select></div>
							<span class="arr"><Icon name="chevron-right" size={16} /></span>
							<div class="field-l"><span class="hint">Settle onto</span><select class="sel" aria-label="Settle onto" disabled><option>—</option></select></div>
						</div>
						<span class="hint">Branch choice arrives with the dispatch-branches change</span>
					</div>
					<label class="field-l"><span class="lbl">Token cap</span>
						<input class="inp mono" type="number" min="0" step="1" bind:value={cap} placeholder="—" />
						<span class="hint">Optional. Starts that would pass it are refused.</span>
					</label>
				{:else if tab === 'roles'}
					<label class="field-l"><span class="lbl">Filter choices</span><input class="inp" type="search" bind:value={query} placeholder="Roles and contracts" /></label>
					{#each [{ name: 'Roles', data: roles }, { name: 'Contracts', data: contracts }] as group (group.name)}
						{@const shown = activeAssets(group.data.data ?? [], group.name === 'Roles' ? 'role' : 'contract').filter((item) => `${item.title} ${item.ref}`.toLowerCase().includes(query.toLowerCase()))}
						{@const chosen = (group.data.data ?? []).filter((item) => refs.includes(item.ref)).length}
						<fieldset>
							<legend class="rule-h"><span class="lbl">{group.name}</span><span class="lbl r">{chosen} chosen</span></legend>
							{#if group.data.error}<p class="hint bad" role="alert">{group.data.error.message}</p>{/if}
							{#if group.data.phase === 'loading'}<p class="hint">Reading {group.name.toLowerCase()}…</p>{/if}
							{#if group.data.data?.length === 0}<p class="hint">No {group.name.toLowerCase()} in Library. Add one there first.</p>
							{:else if group.data.data && shown.length === 0}<p class="hint">Nothing matches “{query}”.</p>{/if}
							<ul class="choices">
								{#each shown as asset (asset.ref)}
									<li><label class="choice">
										<input type="checkbox" checked={refs.includes(asset.ref)} onchange={() => toggle(asset.ref)} />
										<span class="ellipsis">{asset.title || asset.name}</span>
										<span class="mono muted">{asset.tokens} tok</span>
									</label></li>
								{/each}
							</ul>
						</fieldset>
					{/each}
					{#each issues as issue}
						<p class="hint" class:bad={issue.severity === 'error'} role={issue.severity === 'error' ? 'alert' : 'status'}><b>{issue.message}</b> {issue.detail}</p>
					{/each}
					{#each selectedRoles as role (role.ref)}
						<div class="field-l"><span class="lbl">{role.title || role.name}</span>
							<RichSelect label={`Model for ${role.title || role.name}`} {options} value={roleChoice(role)} disabled={pending}
								onchange={(v) => { assigned = { ...assigned, [role.name]: v }; modelChanged(roleSlot(role.name)); }} />
							{#if poolOf(roleChoice(role)).length > 0}
								<Segmented label={`Effort for ${role.name}`} options={levelOptions(roleChoice(role))} value={levelOf(roleSlot(role.name), roleChoice(role)) ?? ''} onchange={(level) => pickEffort(roleSlot(role.name), level)} />
							{/if}
						</div>
					{/each}
					{#if !refs.length}<p class="hint">Uses every active role unless you choose roles above.</p>{/if}
				{:else}
					<label class="field-l"><span class="lbl">Token cap</span>
						<input class="inp mono" type="number" min="0" step="1" bind:value={cap} placeholder="—" />
						<span class="hint">Optional. Enforced at admission.</span>
					</label>
				{/if}
				</div>
				{/key}
			</div>
			<div class="go">
				<div class="status" aria-live="polite">
					{#if pending}
						<span class="state" data-tone="running" role="status">Planning · {planner} · {elapsed}s</span>
					{:else if missing.length}
						<span class="hint">Needs {missing.join(' · ')}</span>
					{/if}
				</div>
				<p class="status bad" role="alert" tabindex="-1" bind:this={failure}>{#if error}<span class="state" data-tone="failed">Failed</span> {error} · Your draft is preserved.{/if}</p>
				{#if pending}
					<p class="status">
						<Button small icon="square-terminal" onclick={() => void focusPlanner()}>Focus pane</Button>
						{#if focusNote}<span class="hint">{focusNote}</span>{/if}
					</p>
				{/if}
				<div class="btnrow">
					<Button icon="x" disabled={pending} onclick={discard}>Discard</Button>
					<span class="grow"></span>
					<Button kind="primary" icon="send-horizontal" busy={pending} disabled={missing.length > 0} onclick={() => void submit()}>Create plan</Button>
				</div>
			</div>
		</aside>
	</div>
</div>

<style>
	.dispatch { display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; height: 100%; }
	.ph .prose { font-size: 13px; }
	.disp { display: grid; grid-template-columns: minmax(0, 1fr) 420px; min-height: 0; }
	.form { overflow: auto; padding: 26px 32px 40px; display: grid; gap: 22px; align-content: start; }
	.brief { min-height: 200px; }
	.launch { border-left: 1px solid var(--ln); background: var(--p1); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-height: 0; }
	.tabbar { padding: 0 24px; border-bottom: 1px solid var(--ln); }
	.lb { overflow: auto; padding: 20px 24px; min-height: 0; }
	.pane { display: grid; gap: 20px; align-content: start; }
	.br-flow { display: grid; grid-template-columns: minmax(0, 1fr) 26px minmax(0, 1fr); gap: 8px; align-items: end; }
	.arr { display: grid; place-items: center; height: 34px; color: var(--dim); }
	.go { padding: 14px 24px 16px; border-top: 1px solid var(--ln); display: grid; gap: 8px; }
	.go .btnrow { margin: 0; }
	.grow { flex: 1; }
	.status { min-height: 18px; margin: 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
	.status:empty { display: none; }
	.bad { color: var(--l656); }
	fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
	legend { padding: 0; width: 100%; }
	.choices { list-style: none; padding: 0; margin: 0; display: grid; }
	.choice { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--ln); cursor: pointer; }
	.choice input { accent-color: var(--tx); margin: 0; }
	@media (max-width: 1023px) { .disp { grid-template-columns: minmax(0, 1fr); overflow: auto; } .launch { border-left: 0; border-top: 1px solid var(--ln); } }
</style>
