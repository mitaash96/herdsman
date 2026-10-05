<!--
KITCHEN — index spine + one pane (redesign U6; DS §9.16, §9.20, rules 8 and 9).

Behaviour is the old page's: one read, one form model (kitchen.ts), one
whole-document Save, a read-only Measure, one deliberate Test. Presentation
is a grouped Models table whose rows edit in place without moving anything,
Assignments, Fallbacks and Test a model.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import Button from '$lib/Button.svelte';
	import EffortLine from '$lib/EffortLine.svelte';
	import Icon from '$lib/Icon.svelte';
	import IconButton from '$lib/IconButton.svelte';
	import Mark from '$lib/Mark.svelte';
	import RichSelect, { type RichOption } from '$lib/RichSelect.svelte';
	import StateMark from '$lib/StateMark.svelte';
	import { harnessHue } from '$lib/marks';
	import { Resource } from '$lib/resource.svelte';
	import { useTitleActions } from '$lib/shell.svelte';
	import type { Tone } from '$lib/tones';
	import {
		daemon,
		DaemonError,
		type AssetSummary,
		type Kitchen,
		type KitchenAssignment,
		type KitchenCapabilities,
		type KitchenSmokeResult,
		type ReadinessState
	} from '$lib/daemon';
	import {
		SMOKE_TIMEOUT,
		absenceOf,
		classifySaveFailure,
		columnsOf,
		defaultEffort,
		editsDirty,
		editsFrom,
		effortEntry,
		effortLevels,
		effortPool,
		effortSelected,
		mergeRacedEdits,
		outcomeCopy,
		pairKey,
		rigReading,
		roleNamesFrom,
		savePayload,
		tierNames,
		type AdapterEdit,
		type ChainRow,
		type Column,
		type KitchenEdits,
		type ModelRow,
		type SaveFailure
	} from '$lib/kitchen';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	/* Role is a named thing: the Library's enumeration is the list the controls
	   choose from; a declared key outside it still renders but is not retyped. */
	const rolesResource = new Resource<AssetSummary[]>((signal) => daemon.libraryRoles(signal));

	type SectionId = 'models' | 'assignments' | 'fallbacks' | 'smoke';
	let section = $state<SectionId>('models');

	/* --- Measure -------------------------------------------------------------- */

	let probing = $state(false);
	let probedAt = $state<Date | null>(null);
	/* `POST /kitchen/discovery` is Sprint 8's; a daemon without it answers 404 and
	   the control becomes an honest unavailable action. */
	let probeRoute = $state<'present' | 'absent'>('present');
	/* One notice region at the top of the pane: a failed Measure or a refused
	   Save, in the daemon's words, focused when it appears. */
	let notice = $state<{ title: string; text: string } | null>(null);
	let noticeEl = $state<HTMLDivElement | null>(null);
	let saved = $state<{ cleared: boolean } | null>(null);

	const columns = $derived(kitchen.data ? columnsOf(kitchen.data) : []);
	const rig = $derived(rigReading(columns));
	const discoverable = $derived(kitchen.data?.discoverable ?? []);

	async function showNotice(title: string, text: string): Promise<void> {
		notice = { title, text };
		await tick();
		noticeEl?.focus();
	}

	/* One probe on first open, and only when the daemon holds no facts at all.
	   Not a poll: each probe starts a real process per declared harness. */
	let opened = false;
	$effect(() => {
		const view = kitchen.data;
		if (!view || opened) return;
		opened = true;
		if (view.configured && view.discovery.facts.length === 0) void probe();
	});

	$effect(() => {
		void kitchen.load();
		void rolesResource.load();
		return () => {
			kitchen.dispose();
			rolesResource.dispose();
		};
	});

	async function probe(): Promise<void> {
		if (probing) return;
		probing = true;
		notice = null;
		try {
			await daemon.probeKitchen();
			probedAt = new Date();
			/* The probe answers with the whole projection; the one Resource re-reads
			   it so exactly one thing on screen can be stale. */
			await kitchen.load();
		} catch (cause) {
			if (cause instanceof DaemonError && cause.kind === 'not_found') {
				probeRoute = 'absent';
				void showNotice(
					'Not measured',
					'This daemon serves no /kitchen/discovery route, so nothing here can be measured from the browser. Run a newer daemon to probe.'
				);
			} else {
				void showNotice(
					'Not measured',
					cause instanceof Error ? cause.message : 'The probe failed before it reported.'
				);
			}
		} finally {
			probing = false;
		}
	}

	const measured = (view: Kitchen): string => {
		if (!view.configured) return 'nothing to measure';
		if (view.discovery.facts.length === 0) return probing ? 'measuring now' : 'not measured';
		if (probedAt) return `measured ${probedAt.toLocaleTimeString()}`;
		return 'measured earlier this session';
	};

	/* `$EDITOR` is the operator's shell, not the browser's: the control hands over
	   the exact command. Honest — it never claims to open anything. */
	let copied = $state<'ok' | 'fail' | null>(null);
	async function copyEditCommand(): Promise<void> {
		try {
			await navigator.clipboard.writeText('$EDITOR .herdsman/kitchen.json');
			copied = 'ok';
		} catch {
			copied = 'fail';
		}
	}

	/* --- The form: adapters ---------------------------------------------------- */

	/** A form row: stored capabilities the operator may edit, plus any template
	 * field they have touched. Untouched fields never enter the payload. */
	interface SetupRow extends AdapterEdit {
		argvText: string;
		argvTouched: boolean;
		modelArgvText: string;
		modelArgvTouched: boolean;
		agentArgsText: string;
		agentArgsTouched: boolean;
	}

	function rowsFrom(view: Kitchen): SetupRow[] {
		return view.adapters.map((adapter) => ({
			name: adapter.name,
			capabilities: { ...adapter.capabilities },
			argv: null,
			argvText: '',
			argvTouched: false,
			model_argv: null,
			modelArgvText: '',
			modelArgvTouched: false,
			agent_args: null,
			agentArgsText: '',
			agentArgsTouched: false
		}));
	}

	/* A refused save's recovery (K2-D2): held rows are merged onto the fresh
	   document and the form keys are pre-seeded so the rebuild effects stand down
	   and nothing the operator typed is lost. */
	function mergeRacedRows(held: SetupRow[], prior: Kitchen, fresh: Kitchen): SetupRow[] {
		const freshRows = rowsFrom(fresh);
		const known = new Set(freshRows.map((row) => row.name));
		const merged = freshRows.map((row) => {
			const old = held.find((item) => item.name === row.name);
			if (old === undefined) return row;
			const stored = prior.adapters.find((adapter) => adapter.name === old.name);
			const touched =
				stored !== undefined &&
				JSON.stringify(old.capabilities) !== JSON.stringify(stored.capabilities);
			return {
				name: row.name,
				capabilities: touched ? old.capabilities : row.capabilities,
				argv: old.argv,
				argvText: old.argvText,
				argvTouched: old.argvTouched,
				model_argv: old.model_argv,
				modelArgvText: old.modelArgvText,
				modelArgvTouched: old.modelArgvTouched,
				agent_args: old.agent_args,
				agentArgsText: old.agentArgsText,
				agentArgsTouched: old.agentArgsTouched
			};
		});
		const carried = held.filter((old) => {
			if (known.has(old.name)) return false;
			const stored = prior.adapters.find((adapter) => adapter.name === old.name);
			return (
				stored === undefined ||
				old.argvTouched ||
				old.modelArgvTouched ||
				old.agentArgsTouched ||
				JSON.stringify(old.capabilities) !== JSON.stringify(stored.capabilities)
			);
		});
		return [...merged, ...carried];
	}

	const blankCaps = (): KitchenCapabilities => ({
		structured_output: 'unknown',
		resume: 'unknown',
		usage: 'unknown',
		pty: 'unknown',
		memory: null
	});

	let rows = $state<SetupRow[]>([]);
	let formKey = '';
	$effect(() => {
		const view = kitchen.data;
		if (!view) return;
		const key = view.adapters.map((adapter) => adapter.name).join('\n');
		if (key === formKey) return;
		formKey = key;
		rows = rowsFrom(view);
	});

	/* --- The form: models, assignments, chains (K3) ----------------------------- */

	let k3 = $state<KitchenEdits>({ models: [], planner: null, initiative: null, roles: [], chains: [] });
	/* Rebuilt only when the document's identity sets change, so a probe or a smoke
	   refresh cannot wipe what the operator typed. */
	const k3Identity = (view: Kitchen): string =>
		JSON.stringify([
			view.models.map(pairKey),
			Object.keys(view.defaults.roles),
			view.fallbacks.map((chain) => pairKey(chain.primary))
		]);
	let k3Key = '';
	$effect(() => {
		const view = kitchen.data;
		if (!view) return;
		const key = k3Identity(view);
		if (key === k3Key) return;
		k3Key = key;
		k3 = editsFrom(view);
	});

	const catalogPairs = $derived(k3.models.map((row) => pairKey(row)));
	const tierOptions = $derived.by(() => {
		const set = new Set(kitchen.data ? tierNames(kitchen.data) : []);
		for (const row of k3.models) if (row.tierTouched && row.tierValue !== '') set.add(row.tierValue);
		return [...set];
	});
	const roleNames = $derived(rolesResource.data ? roleNamesFrom(rolesResource.data) : []);
	const roleRows = $derived.by(() => {
		const names = [...roleNames];
		for (const row of k3.roles) if (!names.includes(row.role)) names.push(row.role);
		/* planner and initiative are seats of their own, listed first; a Library role
		   that shares the name is shown only if it actually carries a default. */
		return names
			.map((role) => k3.roles.find((row) => row.role === role) ?? { role, assignment: null, stored: false })
			.filter((row) => !['planner', 'initiative'].includes(row.role) || row.assignment !== null);
	});

	function parsePair(value: string): { harness: string; model: string } | null {
		const cut = value.indexOf('/');
		/* Harness names never carry a slash: the first one separates, even when the
		   model name carries more. */
		return value === '' || cut <= 0 ? null : { harness: value.slice(0, cut), model: value.slice(cut + 1) };
	}

	const dirtyAdapters = $derived(
		rows.some((row) => {
			const stored = kitchen.data?.adapters.find((adapter) => adapter.name === row.name);
			return (
				row.argvTouched ||
				row.modelArgvTouched ||
				row.agentArgsTouched ||
				stored === undefined ||
				JSON.stringify(stored.capabilities) !== JSON.stringify(row.capabilities)
			);
		}) || (kitchen.data?.adapters.some((a) => !rows.some((row) => row.name === a.name)) ?? false)
	);
	const anyDirty = $derived(
		dirtyAdapters || (kitchen.data !== null ? editsDirty(kitchen.data, k3) : false)
	);

	/* The header's "n changes": one per changed thing, counted against the read. */
	const changes = $derived.by(() => {
		const view = kitchen.data;
		if (!view) return 0;
		const json = (value: unknown): string => JSON.stringify(value ?? null);
		let n = 0;
		for (const row of k3.models) {
			if (!row.stored) {
				n++;
				continue;
			}
			const pair = pairKey(row);
			const levels = effortLevels(view, pair);
			const tierMoved = row.tierTouched && row.tierValue !== (view.tiers[pair] ?? '');
			const effortMoved =
				row.effortTouched &&
				json(effortEntry(levels, row.effortValue)) !==
					json(effortEntry(levels, effortSelected(view, pair)));
			if (tierMoved || effortMoved) n++;
		}
		n += view.models.filter((m) => !k3.models.some((row) => pairKey(row) === pairKey(m))).length;
		const was = view.defaults;
		if (json(k3.planner) !== json(was.planner)) n++;
		if (json(k3.initiative) !== json(was.initiative)) n++;
		const roleKeys = new Set([...Object.keys(was.roles), ...k3.roles.filter((r) => r.assignment).map((r) => r.role)]);
		for (const key of roleKeys) {
			const now = k3.roles.find((r) => r.role === key)?.assignment ?? null;
			if (json(now) !== json(was.roles[key] ?? null)) n++;
		}
		const chainPayload = (c: { primary: unknown; candidates: unknown[] }) => json([c.primary, c.candidates]);
		const shared = Math.min(k3.chains.length, view.fallbacks.length);
		for (let i = 0; i < shared; i++) if (chainPayload(k3.chains[i]) !== chainPayload(view.fallbacks[i])) n++;
		n += Math.abs(k3.chains.length - view.fallbacks.length);
		for (const row of rows) {
			const stored = view.adapters.find((a) => a.name === row.name);
			if (
				stored === undefined ||
				row.argvTouched ||
				row.modelArgvTouched ||
				row.agentArgsTouched ||
				json(stored.capabilities) !== json(row.capabilities)
			)
				n++;
		}
		n += view.adapters.filter((a) => !rows.some((row) => row.name === a.name)).length;
		return anyDirty ? Math.max(n, 1) : n;
	});

	/* --- Save ------------------------------------------------------------------ */

	let saving = $state(false);

	const failureText = (failure: SaveFailure): string => {
		switch (failure.kind) {
			case 'race':
				return `This project's kitchen changed since this form was read, so the daemon refused the save rather than overwrite what it now holds. Nothing was written. Your entries are kept; reload the current declaration and apply them again.\n${failure.detail}`;
			case 'invalid':
				return `${failure.detail}\nNothing was written.`;
			case 'absent':
				return 'This daemon serves no save route, so this form cannot write the declaration. Run a newer daemon to save.';
			default:
				return failure.detail;
		}
	};

	function parseTemplate(text: string): string[] | null {
		try {
			const value: unknown = JSON.parse(text);
			if (Array.isArray(value) && value.every((element) => typeof element === 'string'))
				return value as string[];
		} catch {
			/* not JSON — refused, never guessed at */
		}
		return null;
	}

	async function save(): Promise<void> {
		const view = kitchen.data;
		if (!view || saving) return;
		/* Every touched template is parsed before anything is sent. */
		const edits: AdapterEdit[] = [];
		for (const row of rows) {
			const argv = row.argvTouched ? parseTemplate(row.argvText) : null;
			const modelArgv = row.modelArgvTouched ? parseTemplate(row.modelArgvText) : null;
			const agentArgs = row.agentArgsTouched ? parseTemplate(row.agentArgsText) : null;
			if (
				(row.argvTouched && argv === null) ||
				(row.modelArgvTouched && modelArgv === null) ||
				(row.agentArgsTouched && agentArgs === null)
			) {
				void showNotice(
					'Not written',
					(row.argvTouched && argv === null) || (row.modelArgvTouched && modelArgv === null)
						? 'A launch template must be a JSON array of strings with one {prompt} element, for example ["claude", "-p", "{prompt}"].\nNothing was written.'
						: 'Agent args must be a JSON array of strings, for example ["--permission-mode", "auto"].\nNothing was written.'
				);
				return;
			}
			edits.push({ name: row.name, capabilities: row.capabilities, argv, model_argv: modelArgv, agent_args: agentArgs });
		}
		saving = true;
		notice = null;
		saved = null;
		editing = null;
		try {
			const resultsBefore = view.smoke.results.length;
			const fresh = await daemon.saveKitchen(savePayload(view, edits, k3, view.revision));
			rows = rowsFrom(fresh);
			formKey = fresh.adapters.map((adapter) => adapter.name).join('\n');
			k3 = editsFrom(fresh);
			k3Key = k3Identity(fresh);
			saved = { cleared: resultsBefore > 0 };
			smokeOutcome = null;
			await kitchen.load();
		} catch (cause) {
			const failure: SaveFailure =
				cause instanceof DaemonError
					? classifySaveFailure(cause)
					: {
							kind: 'unknown',
							detail: cause instanceof Error ? cause.message : 'The save failed before it was sent.'
						};
			void showNotice('Not written', failureText(failure));
			if (failure.kind === 'race') {
				const prior = view;
				const held = rows;
				const heldEdits = k3;
				await kitchen.load();
				const fresh = kitchen.data;
				if (fresh) {
					rows = mergeRacedRows(held, prior, fresh);
					formKey = fresh.adapters.map((adapter) => adapter.name).join('\n');
					k3 = mergeRacedEdits(heldEdits, prior, fresh);
					k3Key = k3Identity(fresh);
				}
			}
		} finally {
			saving = false;
		}
	}

	/* --- Edit in place: one row at a time ---------------------------------------- */

	/** `m:harness/model` · `h:harness` · `a:seat` · `c:index` */
	let editing = $state<string | null>(null);
	let snap: unknown = null;
	/* Rows whose tier control is in "declare a new name" mode. */
	let declareTiers = $state<Record<string, boolean>>({});

	function beginEdit(key: string, snapshot: unknown): void {
		if (editing !== null && editing !== key) editing = null; // implicit Done
		editing = key;
		snap = JSON.parse(JSON.stringify(snapshot ?? null));
	}
	const done = (): void => {
		editing = null;
		saved = null;
	};

	function cancelEdit(): void {
		const key = editing;
		editing = null;
		if (key === null) return;
		const [kind, id] = [key[0], key.slice(2)];
		const old = snap as Record<string, unknown> | null;
		if (kind === 'm') {
			const row = k3.models.find((r) => pairKey(r) === id);
			if (row && old) Object.assign(row, old);
			delete declareTiers[id];
		} else if (kind === 'h') {
			const row = rows.find((r) => r.name === id);
			if (row && old) row.capabilities = old as unknown as KitchenCapabilities;
		} else if (kind === 'a') {
			setSeat(id, (snap as KitchenAssignment | null) ?? null);
		} else if (kind === 'c') {
			const index = Number(id);
			if (old && k3.chains[index]) k3.chains[index] = old as unknown as ChainRow;
		}
	}

	function onPaneKey(event: KeyboardEvent): void {
		if (event.key === 'Escape' && editing !== null && !event.defaultPrevented) {
			event.preventDefault();
			cancelEdit();
		}
	}

	/* --- Models ---------------------------------------------------------------- */

	interface Group {
		harness: string;
		column: Column | null;
		models: ModelRow[];
	}
	const groups = $derived.by((): Group[] => {
		const names = columns.map((c) => c.harness);
		for (const row of k3.models) if (!names.includes(row.harness)) names.push(row.harness);
		for (const row of rows) if (!names.includes(row.name)) names.push(row.name);
		return names.map((harness) => ({
			harness,
			column: columns.find((c) => c.harness === harness) ?? null,
			models: k3.models.filter((m) => m.harness === harness)
		}));
	});

	const READY_TONE: Record<ReadinessState, { tone: Tone | 'pass'; word: string }> = {
		ready: { tone: 'pass', word: 'Ready' },
		degraded: { tone: 'waiting', word: 'Degraded' },
		unavailable: { tone: 'failed', word: 'Unavailable' },
		unknown: { tone: 'idle', word: 'Unknown' },
		unconfigured: { tone: 'idle', word: 'Not declared' }
	};

	const versionOf = (view: Kitchen, group: Group): string =>
		group.column?.observed?.version ??
		view.readiness.find((r) => r.harness === group.harness)?.version ??
		'—';

	function resolvedTier(view: Kitchen, row: ModelRow): string {
		return row.tierValue || view.models.find((m) => pairKey(m) === pairKey(row))?.tier || '';
	}

	function removeModel(row: ModelRow): void {
		k3.models = k3.models.filter((item) => pairKey(item) !== pairKey(row));
		delete declareTiers[pairKey(row)];
		saved = null;
	}

	function removeHarness(name: string): void {
		rows = rows.filter((r) => r.name !== name);
		k3.models = k3.models.filter((m) => m.harness !== name);
		saved = null;
	}

	function setTier(row: ModelRow, value: string): void {
		row.tierValue = value;
		row.tierTouched = true;
		saved = null;
	}

	const CAP_LABEL = { structured_output: 'Structured output', resume: 'Resume', usage: 'Usage', pty: 'Needs a PTY' } as const;
	const CAP_SHORT = { structured_output: 'OUT', resume: 'RES', usage: 'USE', pty: 'PTY' } as const;
	const CAP_KEYS = ['structured_output', 'resume', 'usage', 'pty'] as const;

	const hintText = $derived.by(() => {
		if (editing === null)
			return section === 'assignments'
				? 'Edit a row to change its pair and effort.'
				: 'Edit a row to change its tier and effort pool.';
		const id = editing.slice(2);
		if (editing[0] === 'm') {
			const row = k3.models.find((r) => pairKey(r) === id);
			const levels = kitchen.data ? effortLevels(kitchen.data, id) : [];
			if (levels.length === 0) return `Editing ${id} · this harness exposes no effort control.`;
			const n = row?.effortValue.length ?? 0;
			return `Editing ${id} · ${n} effort level${n === 1 ? '' : 's'} selected — launches use the highest unless a role sets one. Save writes kitchen.json.`;
		}
		if (editing[0] === 'h') return `Editing ${id} · declared capabilities. Done keeps them in the form; Save writes kitchen.json.`;
		return `Editing ${id.replace(/^role:/, '')} · pick a pair and an effort. Done keeps it in the form; Save writes kitchen.json.`;
	});

	/* Add harness / Declare model: one inline form row at the top of the table. */
	let draft = $state<{ name: string; argvText: string; modelArgvText: string } | null>(null);
	let draftHint = $state('');
	let addError = $state<string | null>(null);
	let newModel = $state<{ harness: string; model: string } | null>(null);
	let newModelError = $state<string | null>(null);

	function addRow(): void {
		if (draft === null) return;
		const name = draft.name.trim();
		const templateMessage =
			'A launch template must be a JSON array of strings with one {prompt} element, for example ["claude", "-p", "{prompt}"].';
		const argv = parseTemplate(draft.argvText);
		const modelArgv = draft.modelArgvText.trim() === '' ? [] : parseTemplate(draft.modelArgvText);
		if (name === '') {
			addError = 'A new harness needs a name — the name this project declares it under.';
			return;
		}
		if (rows.some((row) => row.name === name)) {
			addError = `${name} is already in this form.`;
			return;
		}
		if (argv === null || argv.length < 2 || argv.filter((element) => element === '{prompt}').length !== 1 || modelArgv === null) {
			addError = templateMessage;
			return;
		}
		rows = [
			...rows,
			{
				name,
				capabilities: blankCaps(),
				argv,
				argvText: draft.argvText,
				argvTouched: true,
				model_argv: modelArgv,
				modelArgvText: draft.modelArgvText,
				modelArgvTouched: true,
				agent_args: null,
				agentArgsText: '',
				agentArgsTouched: false
			}
		];
		draft = null;
		draftHint = '';
		addError = null;
		saved = null;
	}

	function addModel(): void {
		if (newModel === null) return;
		const name = newModel.model.trim();
		if (name === '') {
			newModelError = 'A declared model needs its name — the name this project gives it.';
			return;
		}
		const candidate = { harness: newModel.harness, model: name };
		if (catalogPairs.includes(pairKey(candidate))) {
			newModelError = `${pairKey(candidate)} is already in this catalog.`;
			return;
		}
		k3.models = [
			...k3.models,
			{
				...candidate,
				source: 'declared',
				usage: 'unknown',
				counting: 'unknown',
				price: null,
				tierValue: '',
				tierTouched: false,
				effortValue: [],
				effortTouched: false,
				stored: false
			}
		];
		newModel = null;
		newModelError = null;
		saved = null;
	}

	async function addDiscovered(harness: string, executable: string): Promise<void> {
		newModel = null;
		draft = { name: harness, argvText: '', modelArgvText: '' };
		draftHint = executable;
		addError = null;
		await tick();
		document.getElementById('add-argv')?.focus();
	}

	/* --- Assignments -------------------------------------------------------------- */

	interface Seat {
		key: string;
		label: string;
		assignment: KitchenAssignment | null;
		/** What a seat with no assignment inherits, in words. */
		inherits: string;
		clearable: boolean;
	}
	const seats = $derived.by((): Seat[] => [
		{ key: 'planner', label: 'planner', assignment: k3.planner, inherits: 'no planner configured', clearable: k3.planner !== null },
		{ key: 'initiative', label: 'initiative', assignment: k3.initiative, inherits: 'no executor configured', clearable: k3.initiative !== null },
		...roleRows.map((row) => ({
			key: `role:${row.role}`,
			label: row.role,
			assignment: row.assignment as KitchenAssignment | null,
			inherits: k3.initiative ? `inherits initiative · ${pairKey(k3.initiative)}` : 'inherits initiative default',
			clearable: row.assignment !== null
		}))
	]);

	function setSeat(key: string, pair: KitchenAssignment | null): void {
		const seat = key.startsWith('role:') ? key.slice(5) : key;
		if (key === 'planner') k3.planner = pair;
		else if (key === 'initiative') k3.initiative = pair;
		else if (pair === null) k3.roles = k3.roles.filter((item) => item.role !== seat);
		else {
			const row = k3.roles.find((item) => item.role === seat);
			if (row) row.assignment = pair;
			else k3.roles = [...k3.roles, { role: seat, assignment: pair, stored: false }];
		}
		saved = null;
	}

	const tierOf = (view: Kitchen, a: KitchenAssignment | null): string => {
		if (!a) return '—';
		const row = k3.models.find((m) => pairKey(m) === pairKey(a));
		return (row && resolvedTier(view, row)) || '—';
	};
	const effortOf = (view: Kitchen, a: KitchenAssignment | null): string => {
		if (!a) return '—';
		return a.effort ?? defaultEffort(effortPool(view, pairKey(a))) ?? '—';
	};

	/* --- Fallbacks ------------------------------------------------------------------ */

	/* A candidate already in this chain, or the primary, cannot be picked twice
	   here. Escalation, cycles and pairs outside the catalog are the daemon's
	   refusals on save and are never mirrored. */
	const candidateOptions = (chain: ChainRow, own?: number): string[] =>
		catalogPairs.filter(
			(pair) =>
				pair !== pairKey(chain.primary) &&
				!chain.candidates.some(
					(candidate, index) => (own === undefined || index !== own) && pairKey(candidate) === pair
				)
		);

	function moveCandidate(chain: ChainRow, from: number, to: number): void {
		const next = [...chain.candidates];
		const [moved] = next.splice(from, 1);
		if (moved === undefined) return;
		next.splice(to, 0, moved);
		chain.candidates = next;
	}

	const freePrimary = $derived(
		catalogPairs.find((pair) => !k3.chains.some((chain) => pairKey(chain.primary) === pair)) ?? null
	);

	function addChain(): void {
		const primary = freePrimary === null ? null : parsePair(freePrimary);
		if (primary === null) return;
		k3.chains = [...k3.chains, { primary, candidates: [], stored: false }];
		beginEdit(`c:${k3.chains.length - 1}`, { primary, candidates: [], stored: false });
	}

	/* --- Test a model ----------------------------------------------------------------- */

	let smokeHarness = $state('');
	let smokeModel = $state('');
	let smokeRunning = $state(false);
	let smokeRoute = $state<'present' | 'absent'>('present');
	let smokeNotice = $state<string | null>(null);
	let smokeOutcome = $state<{ harness: string; model: string; result: KitchenSmokeResult } | null>(null);

	/* The pair picks itself from what exists and repairs itself when the catalog
	   changes — a removed model never leaves the select pointing at it. */
	$effect(() => {
		const view = kitchen.data;
		if (!view) return;
		if (view.models.some((m) => m.harness === smokeHarness && m.model === smokeModel)) return;
		const first = view.models[0];
		smokeHarness = first?.harness ?? '';
		smokeModel = first?.model ?? '';
	});

	const smokeOptions = $derived.by((): RichOption[] =>
		(kitchen.data?.models ?? []).map((m) => {
			const ready = kitchen.data?.readiness.find((r) => r.harness === m.harness)?.state;
			return {
				value: pairKey(m),
				label: pairKey(m),
				harness: m.harness,
				model: m.model,
				word: ready ? READY_TONE[ready].word : undefined,
				wordTone: ready === 'ready' ? 'pass' : ready === 'unavailable' ? 'failed' : 'waiting'
			};
		})
	);

	const liveOutcome = $derived.by(() => {
		const live = smokeOutcome;
		const view = kitchen.data;
		if (!live || !view) return live;
		return view.smoke.results.some(
			(r) => r.harness === live.harness && r.model === live.model && r.at === live.result.at
		)
			? null
			: live;
	});

	async function runSmoke(): Promise<void> {
		if (smokeRunning) return;
		smokeRunning = true;
		smokeNotice = null;
		try {
			const result = await daemon.smokeKitchen(smokeHarness, smokeModel, undefined, SMOKE_TIMEOUT);
			smokeOutcome = { harness: result.harness, model: result.model, result };
			await kitchen.load();
		} catch (cause) {
			if (cause instanceof DaemonError && cause.kind === 'not_found') smokeRoute = 'absent';
			else smokeNotice = cause instanceof Error ? cause.message : 'The test failed before it was sent.';
		} finally {
			smokeRunning = false;
		}
	}

	const sortedResults = (results: KitchenSmokeResult[]): KitchenSmokeResult[] =>
		[...results].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));

	const SMOKE_WORD: Record<KitchenSmokeResult['state'], string> = {
		passed: 'Answered',
		failed: 'No answer',
		refused: 'Refused',
		timed_out: 'Timed out'
	};
	const SMOKE_TONE: Record<KitchenSmokeResult['state'], Tone | 'pass'> = {
		passed: 'pass',
		failed: 'failed',
		refused: 'waiting',
		timed_out: 'waiting'
	};
	const USAGE_WORD = { supported: 'usage declared', unsupported: 'usage unsupported', unknown: 'usage undeclared' } as const;

	/* --- Header + index --------------------------------------------------------------- */

	const headline = $derived.by(() => {
		if (kitchen.phase === 'error') return { tone: 'failed' as const, word: 'Not answering' };
		if (!kitchen.data) return { tone: 'idle' as const, word: 'Reading' };
		if (kitchen.stale) return { tone: 'waiting' as const, word: 'Stale' };
		if (!kitchen.data.configured) return { tone: 'idle' as const, word: 'Nothing declared' };
		return {
			tone: (rig.unavailable > 0 ? 'failed' : kitchen.data.ready ? 'pass' : 'waiting') as Tone | 'pass',
			word: `${rig.ready} of ${rig.declared} ready`
		};
	});

	const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;
	const index = $derived.by(() => {
		const view = kitchen.data;
		const harnessCount = groups.filter((g) => g.models.length > 0 || rows.some((r) => r.name === g.harness)).length;
		const assigned = seats.filter((s) => s.assignment !== null).length;
		const results = view?.smoke.results.length ?? 0;
		const toAdd = discoverable.length > 0 ? ` · ${discoverable.length} to add` : '';
		return [
			{
				id: 'models' as const,
				label: 'Models',
				summary:
					k3.models.length === 0 && rows.length === 0
						? 'none declared'
						: `${plural(harnessCount, 'harness', 'harnesses')} · ${plural(k3.models.length, 'model')}${toAdd}`
			},
			{
				id: 'assignments' as const,
				label: 'Assignments',
				summary: assigned === 0 ? 'none declared' : `${plural(assigned, 'role')} assigned`
			},
			{
				id: 'fallbacks' as const,
				label: 'Fallbacks',
				summary: k3.chains.length === 0 ? 'none declared' : plural(k3.chains.length, 'chain')
			},
			{
				id: 'smoke' as const,
				label: 'Test a model',
				summary: results === 0 ? 'not run this session' : `${plural(results, 'result')} this session`
			}
		];
	});
	const ABSENT = /^(none|not run)/;
	const sectionIndex = $derived(Math.max(0, index.findIndex((e) => e.id === section)));

	useTitleActions(() => titleActions);
</script>

{#snippet titleActions()}
	{#if probeRoute === 'absent'}
		<span class="state" data-tone="waiting">Measure unavailable</span>
	{:else}
		<Button
			icon="refresh-cw"
			small
			busy={probing}
			disabled={!kitchen.data?.configured}
			title="Resolve each added executable and run one bounded --version; nothing is written."
			onclick={() => void probe()}>Measure</Button
		>
	{/if}
{/snippet}

{#snippet actions(key: string, onEdit: () => void, onDelete: (() => void) | null, editLabel: string, deleteLabel: string, reason = '')}
	<span class="ibar">
		{#if editing === key}
			<IconButton icon="check" label="Done" small onclick={done} />
			<IconButton icon="x" label="Cancel" small onclick={cancelEdit} />
		{:else}
			<IconButton icon="pencil" label={editLabel} small onclick={onEdit} />
			<IconButton
				icon="trash-2"
				label={deleteLabel}
				small
				danger
				disabled={onDelete === null}
				{reason}
				onclick={() => onDelete?.()}
			/>
		{/if}
	</span>
{/snippet}

{#snippet pairCell(a: KitchenAssignment)}
	<Mark harness={a.harness} size={16} /><span>{a.harness}</span>
	<Icon name="chevron-right" size={13} />
	<Mark model={a.model} size={16} /><span class="mono ellipsis">{a.model}</span>
{/snippet}

<svelte:window onkeydown={onPaneKey} />

<section class="kitchen">
	<AsyncField resource={kitchen} reading="the kitchen" onretry={() => void kitchen.load()}>
		{#snippet children(view: Kitchen)}
			<header class="ph">
				<div class="title">
					<h1 class="h-title">Kitchen</h1>
					<div class="meta">
						<StateMark tone={headline.tone} word={headline.word} />
						<span class="mono muted">.herdsman/kitchen.json · rev {view.revision.slice(0, 8) || '—'}</span>
						<span class="lbl" role="status">{measured(view)}</span>
						{#if saved}
							<span class="state" data-tone="pass" role="status"
								>{saved.cleared ? 'Saved · test results cleared' : 'Saved'}</span
							>
						{/if}
						{#if copied}
							<span class="state" data-tone={copied === 'ok' ? 'pass' : 'waiting'} role="status"
								>{copied === 'ok' ? 'Command copied' : 'Copy blocked — run $EDITOR .herdsman/kitchen.json'}</span
							>
						{/if}
					</div>
				</div>
				<div class="acts">
					<Button
						icon="pencil"
						title="Copies `$EDITOR .herdsman/kitchen.json` — the browser cannot launch your editor."
						onclick={() => void copyEditCommand()}>Edit kitchen.json</Button
					>
					<Button
						icon="check"
						kind="primary"
						busy={saving}
						disabled={changes === 0}
						onclick={() => void save()}
						>{changes === 0 ? 'Save' : `Save · ${plural(changes, 'change')}`}</Button
					>
				</div>
			</header>

			<div class="kit">
				<nav class="spine" aria-label="Kitchen sections">
					<i class="hl" style="transform: translateY({sectionIndex * 76}px)" aria-hidden="true"></i>
					{#each index as entry (entry.id)}
						<button
							type="button"
							class="ix"
							aria-current={section === entry.id ? 'true' : undefined}
							aria-controls="kitchen-pane"
							onclick={() => {
								if (section === entry.id) return;
								editing = null;
								section = entry.id;
							}}
						>
							<span class="s">{entry.label}</span>
							<span class="sum" class:absent={ABSENT.test(entry.summary)}>{entry.summary}</span>
						</button>
					{/each}
					<div class="prov">
						Declared in .herdsman/kitchen.json. Discovery reads PATH with which; nothing is run until
						you Measure.
						{#each view.notes as note (note)}<p>{note}</p>{/each}
					</div>
				</nav>

				<div class="kpane" id="kitchen-pane">
					{#key section}
						<div class="pane-in">
							{#if notice}
								<div bind:this={noticeEl} class="stmt notice" data-tone="failed" role="alert" tabindex="-1">
									<b>{notice.title}</b>
									<p class="mono">{notice.text}</p>
								</div>
							{/if}

							{#if section === 'models'}
								{#if view.blockers.length > 0}
									<ul class="blockers">
										{#each view.blockers as blocker (blocker)}
											<li class="stmt" data-tone="failed"><p>{blocker}</p></li>
										{/each}
									</ul>
								{/if}
								<div class="ktool">
									<div class="rule-h">
										<span class="lbl">Harnesses &amp; models</span><span class="lbl r">declared in kitchen.json</span>
									</div>
									<Button
										icon="plus"
										small
										onclick={() => {
											newModel = null;
											draft = { name: '', argvText: '', modelArgvText: '' };
											draftHint = '';
											addError = null;
										}}>Add harness</Button
									>
									<Button
										icon="plus"
										small
										disabled={rows.length === 0}
										title={rows.length === 0 ? 'A pair needs its harness first — add one.' : undefined}
										onclick={() => {
											draft = null;
											newModel = { harness: rows[0]?.name ?? '', model: '' };
											newModelError = null;
										}}>Declare model</Button
									>
								</div>
								<p class="ehint ellipsis" role="status">
									{#if editing !== null}<Icon name="pencil" size={13} />{/if}{hintText}
								</p>

								<table class="tbl mt">
									<colgroup><col /><col style="width:150px" /><col style="width:400px" /><col style="width:76px" /></colgroup>
									<thead>
										<tr><th>Model</th><th>Tier</th><th>Effort pool</th><th><span class="sr">Actions</span></th></tr>
									</thead>
									<tbody>
										{#if draft !== null}
											<tr class="form">
												<td colspan="4">
													<div class="frm">
														<label class="field-l f-name">
															<span class="lbl">Harness name</span>
															<input class="inp" list="disc-names" bind:value={draft.name} placeholder="claude" />
															<datalist id="disc-names">
																{#each discoverable as found (found.harness)}<option value={found.harness}></option>{/each}
															</datalist>
														</label>
														<label class="field-l f-argv">
															<span class="lbl">Launch template</span>
															<input
																id="add-argv"
																class="inp mono"
																bind:value={draft.argvText}
																placeholder={draftHint ? `["${draftHint}", …, "{prompt}"]` : '["claude", "-p", "{prompt}"]'}
															/>
														</label>
														<label class="field-l f-flag">
															<span class="lbl">Model flag(s)</span>
															<input class="inp mono" bind:value={draft.modelArgvText} placeholder={'["--model"]'} />
														</label>
														<span class="ibar">
															<IconButton icon="check" label="Add to form" small onclick={addRow} />
															<IconButton
																icon="x"
																label="Cancel"
																small
																onclick={() => {
																	draft = null;
																	addError = null;
																}}
															/>
														</span>
													</div>
													{#if addError}<p class="err" role="alert">{addError}</p>{/if}
												</td>
											</tr>
										{/if}
										{#if newModel !== null}
											<tr class="form">
												<td colspan="4">
													<div class="frm">
														<label class="field-l f-name">
															<span class="lbl">Harness</span>
															<select class="sel" bind:value={newModel.harness}>
																{#each rows as r (r.name)}<option value={r.name}>{r.name}</option>{/each}
															</select>
														</label>
														<label class="field-l f-argv">
															<span class="lbl">Model</span>
															<input class="inp mono" bind:value={newModel.model} placeholder="haiku" />
														</label>
														<span class="ibar">
															<IconButton icon="check" label="Add to catalog" small onclick={addModel} />
															<IconButton
																icon="x"
																label="Cancel"
																small
																onclick={() => {
																	newModel = null;
																	newModelError = null;
																}}
															/>
														</span>
													</div>
													{#if newModelError}<p class="err" role="alert">{newModelError}</p>{/if}
												</td>
											</tr>
										{/if}

										{#each groups as group, gi (group.harness)}
											{@const stored = rows.find((r) => r.name === group.harness)}
											{@const ready = READY_TONE[group.column?.state ?? 'unknown']}
											{#if gi > 0}<tr class="gap" aria-hidden="true"><td colspan="4"></td></tr>{/if}
											<tr class="hgrp" class:editing={editing === `h:${group.harness}`}>
												<td colspan="3">
													<div class="cell">
														<Mark harness={group.harness} size={18} />
														<b>{group.harness}</b>
														{#if editing === `h:${group.harness}` && stored}
															<span class="caps fade-in">
																{#each CAP_KEYS as cap (cap)}
																	<label class="cap" title={CAP_LABEL[cap]}>
																		<span class="lbl">{CAP_SHORT[cap]}</span>
																		<select
																			class="sel"
																			aria-label={`${CAP_LABEL[cap]} for ${group.harness}`}
																			bind:value={stored.capabilities[cap]}
																		>
																			<option value="supported">yes</option>
																			<option value="unsupported">no</option>
																			<option value="unknown">—</option>
																		</select>
																	</label>
																{/each}
																<label class="cap" title="Memory class">
																	<span class="lbl">MEM</span>
																	<select
																		class="sel"
																		aria-label={`Memory class for ${group.harness}`}
																		bind:value={stored.capabilities.memory}
																	>
																		<option value={null}>—</option>
																		<option value="A">A</option>
																		<option value="B">B</option>
																		<option value="C">C</option>
																	</select>
																</label>
															</span>
														{:else}
															<span class="mono muted ellipsis"
																>{versionOf(view, group)} · {plural(group.models.length, 'model')}</span
															>
															<span class="rdy" title={group.column?.reason || undefined}>
																<StateMark tone={ready.tone} word={ready.word} />
															</span>
														{/if}
													</div>
												</td>
												<td class="act">
													{#if stored}
														{@render actions(
															`h:${group.harness}`,
															() => beginEdit(`h:${group.harness}`, stored.capabilities),
															() => removeHarness(group.harness),
															'Edit declared capabilities',
															'Remove harness from kitchen.json'
														)}
													{/if}
												</td>
											</tr>
											{#each group.models as row (pairKey(row))}
												{@const key = pairKey(row)}
												{@const ed = editing === `m:${key}`}
												{@const levels = effortLevels(view, key)}
												<tr class="mrow" class:editing={ed}>
													<td>
														<div class="cell">
															<Mark model={row.model} size={16} />
															<span class="mono ellipsis" title={row.model}>{row.model}</span>
															{#if !row.stored}<span class="chip">new</span>{/if}
														</div>
													</td>
													<td>
														{#if ed && declareTiers[key]}
															<input
																class="inp tierbox fade-in"
																aria-label={`New tier name for ${key}`}
																value={row.tierValue}
																placeholder="tier name"
																oninput={(e) => setTier(row, e.currentTarget.value)}
																onkeydown={(e) => {
																	if (e.key === 'Enter') {
																		e.preventDefault();
																		delete declareTiers[key];
																	}
																}}
															/>
														{:else if ed}
															<select
																class="sel tierbox fade-in"
																aria-label={`Tier for ${key}`}
																value={resolvedTier(view, row)}
																onchange={(e) => {
																	const picked = e.currentTarget.value;
																	if (picked === '__new__') {
																		declareTiers[key] = true;
																		setTier(row, '');
																	} else setTier(row, picked);
																}}
															>
																<option value="">not mapped</option>
																{#each tierOptions as name (name)}<option value={name}>{name}</option>{/each}
																<option value="__new__">new tier…</option>
															</select>
														{:else}
															<span class="tierbox">{resolvedTier(view, row) || '—'}</span>
														{/if}
													</td>
													<td>
														<EffortLine
															{levels}
															selected={row.effortValue}
															editing={ed}
															onchange={(next) => {
																row.effortValue = next;
																row.effortTouched = true;
																saved = null;
															}}
														/>
													</td>
													<td class="act">
														{@render actions(
															`m:${key}`,
															() => beginEdit(`m:${key}`, { tierValue: row.tierValue, tierTouched: row.tierTouched, effortValue: row.effortValue, effortTouched: row.effortTouched }),
															() => removeModel(row),
															'Edit tier and effort',
															'Remove from kitchen.json'
														)}
													</td>
												</tr>
											{/each}
										{:else}
											<tr class="note"><td colspan="4"><span class="hint">Nothing is declared yet. Add a harness, then declare a model.</span></td></tr>
										{/each}

										{#if view.discoverable === undefined}
											<tr class="note"><td colspan="4"><span class="hint">This daemon does not look for undeclared harnesses.</span></td></tr>
										{:else}
											{#each discoverable as found (found.harness)}
												<tr class="gap" aria-hidden="true"><td colspan="4"></td></tr>
												<tr class="hgrp disc">
													<td colspan="3">
														<div class="cell">
															<Mark harness={found.harness} size={18} />
															<b>{found.harness}</b>
															<span class="mono muted ellipsis">{found.executable}</span>
															<StateMark tone="waiting" word="On PATH · not added" />
														</div>
													</td>
													<td class="act">
														<Button icon="plus" small onclick={() => void addDiscovered(found.harness, found.executable)}>Add</Button>
													</td>
												</tr>
											{/each}
										{/if}
									</tbody>
								</table>
							{:else if section === 'assignments'}
								<div class="rule-h"><span class="lbl">Precedence</span></div>
								<ol class="ladder" aria-label="Resolution order">
									<li class="dash"><Icon name="file-text" size={13} />Plan override</li>
									<li>Role default</li>
									<li>Initiative default</li>
									<li class="dash">Refusal</li>
								</ol>
								<div class="rule-h"><span class="lbl">Role defaults</span></div>
								{#if rolesResource.phase === 'error'}
									<p class="hint">The role list could not be read from the Library, so only roles already declared are listed.</p>
								{/if}
								<p class="ehint ellipsis" role="status">
									{#if editing !== null}<Icon name="pencil" size={13} />{/if}{hintText}
								</p>
								<div class="alist">
									{#each seats as seat (seat.key)}
										{@const a = seat.assignment}
										{@const ed = editing === `a:${seat.key}`}
										<div class="a-row" class:editing={ed} class:unset={a === null} style="--h: {a ? harnessHue(a.harness) : 'var(--ln2)'}">
											<i class="em" aria-hidden="true"></i>
											<span class="role">{seat.label}</span>
											<span class="pair">
												{#if ed}
													<select
														class="sel fade-in"
														aria-label={`Pair for ${seat.label}`}
														value={a ? pairKey(a) : ''}
														onchange={(e) => setSeat(seat.key, parsePair(e.currentTarget.value))}
													>
														<option value="">{seat.inherits}</option>
														{#each catalogPairs as pair (pair)}<option value={pair}>{pair}</option>{/each}
													</select>
												{:else if a}
													{@render pairCell(a)}
												{:else}
													<span class="muted">{seat.inherits}</span>
												{/if}
											</span>
											<span class="mono muted tier">{tierOf(view, a)}</span>
											<span class="eff-cell">
												{#if ed && a && effortLevels(view, pairKey(a)).length > 0}
													<select
														class="sel fade-in"
														aria-label={`Effort for ${seat.label}`}
														value={a.effort ?? ''}
														onchange={(e) => {
															const v = e.currentTarget.value;
															setSeat(seat.key, { harness: a.harness, model: a.model, ...(v ? { effort: v } : {}) });
														}}
													>
														<option value="">highest</option>
														{#each effortPool(view, pairKey(a)) as level (level)}<option value={level}>{level}</option>{/each}
													</select>
												{:else}
													<span class="mono">effort {effortOf(view, a)}</span>
												{/if}
											</span>
											<span class="act">
												{@render actions(
													`a:${seat.key}`,
													() => beginEdit(`a:${seat.key}`, a),
													seat.clearable ? () => setSeat(seat.key, null) : null,
													'Change assignment',
													'Clear role default',
													'Nothing assigned to clear'
												)}
											</span>
										</div>
									{/each}
								</div>
							{:else if section === 'fallbacks'}
								<div class="ktool">
									<div class="rule-h"><span class="lbl">Fallback chains</span><span class="lbl r">declared · no run consumes them yet</span></div>
									{#if k3.chains.length > 0}
										<Button
											icon="plus"
											small
											disabled={freePrimary === null}
											title={freePrimary === null ? 'Every catalog pair already has a chain.' : undefined}
											onclick={addChain}>Add a fallback</Button
										>
									{/if}
								</div>
								{#if k3.chains.length === 0}
									<p class="empty-line">No fallback chain is declared.</p>
									<div class="btnrow">
										<Button
											icon="plus"
											disabled={freePrimary === null}
											title={catalogPairs.length === 0 ? 'A chain’s primary is a catalog pair — declare models first.' : undefined}
											onclick={addChain}>Add a fallback</Button
										>
									</div>
								{/if}
								<div class="alist">
									{#each k3.chains as chain, ci (ci)}
										{@const ed = editing === `c:${ci}`}
										<div class="c-row" class:editing={ed} style="--h: {harnessHue(chain.primary.harness)}">
											<i class="em" aria-hidden="true"></i>
											<div class="c-main">
												<span class="pair">
													{#if ed}
														<select
															class="sel fade-in"
															aria-label="Primary"
															value={pairKey(chain.primary)}
															onchange={(e) => {
																const next = parsePair(e.currentTarget.value);
																if (next !== null) chain.primary = next;
															}}
														>
															{#each catalogPairs as pair (pair)}<option value={pair}>{pair}</option>{/each}
														</select>
													{:else}
														{@render pairCell(chain.primary)}
														{#if !chain.stored}<span class="chip">new</span>{/if}
													{/if}
												</span>
												{#if !ed}
													<span class="falls">
														{#each chain.candidates as cand, k (k)}
															<Icon name="chevron-right" size={13} />
															<Mark harness={cand.harness} size={14} /><Mark model={cand.model} size={14} />
															<span class="mono ellipsis">{pairKey(cand)}</span>
														{:else}
															<span class="muted">no candidates</span>
														{/each}
													</span>
												{/if}
												<span class="act">
													{@render actions(
														`c:${ci}`,
														() => beginEdit(`c:${ci}`, chain),
														() => (k3.chains = k3.chains.filter((_, i) => i !== ci)),
														'Edit chain',
														'Remove chain'
													)}
												</span>
											</div>
											{#if ed}
												<ol class="cands fade-in">
													{#each chain.candidates as cand, k (k)}
														<li>
															<span class="lbl pos">{k + 1}</span>
															<select
																class="sel"
																aria-label={`Candidate ${k + 1} for ${pairKey(chain.primary)}`}
																value={pairKey(cand)}
																onchange={(e) => {
																	const next = parsePair(e.currentTarget.value);
																	if (next !== null) chain.candidates[k] = next;
																}}
															>
																{#each candidateOptions(chain, k) as pair (pair)}<option value={pair}>{pair}</option>{/each}
																{#if !catalogPairs.includes(pairKey(cand))}<option value={pairKey(cand)}>{pairKey(cand)}</option>{/if}
															</select>
															<span class="ibar">
																<IconButton icon="chevron-up" label="Move up" small disabled={k === 0} onclick={() => moveCandidate(chain, k, k - 1)} />
																<IconButton icon="chevron-down" label="Move down" small disabled={k === chain.candidates.length - 1} onclick={() => moveCandidate(chain, k, k + 1)} />
																<IconButton icon="trash-2" label="Remove candidate" small danger onclick={() => (chain.candidates = chain.candidates.filter((_, i) => i !== k))} />
															</span>
														</li>
													{/each}
													{#if candidateOptions(chain).length > 0}
														<li>
															<span class="lbl pos">+</span>
															<select
																class="sel"
																aria-label="Add a candidate"
																value=""
																onchange={(e) => {
																	const next = parsePair(e.currentTarget.value);
																	if (next !== null) chain.candidates = [...chain.candidates, next];
																	e.currentTarget.value = '';
																}}
															>
																<option value="">add a candidate…</option>
																{#each candidateOptions(chain) as pair (pair)}<option value={pair}>{pair}</option>{/each}
															</select>
														</li>
													{/if}
												</ol>
											{/if}
										</div>
									{/each}
								</div>
							{:else}
								<div class="rule-h"><span class="lbl">Test a model</span><span class="lbl r">the only place testing lives</span></div>
								<div class="smoke">
									<label class="field-l">
										<span class="lbl">Pair</span>
										<RichSelect
											options={smokeOptions}
											value={smokeHarness ? `${smokeHarness}/${smokeModel}` : null}
											label="Pair to test"
											placeholder="No model declared"
											disabled={smokeRunning || smokeOptions.length === 0}
											onchange={(v) => {
												const p = parsePair(v);
												if (p) {
													smokeHarness = p.harness;
													smokeModel = p.model;
												}
											}}
										/>
									</label>
									<div class="btnrow run">
										{#if smokeRoute === 'absent'}
											<span class="state" data-tone="waiting" role="status">Testing is this daemon's route to serve and it does not serve it.</span>
										{:else}
											<Button
												icon="flask-conical"
												kind="primary"
												busy={smokeRunning}
												disabled={smokeOptions.length === 0 || smokeModel === ''}
												onclick={() => void runSmoke()}>Run test</Button
											>
											<span class="hint" role="status">
												{smokeRunning
													? `Waiting on ${smokeHarness} — up to ${SMOKE_TIMEOUT}s.`
													: `Sends the daemon's fixed prompt and spends that harness's tokens. Up to ${SMOKE_TIMEOUT}s.`}
											</span>
										{/if}
									</div>
									{#if smokeNotice}<p class="err" role="alert">{smokeNotice}</p>{/if}
									{#if liveOutcome}
										<div class="res" role="status">
											<StateMark tone={SMOKE_TONE[liveOutcome.result.state]} word={SMOKE_WORD[liveOutcome.result.state]} />
											<span class="mono">{liveOutcome.harness}/{liveOutcome.model}</span>
											<span>{outcomeCopy(liveOutcome.result, SMOKE_TIMEOUT)}</span>
											<span class="mono muted q">“{liveOutcome.result.detail}”</span>
										</div>
									{/if}
								</div>
								{#if view.smoke.results.length > 0}
									<div class="rule-h"><span class="lbl">Results this session</span></div>
									<ul class="reslist">
										{#each sortedResults(view.smoke.results) as r (`${r.harness}/${r.model}`)}
											{@const entry = view.models.find((m) => m.harness === r.harness && m.model === r.model)}
											<li class="res">
												<StateMark tone={SMOKE_TONE[r.state]} word={SMOKE_WORD[r.state]} />
												<span class="who"><Mark harness={r.harness} size={14} /><Mark model={r.model} size={14} /><span class="mono ellipsis">{r.harness}/{r.model}</span></span>
												<span>{outcomeCopy(r, SMOKE_TIMEOUT)}</span>
												<span class="mono muted">{entry ? USAGE_WORD[entry.usage] : '—'} · {new Date(r.at).toLocaleTimeString()}</span>
												<span class="mono muted q">“{r.detail}”</span>
											</li>
										{/each}
									</ul>
								{/if}
								{#if absenceOf(view.smoke) !== null}
									<p class="empty-line">{absenceOf(view.smoke)}</p>
								{/if}
							{/if}
						</div>
					{/key}
				</div>
			</div>
		{/snippet}
	</AsyncField>
</section>

<style>
	.kitchen { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; }
	.kit { flex: 1; display: grid; grid-template-columns: 272px minmax(0, 1fr); min-height: 0; }
	.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
	.meta .lbl { white-space: nowrap; }

	/* --- index spine (DS §9.20) --- */
	.spine { border-right: 1px solid var(--ln); overflow: auto; position: relative; }
	.hl {
		position: absolute; left: 0; right: 0; top: 0; height: 76px; background: var(--p2);
		transition: transform var(--t-tab) var(--ease); z-index: 0; pointer-events: none;
	}
	.ix {
		display: grid; align-content: center; gap: 3px; width: 100%; height: 76px; box-sizing: border-box;
		text-align: left; padding: 0 20px 0 24px; background: none; border: 0; border-bottom: 1px solid var(--ln);
		cursor: pointer; position: relative; z-index: 1; transition: background var(--t-fast);
	}
	.ix:hover:not([aria-current]) { background: color-mix(in srgb, var(--p1) 70%, transparent); }
	.ix .s {
		font: 500 20px/1.1 var(--f-label); letter-spacing: 0.04em; text-transform: uppercase; color: var(--dim);
		transition: color var(--t-fast);
	}
	.ix[aria-current] .s { color: var(--tx); }
	.ix .sum { font: 400 12.5px var(--f-ui); color: var(--dim); white-space: nowrap; }
	.ix .sum.absent { text-decoration: underline dashed var(--fnt); text-underline-offset: 4px; justify-self: start; max-width: 100%; }
	.prov { padding: 16px 24px; font: 400 11.5px/1.5 var(--f-mono); color: var(--fnt); position: relative; z-index: 1; }
	.prov p { margin-top: 8px; }

	.kpane { overflow: auto; min-height: 0; padding: 22px 28px 40px; }

	.notice { margin-bottom: 18px; }
	.notice p { white-space: pre-line; overflow-wrap: anywhere; font-size: 12.5px; }
	.blockers { list-style: none; margin: 0 0 18px; padding: 0; display: grid; gap: 8px; }
	.err { margin: 8px 0 0; font: 400 12.5px var(--f-ui); color: var(--l656); }

	.ktool { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
	.ktool .rule-h { margin: 0; flex: 1; }
	.ehint {
		display: flex; align-items: center; gap: 8px; height: 36px; padding: 8px 2px 0; margin: 0;
		font: 400 12px var(--f-ui); color: var(--dim);
	}

	/* --- grouped table (DS §9.16) --- */
	.mt { table-layout: fixed; min-width: 860px; }
	.mt td { height: 52px; padding-top: 0; padding-bottom: 0; overflow: hidden; transition: background var(--t-fast); }
	.mt td.act { text-align: right; }
	.mt .cell { overflow: hidden; }
	.mt tr.mrow td:first-child { padding-left: 38px; }
	.mt tr.mrow .mono { font-size: 12.5px; }
	.mt tr.gap td { height: 18px; border: 0; background: none; padding: 0; }
	.mt tr.hgrp td { height: 44px; background: var(--p1); border-bottom: 1px solid var(--ln); }
	.mt tr.hgrp b { font: 500 14px var(--f-ui); }
	.mt tr.hgrp .cell { gap: 10px; }
	.mt tr.hgrp .rdy { margin-left: 6px; flex: none; }
	.mt tr.hgrp .rdy :global(.state) { font-size: 10.5px; }
	.mt tr.hgrp.disc td { background: none; border-bottom: 1px dashed var(--ln2); }
	.mt tr.hgrp.disc b { color: var(--tx2); font-weight: 400; }
	.mt tr.hgrp.disc :global(.state) { font-size: 10.5px; margin-left: 6px; }
	.mt tr.note td { height: 40px; border-bottom: 0; }
	.mt tr.form td { height: auto; padding: 12px 12px 14px; background: var(--p1); border-bottom: 1px solid var(--ln2); overflow: visible; }
	.frm { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
	.frm .f-name { width: 190px; }
	.frm .f-argv { flex: 1; min-width: 220px; }
	.frm .f-flag { width: 160px; }
	.frm .inp, .frm .sel { height: 28px; }
	.mt tr.editing td { background: var(--p1); border-bottom-color: var(--ln2); }
	.mt tr.editing td:first-child { box-shadow: inset 2px 0 0 var(--tx); }

	.tierbox {
		display: inline-flex; align-items: center; box-sizing: border-box; width: 132px; height: 28px; padding: 0 9px;
		border: 1px solid transparent; font: 400 11.5px var(--f-mono); color: var(--tx2);
	}
	.tierbox.sel { padding-right: 26px; background-color: var(--p2); border-color: var(--ln2); color: var(--tx); font-family: var(--f-mono); font-size: 11.5px; }
	.tierbox.inp { padding: 0 9px; font-family: var(--f-mono); font-size: 11.5px; }

	.caps { display: flex; gap: 8px; align-items: center; margin-left: 6px; min-width: 0; }
	.cap { display: inline-flex; align-items: center; gap: 4px; flex: none; }
	.cap .lbl { font-size: 10px; letter-spacing: 0.1em; }
	.cap .sel { height: 28px; width: 58px; padding: 0 20px 0 7px; font-size: 12px; background-position: right 11px center, right 6px center; }

	/* --- assignments --- */
	.ladder { display: flex; align-items: center; list-style: none; margin: 4px 0 24px; padding: 0; flex-wrap: wrap; }
	.ladder li {
		display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 14px; border: 1px solid var(--ln2);
		font: 500 11.5px var(--f-label); letter-spacing: 0.12em; text-transform: uppercase; color: var(--tx2);
	}
	.ladder li.dash { border-style: dashed; color: var(--dim); }
	.ladder li + li { position: relative; margin-left: 22px; }
	.ladder li + li::before { content: ''; position: absolute; left: -23px; top: 50%; width: 22px; height: 1px; background: var(--ln2); }

	.alist { margin-top: 2px; }
	.a-row {
		display: grid; grid-template-columns: 2px 150px minmax(0, 1fr) 120px 130px 76px; align-items: center; gap: 0 16px;
		height: 52px; border-bottom: 1px solid var(--ln); transition: background var(--t-fast); min-width: 720px;
	}
	.a-row.editing { background: var(--p1); border-bottom-color: var(--ln2); }
	.a-row .em { align-self: stretch; margin: 10px 0; background: var(--h); }
	.a-row.unset .em { background: repeating-linear-gradient(var(--ln2) 0 3px, transparent 3px 6px); }
	.a-row .role { font: 500 16px var(--f-label); letter-spacing: 0.06em; text-transform: uppercase; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.a-row .pair { display: flex; align-items: center; gap: 8px; min-width: 0; font: 400 13px var(--f-ui); }
	.a-row .pair .sel { height: 28px; max-width: 380px; font: 400 12px var(--f-mono); }
	.a-row .tier { font-size: 11.5px; }
	.a-row .eff-cell { min-width: 0; font-size: 12px; }
	.a-row .eff-cell .sel { height: 28px; width: 110px; font: 400 12px var(--f-mono); }
	.a-row .act { text-align: right; }

	/* --- fallbacks --- */
	.c-row { position: relative; padding-left: 18px; border-bottom: 1px solid var(--ln); min-width: 640px; transition: background var(--t-fast); }
	.c-row.editing { background: var(--p1); border-bottom-color: var(--ln2); }
	.c-row .em { position: absolute; left: 0; top: 10px; bottom: 10px; width: 2px; background: var(--h); }
	.c-main { display: flex; align-items: center; gap: 16px; min-height: 52px; }
	.c-main .pair { display: flex; align-items: center; gap: 8px; flex: none; font: 400 13px var(--f-ui); }
	.c-main .pair .sel { height: 28px; width: 340px; font: 400 12px var(--f-mono); }
	.c-main .falls { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; color: var(--tx2); overflow: hidden; white-space: nowrap; }
	.c-main .act { margin-left: auto; flex: none; }
	.cands { list-style: none; margin: 0; padding: 0 0 12px 0; display: grid; gap: 6px; }
	.cands li { display: flex; align-items: center; gap: 10px; }
	.cands .pos { width: 14px; text-align: right; }
	.cands .sel { height: 28px; width: 340px; font: 400 12px var(--f-mono); }

	/* --- test a model --- */
	.smoke { display: grid; gap: 14px; max-width: 520px; margin-bottom: 28px; }
	.run { margin-top: 0; }
	.res {
		display: grid; grid-template-columns: 120px minmax(0, 260px) minmax(0, 1fr); gap: 4px 16px; align-items: baseline;
		padding: 12px 0; border-bottom: 1px solid var(--ln); font: 400 13px var(--f-ui);
	}
	.smoke .res { border-top: 1px solid var(--ln); max-width: none; }
	.res .who { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 12.5px; }
	.res .q { grid-column: 2 / -1; white-space: pre-line; overflow-wrap: anywhere; font-size: 12px; }
	.reslist { list-style: none; margin: 0; padding: 0; }
</style>
