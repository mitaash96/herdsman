<!--
KITCHEN — THE INDEX SPINE

The index is the sheet's left column and the first thing read: every section
carries its own state on its entry, and the chosen section draws inline beside
it. Harnesses is a register in two bands — Added (declared in kitchen.json) and
Discoverable (a known executable on PATH, located with `which`, never run) —
and each added harness expands into its levels: observed evidence, declared
capabilities, then its models with their tier, effort range and the defaults
they hold.

Evidence is one four-step scale (declared · found · answered · versioned) drawn
as pips; a discoverable harness shows found alone. Height is observed, seats
are declared, and the two never share ink. This surface infers no price or
capability from a name and mirrors no server-side refusal rule; the only write
is the whole-document save, and discovery writes nothing anywhere.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import { fly, slide } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import AsyncField from '$lib/AsyncField.svelte';
	import { Resource } from '$lib/resource.svelte';
	import {
		daemon,
		DaemonError,
		type AssetSummary,
		type Kitchen,
		type KitchenCapabilities,
		type KitchenSmokeResult
	} from '$lib/daemon';
	import {
		COURSES,
		SMOKE_TIMEOUT,
		absenceOf,
		allEfforts,
		allSelected,
		classifySaveFailure,
		columnsOf,
		editsDirty,
		editsFrom,
		effortLevels,
		hasReach,
		mergeRacedEdits,
		memberState,
		outcomeCopy,
		pairKey,
		pairsOf,
		reachOf,
		reachValue,
		rigReading,
		roleNamesFrom,
		savePayload,
		tierNames,
		toggleEffort,
		type AdapterEdit,
		type ChainRow,
		type Column,
		type KitchenEdits,
		type ModelRow,
		type SaveFailure
	} from '$lib/kitchen';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));
	/* The role vocabulary is the Library's enumeration, read once alongside the
	   kitchen: role is a named thing, so the choice rule binds the control to
	   this list — an empty list is a configuration state with a next action,
	   never a text box, and a declared key outside the list still renders as
	   the current value but cannot be typed back into existence. */
	const rolesResource = new Resource<AssetSummary[]>((signal) => daemon.libraryRoles(signal));

	let selected = $state<string | null>(null);
	let probing = $state(false);
	let probedAt = $state<Date | null>(null);
	let outcome = $state<{ ok: boolean; message: string } | null>(null);
	/* `POST /kitchen/discovery` is Sprint 8's; a daemon that predates it answers
	   404 and the control becomes an unavailable action rather than a dead
	   button that fails the same way every time it is pressed. */
	let probeRoute = $state<'present' | 'absent'>('present');
	let outcomeEl = $state<HTMLParagraphElement | null>(null);

	const columns = $derived(kitchen.data ? columnsOf(kitchen.data) : []);
	const rig = $derived(rigReading(columns));
	/* The Reach row: present exactly while the daemon holds any result, valued
	   from each harness's own newest one — never an aggregate, never borrowed. */
	const reachShown = $derived(kitchen.data ? hasReach(kitchen.data.smoke) : false);
	const harnessModels = $derived(
		(kitchen.data?.models ?? []).filter((model) => model.harness === smokeHarness)
	);
	/* This window's own outcome, until the projection carries it: then the
	   per-pair list holds it and the live line stands down, so the same result
	   is never printed twice on one screen. */
	const liveOutcome = $derived.by(() => {
		const live = smokeOutcome;
		const view = kitchen.data;
		if (!live || !view) return live;
		return view.smoke.results.some(
			(result) =>
				result.harness === live.harness &&
				result.model === live.model &&
				result.at === live.result.at
		)
			? null
			: live;
	});

	/* One probe on first open, and only when the daemon holds no facts at all:
	   discovery lives in daemon memory, so a daemon that has just started
	   reports every harness `unknown` until something looks. Not a poll — each
	   probe starts a real process per declared harness — so it happens once
	   here and afterwards only when the operator asks. */
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
		outcome = null;
		try {
			const measuredView = await daemon.probeKitchen();
			probedAt = new Date();
			const rigNow = rigReading(columnsOf(measuredView));
			outcome = {
				ok: true,
				message: `${rigNow.declared} ${rigNow.declared === 1 ? 'harness' : 'harnesses'} measured: ${rigNow.ready} ready, ${rigNow.unavailable} unavailable${rigNow.other > 0 ? `, ${rigNow.other} neither` : ''}.`
			};
			/* The probe answers with the whole projection; this build re-reads it
			   through the one Resource instead of holding a second copy, so there
			   is exactly one thing on screen that can be stale. */
			await kitchen.load();
		} catch (cause) {
			if (cause instanceof DaemonError && cause.kind === 'not_found') {
				probeRoute = 'absent';
				outcome = {
					ok: false,
					message:
						'This daemon serves no /kitchen/discovery route, so nothing here can be measured from the browser. Run a newer daemon to probe.'
				};
			} else {
				outcome = {
					ok: false,
					message:
						cause instanceof Error ? cause.message : 'The probe failed before it reported.'
				};
			}
			outcomeEl?.focus();
		} finally {
			probing = false;
		}
	}

	const measured = (view: Kitchen): string => {
		if (!view.configured) return 'nothing declared to measure';
		if (view.discovery.facts.length === 0)
			return probing ? 'measuring now' : 'not measured — nothing has been probed yet';
		if (probedAt) return `measured at ${probedAt.toLocaleTimeString()}`;
		return 'measured earlier in this daemon session, not by this window';
	};

	/* --- Setting up: the one write path ------------------------------------- */

	/** A form row: stored capabilities the operator may edit, plus any template
	 * field they have touched. Untouched fields never enter the payload — the
	 * daemon reads an omission as *keep the stored template*, and this build
	 * never has a stored template in hand. */
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

	/* A refused save's recovery — K2-D2: the race copy promises the operator's
	   entries are kept, so the reload that fetches the new revision must not run
	   the formKey rebuild that would discard them. Held rows are captured before
	   the read (an effect may flush between await and resume), merged onto the
	   fresh document, and formKey is pre-seeded so the rebuild effect sees a
	   matching key and stands down. The next save is then preconditioned on —
	   and built against — what the daemon now holds: a racing writer's adapter
	   arrives as a fresh row (its untouched template omitted, so the daemon
	   keeps it), and its models/tiers/defaults ride the fresh view. Entries
	   kept, no silent destruction. */
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
		/* A row the racing write removed is carried only when it holds real edits
		   or an unsaved draft — a plain untouched row goes with its deletion. */
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

	function blankCaps(): KitchenCapabilities {
		return {
			structured_output: 'unknown',
			resume: 'unknown',
			usage: 'unknown',
			pty: 'unknown',
			memory: null
		};
	}

	let rows = $state<SetupRow[]>([]);
	let draft = $state<{ name: string; argvText: string; modelArgvText: string } | null>(null);
	let addError = $state<string | null>(null);
	let saving = $state(false);
	/* Two small motions, both position rather than load: an expanded row
	   unfolds, and a switched pane settles in. Reduced motion collapses both. */
	const SLIDE = { duration: 180, easing: cubicOut };
	const SETTLE = { y: 6, duration: 180, easing: cubicOut };

	type SectionId = 'harnesses' | 'catalog' | 'assignments' | 'fallbacks' | 'smoke' | 'notes';
	let section = $state<SectionId>('harnesses');
	let saveOutcome = $state<
		{ kind: 'saved'; cleared: boolean } | { kind: 'failed'; failure: SaveFailure } | null
	>(null);
	let saveEl = $state<HTMLDivElement | null>(null);

	/* --- K3: one dirty model across every editor ---------------------------- */

	type SaveSection = 'setup' | 'catalog' | 'assignments' | 'fallbacks';
	/* Whose Save was pressed: the outcome renders there and nowhere else — the
	   same sentence printed under four sections would be R2's recurring
	   printed-twice finding. */
	let saveSection = $state<SaveSection>('setup');

	let k3 = $state<KitchenEdits>({
		models: [],
		planner: null,
		initiative: null,
		roles: [],
		chains: []
	});
	/* Rebuild keyed on the document's *identity* sets — model pairs, role keys,
	   chain primaries — exactly as formKey keys adapters: a probe or a smoke
	   refresh cannot wipe what the operator typed, an explicit save rebuilds,
	   and a race is merged by hand with this key pre-seeded so the rebuild
	   effect stands down (K2-D2's recipe, generalized by K5). */
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


	let newModel = $state<{ harness: string; model: string } | null>(null);
	let newModelError = $state<string | null>(null);
	/* Rows whose tier control is in "declare a new name" mode; membership is
	   the mode, so an empty new name is still distinguishable from unmapped. */
	let declareTiers = $state<Record<string, boolean>>({});

	const catalogPairs = $derived(k3.models.map((row) => pairKey(row)));
	const tierOptions = $derived.by(() => {
		const names = kitchen.data ? tierNames(kitchen.data) : [];
		const set = new Set(names);
		/* A tier name typed this session, before any save puts it in the map. */
		for (const row of k3.models) {
			if (row.tierTouched && row.tierValue !== '') set.add(row.tierValue);
		}
		return [...set];
	});
	const roleNames = $derived(rolesResource.data ? roleNamesFrom(rolesResource.data) : []);

	function parsePair(value: string): { harness: string; model: string } | null {
		if (value === '') return null;
		const cut = value.indexOf('/');
		/* Harness names never carry a slash, so the first one is the separator
		   even when the model name itself contains more. */
		return cut <= 0 ? null : { harness: value.slice(0, cut), model: value.slice(cut + 1) };
	}

	function removeModel(row: ModelRow): void {
		k3.models = k3.models.filter((item) => pairKey(item) !== pairKey(row));
		delete declareTiers[pairKey(row)];
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
	}

	/* Price reads each side on its own: an absent side is unknown, never zero,
	   and the currency rides only when a price exists at all. */
	const priceText = (row: ModelRow): string => {
		if (row.price === null) return 'unknown';
		const input = row.price.input_per_mtok === null ? 'input unknown' : `input $${row.price.input_per_mtok}/Mtok`;
		const output = row.price.output_per_mtok === null ? 'output unknown' : `output $${row.price.output_per_mtok}/Mtok`;
		return `${input} · ${output} ${row.price.currency}`;
	};

	/* Form-level identity only: a candidate already in this chain — other than
	   the row being edited — or the chain's own primary, cannot be picked twice
	   here. The row's own current value always stays an option, so a declared
	   candidate is never rendered blank. Escalation, cycles and pairs outside
	   the catalog are the daemon's refusals on save and are never mirrored. */
	const candidateOptions = (chain: ChainRow, own?: number): string[] =>
		catalogPairs.filter(
			(pair) => pair !== pairKey(chain.primary) &&
				!chain.candidates.some(
					(candidate, index) =>
						(own === undefined || index !== own) && pairKey(candidate) === pair
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

	/* One dirty model across four editors: Save in any section writes every
	   unsaved change on the page, and the consequence says so (K5). Declared
	   after `dirty`, which it folds in. */

	/* The one approved whole-document consequence, shown under every Save. */
	const SAVE_CONSEQUENCE =
		"Saving writes this project's whole .herdsman/kitchen.json declaration — every unsaved change on this page, not only this section's. It also clears every model test result, because those describe the configuration being replaced. No harness setting outside this project is touched.";

	/* The form follows the *set of adapters*, never a re-read: a probe or a
	   smoke refresh cannot wipe what the operator typed, and the rebuild after a
	   successful save is what returns the rows to their stored state. */
	let formKey = '';
	$effect(() => {
		const view = kitchen.data;
		if (!view) return;
		const key = view.adapters.map((adapter) => adapter.name).join('\n');
		if (key === formKey) return;
		formKey = key;
		rows = rowsFrom(view);
	});

	function parseTemplate(text: string): string[] | null {
		try {
			const value: unknown = JSON.parse(text);
			if (Array.isArray(value) && value.every((element) => typeof element === 'string'))
				return value as string[];
		} catch {
			/* not JSON — refused below, never guessed at */
		}
		return null;
	}

	const dirty = $derived(
		rows.some((row) => {
			const stored = kitchen.data?.adapters.find((adapter) => adapter.name === row.name);
			return (
				row.argvTouched ||
				row.modelArgvTouched ||
				row.agentArgsTouched ||
				stored === undefined ||
				JSON.stringify(stored.capabilities) !== JSON.stringify(row.capabilities)
			);
		})
	);

	/* One dirty model across four editors: Save in any section writes every
	   unsaved change on the page, and the consequence says so (K5). */
	const anyDirty = $derived(
		dirty || (kitchen.data !== null ? editsDirty(kitchen.data, k3) : false)
	);

	function addRow(): void {
		if (draft === null) return;
		const name = draft.name.trim();
		const templateMessage =
			'A launch template must be a JSON array of strings with one {prompt} element, for example ["claude", "-p", "{prompt}"].';
		const argv = parseTemplate(draft.argvText);
		const modelArgv = draft.modelArgvText.trim() === '' ? [] : parseTemplate(draft.modelArgvText);
		if (name === '') {
			addError = 'A new adapter needs a name — the name this project declares it under.';
			return;
		}
		if (rows.some((row) => row.name === name)) {
			addError = `${name} is already in this form.`;
			return;
		}
		if (argv === null || argv.length < 2 || argv.filter((element) => element === '{prompt}').length !== 1) {
			addError = templateMessage;
			return;
		}
		if (modelArgv === null) {
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
	}

	async function save(section: SaveSection): Promise<void> {
		const view = kitchen.data;
		if (!view || saving) return;
		/* Every touched template is parsed before anything is sent: an unparsable
		   field stops the save with nothing written, and the field keeps exactly
		   what was typed. */
		const edits: AdapterEdit[] = [];
		for (const row of rows) {
			const argv = row.argvTouched ? parseTemplate(row.argvText) : null;
			const modelArgv = row.modelArgvTouched ? parseTemplate(row.modelArgvText) : null;
			const agentArgs = row.agentArgsTouched ? parseTemplate(row.agentArgsText) : null;
			if ((row.argvTouched && argv === null) || (row.modelArgvTouched && modelArgv === null) || (row.agentArgsTouched && agentArgs === null)) {
				saveOutcome = {
					kind: 'failed',
					failure: {
						kind: 'invalid',
						detail:
							'A launch template must be a JSON array of strings with one {prompt} element, for example ["claude", "-p", "{prompt}"].'
					}
				};
				saveEl?.focus();
				return;
			}
			edits.push({ name: row.name, capabilities: row.capabilities, argv, model_argv: modelArgv, agent_args: agentArgs });
		}
		saving = true;
		saveSection = section;
		saveOutcome = null;
		try {
			const resultsBefore = view.smoke.results.length;
			const fresh = await daemon.saveKitchen(savePayload(view, edits, k3, view.revision));
			rows = rowsFrom(fresh);
			formKey = fresh.adapters.map((adapter) => adapter.name).join('\n');
			k3 = editsFrom(fresh);
			k3Key = k3Identity(fresh);
			saveOutcome = { kind: 'saved', cleared: resultsBefore > 0 };
			/* Every saved result described the configuration being replaced — this
			   window's own smoke outcome goes with them. */
			smokeOutcome = null;
			await kitchen.load();
		} catch (cause) {
			const failure =
				cause instanceof DaemonError
					? classifySaveFailure(cause)
					: {
							kind: 'unknown' as const,
							detail: cause instanceof Error ? cause.message : 'The save failed before it was sent.'
					  };
			saveOutcome = { kind: 'failed', failure };
			/* The race copy promises entries are kept: reload for the new revision,
			   but merge the held rows onto the fresh document first and pre-seed
			   formKey, so the rebuild effect stands down and nothing typed is lost. */
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
			if (saveOutcome?.kind === 'failed') saveEl?.focus();
		}
	}

	/* --- Testing a model ----------------------------------------------------- */

	let smokeHarness = $state('');
	let smokeModel = $state('');
	let smokeArmed = $state(false);
	let smokeRunning = $state(false);
	let smokeRoute = $state<'present' | 'absent'>('present');
	let smokeNotice = $state<string | null>(null);
	let smokeNoticeEl = $state<HTMLParagraphElement | null>(null);
	let smokeAbsentEl = $state<HTMLParagraphElement | null>(null);
	let smokeOutcome = $state<{ harness: string; model: string; result: KitchenSmokeResult } | null>(
		null
	);

	/* The pair picks itself from what exists and repairs itself when the
	   catalog changes — a removed model never leaves a select pointing at it. */
	$effect(() => {
		const view = kitchen.data;
		if (!view) return;
		if (!view.adapters.some((adapter) => adapter.name === smokeHarness)) {
			smokeHarness = view.adapters[0]?.name ?? '';
		}
		if (!view.models.some((m) => m.harness === smokeHarness && m.model === smokeModel)) {
			smokeModel = view.models.find((m) => m.harness === smokeHarness)?.model ?? '';
		}
	});

	/* Arm belongs to the pair it was armed for: switching the subject withdraws
	   it, exactly as R3 keys a decision to what it was made against. Held in a
	   plain let — an effect that reads and writes one reactive value re-triggers
	   itself on its own write (H1's and L1's recorded bug). */
	let armedPair = '';
	$effect(() => {
		const pair = `${smokeHarness}/${smokeModel}`;
		if (pair === armedPair) return;
		armedPair = pair;
		smokeArmed = false;
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
			if (cause instanceof DaemonError && cause.kind === 'not_found') {
				/* The control block a notice would announce from is the block this
				   flip removes: the absent paragraph is the notice now, focused
				   after the flip so the change reaches the operator. */
				smokeRoute = 'absent';
				await tick();
				smokeAbsentEl?.focus();
			} else {
				smokeNotice = cause instanceof Error ? cause.message : 'The test failed before it was sent.';
				smokeNoticeEl?.focus();
			}
		} finally {
			smokeRunning = false;
			smokeArmed = false;
		}
	}

	/* Newest first: the list reads as a history, and the key stays the pair so
	   a live re-read can never swap two rows under the reader. */
	function sortedResults(results: KitchenSmokeResult[]): KitchenSmokeResult[] {
		return [...results].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
	}

	const SMOKE_STATE_WORD: Record<KitchenSmokeResult['state'], string> = {
		passed: 'Answered',
		failed: 'No answer',
		refused: 'Refused',
		timed_out: 'Timed out'
	};

	const headline = (): string => {
		if (kitchen.phase === 'error') return 'Not answering';
		if (!kitchen.data) return 'Reading';
		if (kitchen.stale) return 'Stale';
		if (!kitchen.data.configured) return 'Nothing declared';
		return `${rig.ready} of ${rig.declared} ready`;
	};



	/* --- the index: each entry carries its own section's state -------------- */

	const discoverable = $derived(kitchen.data?.discoverable ?? []);
	const roleRows = $derived.by(() => {
		/* Every role the Library names, plus any declared key outside it, so a
		   role with no default reads as inheriting rather than going missing. */
		const names = [...roleNames];
		for (const row of k3.roles) if (!names.includes(row.role)) names.push(row.role);
		return names.map((role) => k3.roles.find((row) => row.role === role) ?? { role, assignment: null, stored: false });
	});

	interface IndexEntry {
		id: SectionId;
		no: string;
		label: string;
		summary: string;
		state: 'slack' | 'balanced' | 'seated' | 'failed';
	}
	const index = $derived.by((): IndexEntry[] => {
		const view = kitchen.data;
		const harnessCount = new Set(k3.models.map((row) => row.harness)).size;
		const tierCount = new Set(k3.models.map((row) => row.tierValue).filter(Boolean)).size;
		const results = view?.smoke.results.length ?? 0;
		const found = discoverable.length > 0 ? ` · ${discoverable.length} discoverable` : '';
		return [
			{
				id: 'harnesses', no: '01', label: 'Harnesses',
				summary: !view?.configured ? `none added${found}` : `${rig.ready} of ${rig.declared} ready${found}`,
				state: rig.unavailable > 0 ? 'failed' : view?.configured ? 'seated' : 'slack'
			},
			{
				id: 'catalog', no: '02', label: 'Models',
				summary: k3.models.length === 0 ? 'none declared' : `${k3.models.length} across ${harnessCount} ${harnessCount === 1 ? 'harness' : 'harnesses'} · ${tierCount} ${tierCount === 1 ? 'tier' : 'tiers'}`,
				state: k3.models.length ? 'balanced' : 'slack'
			},
			{
				id: 'assignments', no: '03', label: 'Assignments',
				summary: k3.planner === null || k3.initiative === null
					? `${k3.planner === null ? 'no planner' : 'no initiative default'}`
					: `planner · initiative · ${k3.roles.length} ${k3.roles.length === 1 ? 'role' : 'roles'}`,
				state: k3.planner === null || k3.initiative === null ? 'slack' : 'balanced'
			},
			{
				id: 'fallbacks', no: '04', label: 'Fallbacks',
				summary: k3.chains.length === 0 ? 'none declared' : `${k3.chains.length} ${k3.chains.length === 1 ? 'chain' : 'chains'}`,
				state: k3.chains.length ? 'balanced' : 'slack'
			},
			{
				id: 'smoke', no: '05', label: 'Test a model',
				summary: results === 0 ? 'not run this session' : `${results} ${results === 1 ? 'result' : 'results'} this session`,
				state: results ? 'balanced' : 'slack'
			}
		];
	});

	/* --- the harness register --------------------------------------------- */

	/** Which seats a pair holds, from the form's own state, so an unsaved
	 * reassignment already shows where it will land. */
	function seatsHeld(harness: string, model?: string): string[] {
		const holds = (pair: { harness: string; model: string } | null): boolean =>
			pair !== null && pair.harness === harness && (model === undefined || pair.model === model);
		const held: string[] = [];
		if (holds(k3.planner)) held.push('planner');
		if (holds(k3.initiative)) held.push('initiative');
		for (const row of k3.roles) if (holds(row.assignment)) held.push(row.role);
		return held;
	}

	/** Make one pair the default for one seat. Nothing is written until Save. */
	function assignSeat(seat: string, pair: { harness: string; model: string }): void {
		if (seat === 'planner') k3.planner = pair;
		else if (seat === 'initiative') k3.initiative = pair;
		else {
			const row = k3.roles.find((item) => item.role === seat);
			if (row) row.assignment = pair;
			else k3.roles = [...k3.roles, { role: seat, assignment: pair, stored: false }];
		}
	}

	const pairLabel = (pair: { harness: string; model: string } | null): string =>
		pair === null ? 'unset' : pairKey(pair);

	/** The effort range a pair allows, as its ends and a count. */
	function effortRange(view: Kitchen, row: ModelRow): string {
		const levels = row.effortValue.length > 0 ? row.effortValue : effortLevels(view, pairKey(row));
		if (levels.length === 0) return 'no effort levels reported';
		if (levels.length === 1) return levels[0];
		return `${levels[0]} → ${levels[levels.length - 1]} · ${levels.length} levels`;
	}

	/* Adding from Discoverable opens the add form with the name filled in. The
	   launch template stays the operator's: its flags are never guessed. */
	function addDiscovered(harness: string, executable: string): void {
		draft = { name: harness, argvText: '', modelArgvText: '' };
		draftHint = executable;
		addError = null;
	}
	let draftHint = $state('');

	const stateWord: Record<string, string> = {
		ready: 'Ready',
		degraded: 'Degraded',
		unavailable: 'Unavailable',
		unknown: 'Unknown',
		unconfigured: 'Not declared'
	};

	const seatMark: Record<string, string> = {
		supported: 'declared supported',
		unsupported: 'declared unsupported',
		unknown: 'undeclared'
	};
</script>

<section class="kitchen">
	<AsyncField resource={kitchen} reading="the kitchen" onretry={() => void kitchen.load()}>
		{#snippet children(view: Kitchen)}
		{#snippet saveOutcomeBlock(section: SaveSection)}
			{#if saveOutcome !== null && saveSection === section}
				<div
					bind:this={saveEl}
					class="save-outcome member"
					data-state={saveOutcome.kind === 'saved' ? 'seated' : 'failed'}
					role={saveOutcome.kind === 'saved' ? 'status' : 'alert'}
					tabindex="-1"
				>
					{#if saveOutcome.kind === 'saved'}
						<span>{saveOutcome.cleared
								? 'Saved. Every test result was cleared: they described the configuration that was replaced.'
								: 'Saved.'}</span>
					{:else if saveOutcome.failure.kind === 'race'}
						<span class="label">Not written</span>
						<span
							>This project's kitchen changed since this form was read, so the daemon refused
							the save rather than overwrite what it now holds. Nothing was written. Your
							entries are kept; reload the current declaration and apply them again.
							{saveOutcome.failure.detail}</span
						>
					{:else if saveOutcome.failure.kind === 'invalid'}
						<span class="label">Not written</span>
						<span class="detail-lines">{saveOutcome.failure.detail}
Nothing was written.</span>
					{:else if saveOutcome.failure.kind === 'absent'}
						<span class="label">Not written</span>
						<span
							>This daemon serves no save route, so this form cannot write the declaration.
							Run a newer daemon to save.</span
						>
					{:else}
						<span class="label">Not written</span>
						<span>{saveOutcome.failure.detail}</span>
					{/if}
				</div>
			{/if}
		{/snippet}
		{#snippet adapterEditor(row: SetupRow, index: number)}
							{@const stored = kitchen.data?.adapters.some((a) => a.name === row.name)}
							<div class="adapter plate">
								<p class="label adapter-head">
									<span class="adapter-name">{row.name}</span>
									{#if !stored}
										<span class="member" data-state="balanced">Not yet saved</span>
									{/if}
								</p>
								<div class="cap-grid">
									<p class="field">
										<label class="label" for="cap-{index}-output">Structured output</label>
										<select class="plate" id="cap-{index}-output" bind:value={row.capabilities.structured_output}>
											<option value="supported">declared supported</option>
											<option value="unsupported">declared unsupported</option>
											<option value="unknown">undeclared</option>
										</select>
									</p>
									<p class="field">
										<label class="label" for="cap-{index}-resume">Resume</label>
										<select class="plate" id="cap-{index}-resume" bind:value={row.capabilities.resume}>
											<option value="supported">declared supported</option>
											<option value="unsupported">declared unsupported</option>
											<option value="unknown">undeclared</option>
										</select>
									</p>
									<p class="field">
										<label class="label" for="cap-{index}-usage">Usage</label>
										<select class="plate" id="cap-{index}-usage" bind:value={row.capabilities.usage}>
											<option value="supported">declared supported</option>
											<option value="unsupported">declared unsupported</option>
											<option value="unknown">undeclared</option>
										</select>
									</p>
									<p class="field">
										<label class="label" for="cap-{index}-pty">Needs a PTY</label>
										<select class="plate" id="cap-{index}-pty" bind:value={row.capabilities.pty}>
											<option value="supported">declared supported</option>
											<option value="unsupported">declared unsupported</option>
											<option value="unknown">undeclared</option>
										</select>
									</p>
									<p class="field">
										<label class="label" for="cap-{index}-memory">Memory</label>
										<select class="plate" id="cap-{index}-memory" bind:value={row.capabilities.memory}>
											<option value={null}>no class declared</option>
											<option value="A">class A</option>
											<option value="B">class B</option>
											<option value="C">class C</option>
										</select>
									</p>
								</div>
								{#if stored}
									<p class="prose gloss-line" id="tpl-note-{index}">
										Launch template configured; its values are intentionally unreadable. Leave
										replacement fields untouched to keep it, or enter a complete replacement.
									</p>
								{:else}
									<p class="prose gloss-line" id="tpl-note-{index}">
										A new adapter needs its launch template. It is written to this project's
										document and never read back here.
									</p>
								{/if}
								<p class="field">
									<label class="label" for="tpl-argv-{index}">Launch template</label>
									<input
										id="tpl-argv-{index}"
										type="text"
										bind:value={row.argvText}
										oninput={() => (row.argvTouched = true)}
										placeholder={'["claude", "-p", "{prompt}"]'}
										aria-describedby="tpl-note-{index}"
									/>
								</p>
								<p class="field">
									<label class="label" for="tpl-model-{index}">Model flag(s)</label>
									<input
										id="tpl-model-{index}"
										type="text"
										bind:value={row.modelArgvText}
										oninput={() => (row.modelArgvTouched = true)}
										placeholder='["--model"]'
										aria-describedby="tpl-note-{index}"
									/>
								</p>
								<p class="field">
									<label class="label" for="tpl-agent-{index}">Interactive args</label>
									<input
										id="tpl-agent-{index}"
										type="text"
										bind:value={row.agentArgsText}
										oninput={() => (row.agentArgsTouched = true)}
										placeholder='["--permission-mode", "auto"]'
										aria-describedby="tpl-agent-note-{index}"
									/>
								</p>
								<p class="prose gloss-line" id="tpl-agent-note-{index}">
									Extra flags for the interactive herdr launch, after the model flag. Write-only like the template: a JSON array of strings, never read back; untouched keeps the stored value.
								</p>
								{#if !stored}
									<div class="acts">
										<button
											type="button"
											class="act"
											onclick={() => (rows = rows.filter((r) => r.name !== row.name))}
											>Remove</button
										>
									</div>
								{/if}
							</div>
		{/snippet}
		{#snippet setupFoot()}
			<section class="setup">
					<div class="setup-body">
						{#if !view.configured}
							<p class="prose">
								Declarations live in <code>.herdsman/kitchen.json</code>. A minimal document is
								one harness and two model assignments — the documented first run. Adding a
								harness below writes that document when you save; no configured project's
								launch template is ever rendered by this view.
							</p>
						{/if}

						{#each rows as row, index (row.name)}
							{#if !kitchen.data?.adapters.some((a) => a.name === row.name)}
								{@render adapterEditor(row, index)}
							{/if}
						{/each}

						{#if draft === null}
							<button
								type="button"
								class="plate act add-btn"
								onclick={() => {
									draft = { name: '', argvText: '', modelArgvText: '' };
									addError = null;
								}}>Add a harness</button
							>
						{:else}
						<div class="adapter plate add" id="add-harness" transition:slide={SLIDE}>
								<p class="label adapter-head">A new adapter</p>
								<p class="field">
									<label class="label" for="add-name">Name</label>
									<input id="add-name" type="text" bind:value={draft.name} placeholder="claude" />
								</p>
								<p class="prose gloss-line" id="add-note">
									A new adapter needs its launch template. It is written to this project's
									document and never read back here.
								</p>
								<p class="field">
									<label class="label" for="add-argv">Launch template</label>
									<input
										id="add-argv"
										type="text"
										bind:value={draft.argvText}
										placeholder={draftHint ? `["${draftHint}", …, "{prompt}"]` : '["claude", "-p", "{prompt}"]'}
										aria-describedby="add-note"
									/>
								</p>
								<p class="field">
									<label class="label" for="add-model-argv">Model flag(s)</label>
									<input
										id="add-model-argv"
										type="text"
										bind:value={draft.modelArgvText}
										placeholder='["--model"]'
										aria-describedby="add-note"
									/>
								</p>
								{#if addError !== null}
									<p class="member prose" data-state="failed" role="alert">{addError}</p>
								{/if}
								<div class="acts">
									<button type="button" class="plate act" onclick={addRow}>Add to form</button>
									<button
										type="button"
										class="act"
										onclick={() => {
											draft = null;
											draftHint = '';
											addError = null;
										}}>Cancel</button
									>
								</div>
						</div>
						{/if}

						{#if anyDirty || draft !== null}
						<p class="prose gloss-line">
							Authentication is the harness's. Herdsman stores no credential, and this form has
							no field for one.
						</p>
						<p class="prose gloss-line">
							What is shown here is everything the daemon returns for this project. Launch
							templates are excluded by design, and no field is filled from a stored value.
						</p>
						{#if anyDirty}
							<p class="prose gloss-line" id="save-consequence">{SAVE_CONSEQUENCE}</p>
						{/if}
						<div class="acts">
							<button
								type="button"
								class="plate act"
								disabled={!anyDirty || saving}
								onclick={() => void save('setup')}
								aria-describedby={anyDirty ? 'save-consequence' : undefined}>{saving ? 'Saving' : 'Save'}</button
							>
						</div>
												{/if}
						{@render saveOutcomeBlock('setup')}
					</div>
			</section>
		{/snippet}
		{#snippet catalogSection()}

			<section class="catalog k3-section">
					<div class="k3-body">
						<p class="prose gloss-line">
							This catalog is what this project declares. Harness discovery never adds a model
							to it: a probe reads a version, not an inventory. A model is here because it is
							written in <code>.herdsman/kitchen.json</code>.
						</p>
						<p class="prose gloss-line">
							Unknown is a value here, not a gap to fill in. Herdsman never infers a price, a
							capability or a tier from a model's name, so a fact nobody declared stays
							unknown.
						</p>
						<p class="prose gloss-line">
							Tiers are this project's own names, read from its tier map. Nothing ranks models
							for you, and a tier written on a model row is refused — it belongs to the map.
						</p>

						{#if k3.models.length === 0}
							<p class="member prose" data-state="slack">
								<span class="label">Empty catalog</span> No model is declared yet. A pair is
								one harness with one model, and the documented first run declares two.
							</p>
						{:else}
							{#each k3.models as row, index (pairKey(row))}
								<div class="adapter plate">
									<p class="label adapter-head">
										<span class="adapter-name">{row.harness} / {row.model}</span>
										{#if !row.stored}
											<span class="member" data-state="balanced">Not yet saved</span>
										{/if}
										<span class="gloss">source: {row.source}</span>
										<button
											type="button"
											class="act row-remove"
											onclick={() => removeModel(row)}
											aria-label={`Remove ${pairKey(row)} from the catalog`}>Remove</button
										>
									</p>
									<dl class="facts">
										<div class="wide">
											<dt class="label">Price</dt>
											<dd>{priceText(row)}</dd>
										</div>
										<div>
											<dt class="label">Usage</dt>
											<dd>{seatMark[row.usage]}</dd>
										</div>
										<div>
											<dt class="label">Counting</dt>
											<dd>{seatMark[row.counting]}</dd>
										</div>
									</dl>
									<div class="tier-line">
										<label class="label" for="tier-{index}">Tier map</label>
										{#if declareTiers[pairKey(row)]}
											<input
												id="tier-{index}"
												type="text"
												value={row.tierValue}
												oninput={(event) => {
													row.tierValue = event.currentTarget.value;
													row.tierTouched = true;
												}}
												placeholder="a new tier name"
											/>
											<button
												type="button"
												class="act"
												onclick={() => {
													delete declareTiers[pairKey(row)];
													row.tierValue = '';
													row.tierTouched = true;
												}}>Use an existing name</button
											>
										{:else}
											<select
												id="tier-{index}"
												value={row.tierValue}
												onchange={(event) => {
													const picked = event.currentTarget.value;
													row.tierTouched = true;
													if (picked === '__declare__') {
														row.tierValue = '';
														declareTiers[pairKey(row)] = true;
													} else {
														row.tierValue = picked;
													}
												}}
											>
												<option value="">not mapped to a tier</option>
												{#each tierOptions as name (name)}
													<option value={name}>{name}</option>
												{/each}
												<option value="__declare__">declare a new tier name…</option>
											</select>
										{/if}
									</div>
									{#if effortLevels(view, pairKey(row)).length > 0}
										{@const levels = effortLevels(view, pairKey(row))}
										{@const only = row.effortValue.length === 1 ? row.effortValue[0] : null}
										<div class="effort-line">
											<span class="label">Effort levels</span>
											<div
												class="chips"
												role="group"
												aria-label={`Effort levels allowed for ${pairKey(row)}`}
											>
												{#each levels as level (level)}
													<button
														type="button"
														class="chip"
														aria-pressed={row.effortValue.includes(level)}
														disabled={only === level}
														title={only === level
															? 'At least one effort level must stay selected'
															: undefined}
														onclick={() => {
															row.effortValue = toggleEffort(row.effortValue, level);
															row.effortTouched = true;
														}}>{level}</button
													>
												{/each}
												<button
													type="button"
													class="chip"
													aria-pressed={allSelected(levels, row.effortValue)}
													onclick={() => {
														row.effortValue = allEfforts(levels);
														row.effortTouched = true;
													}}>All</button
												>
											</div>
										</div>
									{/if}
								</div>
							{/each}
						{/if}

						<div class="adapter plate add">
							{#if newModel === null}
								<button
									type="button"
									class="act"
									disabled={view.adapters.length === 0}
									onclick={() => {
										newModel = { harness: view.adapters[0]?.name ?? '', model: '' };
										newModelError = null;
									}}>Declare a model</button
								>
								{#if view.adapters.length === 0}
									<p class="prose gloss-line">
										No harness is declared yet, and a pair needs its harness first — declare
										one in Setting up above.
									</p>
								{/if}
							{:else}
								<p class="label adapter-head">A new model pair</p>
								<div class="fields">
									<p class="field">
										<label class="label" for="new-harness">Harness</label>
										<select id="new-harness" bind:value={newModel.harness}>
											{#each view.adapters as adapter (adapter.name)}
												<option value={adapter.name}>{adapter.name}</option>
											{/each}
										</select>
									</p>
									<p class="field">
										<label class="label" for="new-model">Model</label>
										<input id="new-model" type="text" bind:value={newModel.model} placeholder="haiku" />
									</p>
								</div>
								{#if newModelError !== null}
									<p class="member prose" data-state="failed" role="alert">{newModelError}</p>
								{/if}
								<div class="acts">
									<button type="button" class="plate act" onclick={addModel}>Add to catalog</button>
									<button
										type="button"
										class="act"
										onclick={() => {
											newModel = null;
											newModelError = null;
										}}>Cancel</button
									>
								</div>
							{/if}
						</div>

						<p class="prose gloss-line">
							Frontier tiers: {view.frontier_tiers.join(', ') || 'none declared'} — which tier
							names count as frontier for the escalation rule, read from this document. This
							view shows them and does not change them.
						</p>

						{#if anyDirty}
							<p class="prose gloss-line" id="save-consequence-catalog">{SAVE_CONSEQUENCE}</p>
						{/if}
						<div class="acts">
							<button
								type="button"
								class="plate act"
								disabled={!anyDirty || saving}
								onclick={() => void save('catalog')}
								aria-describedby={anyDirty ? 'save-consequence-catalog' : undefined}
								>{saving ? 'Saving' : 'Save'}</button
							>
						</div>
						{@render saveOutcomeBlock('catalog')}
					</div>
			</section>
		{/snippet}
		{#snippet assignmentsSection()}

			<section class="assignments k3-section">
					<div class="k3-body">
						<p class="prec" aria-label="Resolution order">
							<span class="label">Resolution order</span>
							<span class="step dim">plan override · set at dispatch</span>
							<span class="arr" aria-hidden="true">›</span>
							<span class="step">role default</span>
							<span class="arr" aria-hidden="true">›</span>
							<span class="step">initiative default</span>
							<span class="arr" aria-hidden="true">›</span>
							<span class="step dim">none: dispatch refuses</span>
						</p>
						<p class="prose gloss-line">
							A plan approved with an assignment keeps it; nothing here re-resolves it. Read from
							this project's declarations by this view, not quoted from the daemon.
						</p>

						<div class="seats-table">
							<div class="seat-row head" aria-hidden="true">
								<span class="label">Seat</span>
								<span class="label">Runs on</span>
								<span class="label">Supplied by</span>
							</div>
							<div class="seat-row">
								<label class="seat-name" for="planner-pair">Planner</label>
								<span>
									<select
										id="planner-pair"
										value={k3.planner ? pairKey(k3.planner) : ''}
										onchange={(event) => (k3.planner = parsePair(event.currentTarget.value))}
									>
										<option value="">no planner configured</option>
										{#each catalogPairs as pair (pair)}
											<option value={pair}>{pair}</option>
										{/each}
									</select>
								</span>
								<span class="levels"
									><span class="lv" class:won={k3.planner !== null}>planner default</span></span
								>
							</div>
							<div class="seat-row">
								<label class="seat-name" for="initiative-pair">Initiative</label>
								<span>
									<select
										id="initiative-pair"
										value={k3.initiative ? pairKey(k3.initiative) : ''}
										onchange={(event) => (k3.initiative = parsePair(event.currentTarget.value))}
									>
										<option value="">no executor configured</option>
										{#each catalogPairs as pair (pair)}
											<option value={pair}>{pair}</option>
										{/each}
									</select>
								</span>
								<span class="levels"
									><span class="lv" class:won={k3.initiative !== null}>initiative default</span></span
								>
							</div>
							<p class="label section roles-head">Roles</p>
							{#if rolesResource.phase === 'error'}
								<p class="member prose" data-state="failed">
									The role list could not be read from the Library, so only roles already declared
									are listed. The declaration on disk is untouched.
								</p>
							{:else if roleRows.length === 0}
								<p class="prose gloss-line">
									No role assets are authored in this project, so there is no role to assign a
									default to. Roles live in the Library; author one there and it appears here.
								</p>
							{/if}
							{#each roleRows as row, index (row.role)}
								<div class="seat-row">
									<label class="seat-name" for="role-pair-{index}">
										{row.role}
										{#if row.assignment !== null && !row.stored}
											<span class="member" data-state="balanced">Not yet saved</span>
										{/if}
									</label>
									<span>
										<select
											id="role-pair-{index}"
											value={row.assignment ? pairKey(row.assignment) : ''}
											onchange={(event) => {
												const pair = parsePair(event.currentTarget.value);
												if (pair === null) k3.roles = k3.roles.filter((item) => item.role !== row.role);
												else assignSeat(row.role, pair);
											}}
										>
											<option value="">inherit the initiative default ({pairLabel(k3.initiative)})</option>
											{#each catalogPairs as pair (pair)}
												<option value={pair}>{pair}</option>
											{/each}
										</select>
									</span>
									<span class="levels">
										<span class="lv" class:won={row.assignment !== null}>role</span>
										<span class="lv" class:won={row.assignment === null && k3.initiative !== null}>initiative</span>
									</span>
								</div>
							{/each}
						</div>
						<p class="prose gloss-line">
							Filled is the level that supplies the seat; open is the level that would take over
							if it were cleared. Any model can also be made a default from its row under
							Harnesses.
						</p>

						{#if anyDirty}
							<p class="prose gloss-line" id="save-consequence-assignments">{SAVE_CONSEQUENCE}</p>
						{/if}
						<div class="acts">
							<button
								type="button"
								class="plate act"
								disabled={!anyDirty || saving}
								onclick={() => void save('assignments')}
								aria-describedby={anyDirty ? 'save-consequence-assignments' : undefined}
								>{saving ? 'Saving' : 'Save'}</button
							>
						</div>
						{@render saveOutcomeBlock('assignments')}
					</div>
			</section>
		{/snippet}
		{#snippet fallbacksSection()}

			<section class="fallbacks k3-section">
					<div class="k3-body">
						<p class="prose gloss-line">
							This is a declaration. No run consumes it today; it is written, validated and
							kept, and the unit that reads it is not built.
						</p>
						<p class="prose gloss-line">
							What is refused is refused when you save: a candidate that would escalate a
							non-frontier primary to a frontier tier, a chain that loops, a pair that is not
							in the catalog. The daemon's words are printed as it wrote them.
						</p>

						{#if k3.chains.length === 0}
							<p class="member prose" data-state="slack">
								<span class="label">No chains</span> Nothing is declared to fall back to.
							</p>
						{:else}
							{#each k3.chains as chain, chainIndex (chainIndex)}
								<div class="adapter plate">
									<p class="label adapter-head">
										<span class="adapter-name">Primary {pairKey(chain.primary)}</span>
										{#if !chain.stored}
											<span class="member" data-state="balanced">Not yet saved</span>
										{/if}
										<button
											type="button"
											class="act row-remove"
											onclick={() =>
												(k3.chains = k3.chains.filter((_, i) => i !== chainIndex))}
											aria-label={`Remove the chain for ${pairKey(chain.primary)}`}>Remove</button
										>
									</p>
									<p class="field">
										<label class="label" for="chain-primary-{chainIndex}">Primary</label>
										<select
											id="chain-primary-{chainIndex}"
											value={pairKey(chain.primary)}
											onchange={(event) => {
												const next = parsePair(event.currentTarget.value);
												if (next !== null) chain.primary = next;
											}}
										>
											{#each catalogPairs as pair (pair)}
												<option value={pair}>{pair}</option>
											{/each}
										</select>
									</p>
									<ol class="candidates">
										{#each chain.candidates as candidate, candIndex (candIndex)}
											<li>
												<span class="label pos">{candIndex + 1}</span>
												<select
													value={pairKey(candidate)}
													aria-label={`Candidate ${candIndex + 1} for ${pairKey(chain.primary)}`}
													onchange={(event) => {
														const next = parsePair(event.currentTarget.value);
														if (next !== null) chain.candidates[candIndex] = next;
													}}
												>
													{#each candidateOptions(chain, candIndex) as pair (pair)}
														<option value={pair}>{pair}</option>
													{/each}
													{#if !catalogPairs.includes(pairKey(candidate))}
														<option value={pairKey(candidate)}>{pairKey(candidate)}</option>
													{/if}
												</select>
												<button
													type="button"
													class="act"
													disabled={candIndex === 0}
													onclick={() => moveCandidate(chain, candIndex, candIndex - 1)}>Up</button
												>
												<button
													type="button"
													class="act"
													disabled={candIndex === chain.candidates.length - 1}
													onclick={() => moveCandidate(chain, candIndex, candIndex + 1)}>Down</button
												>
												<button
													type="button"
													class="act"
													onclick={() =>
														(chain.candidates = chain.candidates.filter((_, i) => i !== candIndex))}
													aria-label={`Remove candidate ${candIndex + 1}`}>Remove</button
												>
											</li>
										{/each}
									</ol>
									{#if candidateOptions(chain).length > 0 && chain.candidates.length < catalogPairs.length - 1}
										<label class="label" for="add-candidate-{chainIndex}">Add a candidate</label>
										<select
											id="add-candidate-{chainIndex}"
											value=""
											onchange={(event) => {
												const next = parsePair(event.currentTarget.value);
												if (next !== null) chain.candidates = [...chain.candidates, next];
												event.currentTarget.value = '';
											}}
										>
											<option value="">choose a pair…</option>
											{#each candidateOptions(chain) as pair (pair)}
												<option value={pair}>{pair}</option>
											{/each}
										</select>
									{/if}
								</div>
							{/each}
						{/if}

							<button
								type="button"
								class="plate act add-btn"
								disabled={freePrimary === null}
								onclick={() => {
									if (freePrimary === null) return;
									const cut = freePrimary.indexOf('/');
									k3.chains = [
										...k3.chains,
										{
											primary: {
												harness: freePrimary.slice(0, cut),
												model: freePrimary.slice(cut + 1)
											},
											candidates: [],
											stored: false
										}
									];
								}}>Add a chain</button
							>
							{#if catalogPairs.length === 0}
								<p class="prose gloss-line">
									A chain's primary is a pair from the catalog — declare models first.
								</p>
							{:else if freePrimary === null}
								<p class="prose gloss-line">
									Every catalog pair already has a chain; each primary takes one.
								</p>
							{/if}

						{#if anyDirty}
							<p class="prose gloss-line" id="save-consequence-fallbacks">{SAVE_CONSEQUENCE}</p>
						{/if}
						<div class="acts">
							<button
								type="button"
								class="plate act"
								disabled={!anyDirty || saving}
								onclick={() => void save('fallbacks')}
								aria-describedby={anyDirty ? 'save-consequence-fallbacks' : undefined}
								>{saving ? 'Saving' : 'Save'}</button
							>
						</div>
						{@render saveOutcomeBlock('fallbacks')}
					</div>
			</section>
		{/snippet}
		{#snippet smokeSection()}

			<section class="smoke">
				<div class="fields">
					<p class="field">
						<label class="label" for="smoke-harness">Harness</label>
						<select
							class="plate"
							id="smoke-harness"
							bind:value={smokeHarness}
							disabled={smokeRunning || view.adapters.length === 0}
						>
							{#each view.adapters as adapter (adapter.name)}
								<option value={adapter.name}>{adapter.name}</option>
							{/each}
						</select>
					</p>
					<p class="field">
						<label class="label" for="smoke-model">Model</label>
						<select
							class="plate"
							id="smoke-model"
							bind:value={smokeModel}
							disabled={smokeRunning || harnessModels.length === 0}
						>
							{#each harnessModels as model (`${model.harness}/${model.model}`)}
								<option value={model.model}>{model.model}</option>
							{/each}
						</select>
					</p>
				</div>

				{#if smokeRoute === 'absent'}
					<p
						bind:this={smokeAbsentEl}
						class="member prose"
						data-state="slack"
						role="status"
						tabindex="-1"
					>
						<span class="label">Unavailable</span> Testing is this daemon's route to serve and it
						does not serve it.
					</p>
				{:else if harnessModels.length === 0 || smokeModel === ''}
					<p class="member prose" data-state="slack">
						<span class="label">Nothing to test</span> No model pair is configured for
						{smokeHarness || 'this project'}, so there is nothing to test. Pairs are declared in
						<code>.herdsman/kitchen.json</code>, and Blocking a run above names what this project
						still lacks.
					</p>
				{:else}
					{#if smokeNotice !== null}
						<p
							bind:this={smokeNoticeEl}
							class="member prose"
							data-state="failed"
							role="alert"
							tabindex="-1">{smokeNotice}</p
						>
					{/if}
					{#if !smokeArmed}
						<button
							type="button"
							class="plate act"
							disabled={smokeRunning}
							onclick={() => (smokeArmed = true)}>Run a test</button
						>
					{:else}
						<div class="arm panel plate">
							<p class="prose panel-line">
								Running this sends one fixed prompt to {smokeModel} through {smokeHarness} and
								waits up to {SMOKE_TIMEOUT} seconds for an answer. It spends that harness's model
								tokens — Herdsman cannot say how many, and the harness bills it, not Herdsman. It
								writes nothing to this project. The prompt is fixed by the daemon; nothing here
								composes it.
							</p>
							<div class="acts">
								{#if smokeRunning}
									<button type="button" class="plate act" disabled>Testing</button>
									<p class="prose gloss-line" role="status">
										Waiting on {smokeHarness} — up to {SMOKE_TIMEOUT}s.
									</p>
								{:else}
									<button type="button" class="plate act" onclick={() => void runSmoke()}
										>Send the test prompt</button
									>
									<button type="button" class="act" onclick={() => (smokeArmed = false)}
										>Cancel</button
									>
								{/if}
							</div>
						</div>
					{/if}
					{#if liveOutcome !== null}
						<p
							class="outcome member"
							data-state={liveOutcome.result.state === 'passed' ? 'seated' : 'failed'}
							role="status"
						>
							<span class="label">{SMOKE_STATE_WORD[liveOutcome.result.state]}</span>
							<span>{outcomeCopy(liveOutcome.result, SMOKE_TIMEOUT)}</span>
							<span class="detail gloss">“{liveOutcome.result.detail}”</span>
						</p>
					{/if}
				{/if}

				{#if view.smoke.results.length > 0}
					<ul class="smoke-list">
						{#each sortedResults(view.smoke.results) as result (`${result.harness}/${result.model}`)}
							<li class="member" data-state={result.state === 'passed' ? 'seated' : 'failed'}>
								<span class="label">{result.harness} / {result.model}</span>
								<span class="label">{SMOKE_STATE_WORD[result.state]}</span>
								<span>{outcomeCopy(result, SMOKE_TIMEOUT)}</span>
								<span class="detail gloss">“{result.detail}”</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if absenceOf(view.smoke) !== null}
					<p class="prose gloss-line">{absenceOf(view.smoke)}</p>
				{/if}
				<p class="prose gloss-line">
					A failed test does not make this rig unready, and a passing one does not make it ready.
					Readiness is about what is declared and resolvable; a test is about whether one model
					answered once.
				</p>
			</section>
		{/snippet}
		{#snippet notesSection()}

			{#if view.notes.length > 0}
				<section class="notes">
					<ul class="daemon-words">
						{#each view.notes as note (note)}
							<li class="member" data-state="slack">{note}</li>
						{/each}
					</ul>
				</section>
			{/if}
		{/snippet}
		{#snippet pips(reached: number, state: string, found: boolean)}
			<span
				class="pips"
				role="img"
				aria-label={found
					? 'found on PATH, not declared'
					: `observed as far as ${COURSES[Math.max(reached, 1) - 1].name.toLowerCase()}`}
			>
				{#each COURSES as course, i (course.id)}
					<span
						class="pip"
						class:on={found ? i === 1 : reached > i}
						class:broke={!found && state === 'unavailable' && i === reached}
					></span>
				{/each}
			</span>
		{/snippet}
		{#snippet heldChips(held: string[])}
			<span class="held">
				{#each held as seat (seat)}<span class="seat-chip">{seat}</span>{/each}
			</span>
		{/snippet}
		{#snippet harnessesSection()}
			<section class="register" aria-label="Harnesses">
				<p class="label rule-label band">
					<span>Added</span><span class="rule"></span><span>declared in .herdsman/kitchen.json</span>
				</p>
				{#if columns.length === 0}
					<p class="prose gloss-line">
						No harness is added to this project yet.{#if discoverable.length > 0}
							Herdsman found {discoverable.length} on this machine, listed under Discoverable.{/if}
					</p>
				{:else}
					<p class="gloss legend">
						Pips are observed evidence: {COURSES.map((course) => course.name.toLowerCase()).join(' · ')}.
					</p>
				{/if}
				<ul class="hlist">
					{#each columns as column, index (column.harness)}
						{@const open = selected === column.harness}
						{@const rowIndex = rows.findIndex((row) => row.name === column.harness)}
						{@const models = k3.models.filter((m) => m.harness === column.harness)}
						<li>
							<button
								type="button"
								class="hrow member"
								data-state={memberState(column.state)}
								aria-expanded={open}
								aria-controls="harness-{index}"
								onclick={() => (selected = open ? null : column.harness)}
							>
								<span class="chev" aria-hidden="true"></span>
								<span class="hname">{column.harness}</span>
								{@render pips(column.reached, column.state, false)}
								<span class="hfacts">
									{column.observed?.version ?? stateWord[column.state]} · {models.length}
									{models.length === 1 ? 'model' : 'models'}
								</span>
								{@render heldChips(seatsHeld(column.harness))}
							</button>
							{#if open}
								<div class="hbody" id="harness-{index}" transition:slide={SLIDE}>
									<p class="label section">Models</p>
									{#if models.length === 0}
										<p class="prose gloss-line">
											No model is declared for {column.harness}. Declare one under Models.
										</p>
									{:else}
										<ul class="mlist">
											{#each models as m (pairKey(m))}
												{@const held = seatsHeld(m.harness, m.model)}
												<li class="mrow">
													<span class="mname">{m.model}</span>
													<span class="tier">{m.tierValue || 'no tier'}</span>
													<span class="range">{effortRange(view, m)}</span>
													{@render heldChips(held)}
													<select
														class="set-default"
														aria-label={`Make ${pairKey(m)} the default for a seat`}
														value=""
														onchange={(event) => {
															const seat = event.currentTarget.value;
															if (seat !== '') assignSeat(seat, { harness: m.harness, model: m.model });
															event.currentTarget.value = '';
														}}
													>
														<option value="">Set default…</option>
														<option value="planner" disabled={held.includes('planner')}
															>planner · now {pairLabel(k3.planner)}</option
														>
														<option value="initiative" disabled={held.includes('initiative')}
															>initiative · now {pairLabel(k3.initiative)}</option
														>
														{#each roleRows as role (role.role)}
															<option value={role.role} disabled={held.includes(role.role)}
																>{role.role} · now {role.assignment
																	? pairKey(role.assignment)
																	: 'inherits initiative'}</option
															>
														{/each}
													</select>
												</li>
											{/each}
										</ul>
									{/if}

									{@render readingSection(column)}

									{#if rowIndex >= 0}
										<p class="label section">Declaration</p>
										{@render adapterEditor(rows[rowIndex], rowIndex)}
									{/if}
								</div>
							{/if}
						</li>
					{/each}
				</ul>

				{@render setupFoot()}

				<p class="label rule-label band">
					<span>Discoverable</span><span class="rule"></span><span>on PATH, not added · nothing run</span>
				</p>
				{#if view.discoverable === undefined}
					<p class="prose gloss-line">
						This daemon does not look for undeclared harnesses. Run a newer daemon to see them.
					</p>
				{:else if discoverable.length === 0}
					<p class="prose gloss-line">No other known harness is on PATH.</p>
				{:else}
					<ul class="hlist">
						{#each discoverable as found (found.harness)}
							<li class="hrow disc">
								<span class="chev" aria-hidden="true"></span>
								<span class="hname">{found.harness}</span>
								{@render pips(0, 'unknown', true)}
								<span class="hfacts">{found.executable}</span>
								<span class="held">
									<button
										type="button"
										class="act"
										onclick={async () => {
											addDiscovered(found.harness, found.executable);
											await tick();
											document.getElementById('add-argv')?.focus();
										}}>Add</button
									>
								</span>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/snippet}
		{#snippet readingSection(current: Column)}
				{@const reach = reachOf(view.smoke, current.harness)}
					<div class="reading">
						<p class="label section">Observed</p>
						{#if current.observed === null}
							<p class="prose">
								Nothing observed. No probe has touched this harness since the daemon started,
								so every probe reading below is absent rather than negative.
							</p>
							{#if reachShown}
								<dl class="facts">
									<div class="wide">
										<dt class="label">Reach</dt>
										<dd>{reachValue(reach)}</dd>
									</div>
								</dl>
								<p class="prose gloss-line">
									A failed test does not make this rig unready, and a passing one does not make it
									ready. Readiness is about what is declared and resolvable; a test is about
									whether one model answered once.
								</p>
							{/if}
						{:else}
							{@const seen = current.observed}
							<dl class="facts">
								<div class="wide">
									<dt class="label">Executable</dt>
									<dd class="path">{seen.executable ?? 'not found'}</dd>
								</div>
								<div>
									<dt class="label">Version</dt>
									<dd>{seen.version ?? 'none read'}</dd>
								</div>
								<div>
									<dt class="label">Health</dt>
									<dd>{seen.health}</dd>
								</div>
								{#if reachShown}
									<div>
										<dt class="label">Reach</dt>
										<dd>{reachValue(reach)}</dd>
									</div>
								{/if}
								{#if seen.detail}
									<div class="wide">
										<dt class="label">The probe's words</dt>
										<dd class="quote">{seen.detail}</dd>
									</div>
								{/if}
							</dl>
							{#if reachShown}
								<p class="prose gloss-line">
									A failed test does not make this rig unready, and a passing one does not make it
									ready. Readiness is about what is declared and resolvable; a test is about
									whether one model answered once.
								</p>
							{/if}
						{/if}

						<p class="label section">Not observed</p>
						{#if reach !== null}
							<p class="prose">
								Credentials. A test proves this harness reached its provider once; it never shows
								Herdsman a credential. Sign-in is the harness's to hold — Herdsman reads none,
								stores none and shows none. This view never renders a harness's declared launch
								template for the same reason — a flag can carry a secret — so the resolved
								executable is the only command-line fact it holds.
							</p>
						{:else}
							<p class="prose">
								Authentication. A version probe proves the executable runs, not that it can reach
								a provider — <code>--version</code> never signs in. Herdsman reads no credential
								for any harness and shows none here; check sign-in inside {current.harness}
								itself. This view never renders a harness's declared launch template for the
								same reason — a flag can carry a secret — so the resolved executable is the
								only command-line fact it holds. Testing a model is the only thing on this page
								that reaches a provider.
							</p>
						{/if}

						{#if current.reason || current.action}
							<p class="label section">Next</p>
							<div class="next member" data-state={memberState(current.state)}>
								{#if current.reason}<p class="why">{current.reason}</p>{/if}
								{#if current.action}<p class="do">{current.action}</p>{/if}
							</div>
						{/if}
					</div>
		{/snippet}






			<div class="sheet-cap">
				<p class="label rule-label">
					<span>Kitchen</span>
					<span class="rule"></span>
					<span
						class="member"
						data-state={kitchen.phase === 'error'
							? 'failed'
							: kitchen.stale
								? 'slack'
								: rig.unavailable > 0
									? 'failed'
									: view.ready
										? 'seated'
										: 'balanced'}
					>
						{headline()}{#if discoverable.length > 0}&nbsp;· {discoverable.length} discoverable{/if}
					</span>
				</p>
				{#if probeRoute === 'absent'}
					<p class="member prose" data-state="slack">
						<span class="label">Unavailable</span>
						Measuring is this daemon's route to serve and it does not serve it.
					</p>
				{:else}
					<button
						type="button"
						class="plate act"
						onclick={() => void probe()}
						disabled={probing || !view.configured}
						aria-describedby="measure-gloss"
					>
						{probing ? 'Measuring' : 'Measure'}
					</button>
				{/if}
			</div>
			<p id="measure-gloss" class="gloss measure-gloss">{measured(view)}. Measuring resolves each added
				executable and runs one bounded <code>--version</code>; it writes nothing.</p>

			<p
				bind:this={outcomeEl}
				class="outcome member"
				data-state={outcome === null ? 'balanced' : outcome.ok ? 'seated' : 'failed'}
				role="status"
				tabindex="-1"
			>
				{#if outcome}
					<span class="label">{outcome.ok ? 'Measured' : 'Not measured'}</span>
					<span>{outcome.message}</span>
				{/if}
			</p>

			{#if view.blockers.length > 0}
				<div class="blockers">
					<p class="label rule-label"><span>Blocking a run</span><span class="rule"></span><span class="member" data-state="failed">{view.blockers.length}</span></p>
					<ul class="daemon-words">{#each view.blockers as blocker (blocker)}<li class="member" data-state="failed">{blocker}</li>{/each}</ul>
				</div>
			{/if}

			<div class="spine">
				<nav class="index" aria-label="Kitchen sections">
					{#each index as entry (entry.id)}
						<button
							type="button"
							class="ix"
							aria-current={section === entry.id ? 'true' : undefined}
							aria-controls="kitchen-pane"
							onclick={() => (section = entry.id)}
						>
							<span class="ix-no label">{entry.no}</span>
							<span class="ix-name">{entry.label}</span>
							<span class="ix-sum member" data-state={entry.state}>{entry.summary}</span>
						</button>
					{/each}
					{#if view.notes.length > 0}
						<button
							type="button"
							class="ix ix-foot"
							aria-current={section === 'notes' ? 'true' : undefined}
							aria-controls="kitchen-pane"
							onclick={() => (section = 'notes')}
						>
							<span class="ix-no label"></span>
							<span class="ix-name">Provenance</span>
							<span class="ix-sum">{view.notes.length} {view.notes.length === 1 ? 'note' : 'notes'} · rev {view.revision.slice(0, 8)}</span>
						</button>
					{:else}
						<p class="ix ix-foot">
							<span class="ix-no label"></span>
							<span class="ix-sum">.herdsman/kitchen.json · rev {view.revision.slice(0, 8) || 'none'}</span>
						</p>
					{/if}
				</nav>

				<div class="pane" id="kitchen-pane">
					{#key section}
					<div in:fly={SETTLE}>
					{#if section === 'harnesses'}{@render harnessesSection()}
					{:else if section === 'catalog'}{@render catalogSection()}
					{:else if section === 'assignments'}{@render assignmentsSection()}
					{:else if section === 'fallbacks'}{@render fallbacksSection()}
					{:else if section === 'smoke'}{@render smokeSection()}
					{:else}{@render notesSection()}{/if}
					</div>
					{/key}
				</div>
			</div>
		{/snippet}
	</AsyncField>
</section>

<style>
	.kitchen { min-width: 0; }

	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}

	.outcome {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 0 0 1.5rem;
		color: var(--member-ink);
	}
	.outcome .label {
		color: var(--member-ink);
	}
	/* Present in the accessibility tree from first paint — a live region created
	   at the moment of the change is not reliably announced — and taking no
	   space until it has something to report. */
	.outcome:empty {
		height: 0;
		margin: 0;
		overflow: hidden;
	}

	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		color: var(--member-ink, var(--ink));
	}
	.gloss {
		margin: 0.3rem 0 0;
		font-size: 0.625rem;
		letter-spacing: 0.06em;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.gloss-line {
		margin: 0.75rem 0 0;
		font-size: 0.75rem;
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
		cursor: pointer;
	}
	.act:hover:not(:disabled) {
		border-color: var(--red);
		color: var(--red);
	}
	.act:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: default;
	}

	/* --- the reading inside the seat ----------------------------------------- */
	.section {
		margin: 1.25rem 0 0.5rem;
		padding-top: 0.5rem;
		border-top: 1px solid var(--rule);
	}
	.facts > div {
		display: grid;
		grid-template-columns: 6rem minmax(0, 1fr);
		gap: 0.5rem;
		padding: 0.2rem 0;
	}
	/* Wide rows keep the same two-column label-left alignment as every other
	   fact — K2's U5(d): the three observed facts and the probe's words share
	   one alignment, and a long path still gets the full row to wrap in. */
	.facts .wide {
		grid-template-columns: 6rem minmax(0, 1fr);
	}
	.path,
	.quote {
		overflow-wrap: anywhere;
		color: var(--ink);
	}
	.quote {
		border-left: 1px solid var(--rule-strong);
		padding-left: 0.6rem;
		color: var(--ink-2);
	}
	.next {
		color: var(--member-ink);
	}
	.next .why {
		margin: 0;
	}
	.next .do {
		margin: 0.35rem 0 0;
		color: var(--ink);
	}

	/* --- the daemon's own sentences ------------------------------------------ */
	.blockers { margin-top: 1.5rem; }
	.daemon-words {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.daemon-words li {
		padding: 0.4rem 0 0.4rem 0.9rem;
		border-left: 1px solid var(--member-ink);
		color: var(--member-ink);
	}
	.daemon-words li + li {
		margin-top: 0.4rem;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}

	/* --- setting up: the one write path -------------------------------------- */
	.setup-body {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
		margin-top: 0.75rem;
	}
	.adapter {
		--cut: 12px;
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 1rem 1.25rem;
	}
	.adapter-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 0.75rem;
	}
	.adapter-name {
		font-size: 1rem;
		letter-spacing: 0.02em;
		text-transform: none;
		color: var(--ink);
	}
	/* Floored at 13rem — a definite floor sized to the widest declaration a
	   select can hold ("declared unsupported"), so it never clips to
	   "declared unsuppo…". min-content is not a valid auto-fit minimum. */
	.cap-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
		gap: 0.75rem 1rem;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 16rem));
		gap: 0.75rem 1rem;
		margin-bottom: 1rem;
	}
	.field {
		margin: 0;
	}
	.field .label {
		display: block;
		margin-bottom: 0.3rem;
	}
	/* Same control vocabulary as the intervention form: plate-backed, chamfered,
	   red on focus, never-allowed for a closed list. */
	input[type='text'],
	select {
		--cut: 10px;
		font: inherit;
		width: 100%;
		box-sizing: border-box;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		padding: 0.45rem 0.7rem;
	}
	select {
		appearance: none;
		padding-right: 2.25rem;
	}
	select:disabled,
	input:disabled {
		color: var(--ink-2);
		border-color: var(--rule);
		cursor: not-allowed;
	}
	input[type='text']:focus-visible,
	select:focus-visible {
		border-color: var(--red);
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.75rem 1rem;
	}
	.save-outcome {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		color: var(--member-ink);
	}
	.save-outcome .label {
		color: var(--member-ink);
	}
	.detail-lines {
		white-space: pre-line;
		overflow-wrap: anywhere;
	}

	/* --- testing a model ------------------------------------------------------ */
	.arm {
		--cut: 12px;
		margin-top: 0.75rem;
		padding: 1rem 1.25rem;
		background: var(--plate);
		border: 1px solid var(--rule-strong);
	}
	.panel-line {
		margin: 0;
	}
	.arm .acts {
		margin-top: 0.9rem;
	}
	.smoke-list {
		list-style: none;
		margin: 1.25rem 0 0;
		padding: 0;
	}
	.smoke-list li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		padding: 0.5rem 0;
		border-top: 1px solid var(--rule);
		color: var(--member-ink);
	}
	.smoke-list .label {
		color: var(--ink);
	}
	/* The daemon's own words, on their own line: quoted, never fused into copy. */
	.smoke-list .detail,
	.outcome .detail {
		flex-basis: 100%;
		border-left: 1px solid var(--rule-strong);
		padding-left: 0.6rem;
		color: var(--ink-2);
		overflow-wrap: anywhere;
	}

	/* --- K3: the three declaration editors ---------------------------------- */
	.k3-body {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
		margin-top: 0.75rem;
	}
	.row-remove {
		margin-left: auto;
	}
	.tier-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
		margin-top: 0.9rem;
	}
	.tier-line .label {
		flex: none;
	}
	.tier-line select,
	.tier-line input {
		flex: 1 1 12rem;
		min-width: 0;
	}
	/* The effort pool, as the Memory shelf's chips: one row per pair, the
	   uppercase vocabulary with the selected level ruled underneath. Copied
	   rather than extracted — the shelf's block is a filter, this one is an
	   editor, and a shared class would tie two unrelated meanings together. */
	.effort-line {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem 0.75rem;
		margin-top: 0.65rem;
	}
	.effort-line .label {
		flex: none;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
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
	.chip:hover:not(:disabled) {
		color: var(--red);
	}
	.chip[aria-pressed='true'] {
		color: var(--ink);
		border-bottom-color: var(--member-line);
	}
	/* The last selected level is the ≥1 floor: it stays pressed and stays put. */
	.chip:disabled {
		cursor: default;
	}
	.candidates {
		list-style: none;
		margin: 0.9rem 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.candidates li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}
	.candidates .pos {
		flex: none;
		width: 1.25rem;
		text-align: right;
	}
	.candidates select {
		flex: 1 1 12rem;
		min-width: 0;
	}
	.candidates .act,
	.adapter-head .act {
		padding: 0.2rem 0.55rem;
		font-size: 0.625rem;
	}
	.add {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.75rem;
	}
	.add .label {
		margin: 0;
	}
	.add select {
		width: min(22rem, 100%);
	}

	/* --- the sheet cap: one ridden headline and the one probe control ------- */
	.sheet-cap {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem 1.25rem;
	}
	.sheet-cap .rule-label {
		flex: 1 1 20rem;
		margin: 0;
	}
	.measure-gloss {
		margin: 0.5rem 0 1.25rem;
		max-width: 68ch;
	}
	.blockers {
		margin-bottom: 1.5rem;
	}

	/* --- the index spine ------------------------------------------------------
	   The index is the sheet's first read: display type, each entry carrying
	   its own section's state, the current one tied to the pane by a carbon
	   member on the spine's edge. Location is carbon, never red. */
	.spine {
		display: grid;
		grid-template-columns: 15.5rem minmax(0, 1fr);
		gap: 2.5rem;
		align-items: start;
	}
	.index {
		display: grid;
		border-right: 1px solid var(--rule);
		position: sticky;
		top: 1rem;
	}
	.ix {
		display: grid;
		grid-template-columns: 1.75rem minmax(0, 1fr);
		gap: 0.15rem 0.25rem;
		position: relative;
		margin: 0;
		padding: 0.9rem 1.25rem 0.9rem 0;
		border: 0;
		border-bottom: 1px solid var(--rule);
		background: transparent;
		font: inherit;
		text-align: left;
		color: var(--ink-2);
	}
	button.ix {
		cursor: pointer;
	}
	.ix-no {
		line-height: 2;
	}
	.ix-name {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 72, 'wght' 620;
		font-weight: 620;
		font-size: 1.25rem;
		line-height: 1.1;
		text-transform: uppercase;
		color: var(--ink-2);
	}
	.ix-sum {
		grid-column: 2;
		font-size: 0.6875rem;
		color: var(--member-ink, var(--ink-2));
	}
	/* Slack is graphite plus a dashed ash rule, never ash type. */
	.ix-sum[data-state='slack'] {
		color: var(--ink-2);
		text-decoration: underline dashed var(--ash);
		text-underline-offset: 0.3em;
	}
	.ix-sum[data-state='balanced'],
	.ix-sum[data-state='seated'] {
		color: var(--ink-2);
	}
	button.ix:hover .ix-name {
		color: var(--red);
	}
	.ix[aria-current='true'] .ix-name {
		color: var(--ink);
	}
	.ix[aria-current='true']::after {
		content: '';
		position: absolute;
		top: -1px;
		bottom: -1px;
		right: -1.5px;
		width: 2px;
		background: var(--member-line);
	}
	.ix-foot {
		border-bottom: 0;
	}
	.ix-foot .ix-name {
		font: inherit;
		font-size: 0.75rem;
		text-transform: none;
	}
	.pane {
		min-width: 0;
	}
	.add-btn {
		align-self: flex-start;
	}

	/* --- the harness register ---------------------------------------------- */
	.band {
		margin: 0 0 0.35rem;
	}
	.register .band ~ .band {
		margin-top: 2rem;
	}
	.legend {
		margin: 0 0 0.5rem;
	}
	.hlist {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.hrow {
		display: grid;
		grid-template-columns: 1rem 9rem 7rem minmax(0, 1fr) auto;
		gap: 1rem;
		align-items: center;
		width: 100%;
		margin: 0;
		padding: 0.65rem 0;
		border: 0;
		border-bottom: 1px solid var(--rule);
		background: transparent;
		font: inherit;
		text-align: left;
		color: var(--ink);
	}
	button.hrow {
		cursor: pointer;
	}
	button.hrow:hover .hname {
		color: var(--red);
	}
	.hrow[data-state='failed'] .hname {
		color: var(--red);
	}
	.hrow.disc {
		border-bottom-style: dashed;
		color: var(--ink-2);
	}
	.chev {
		width: 0.45rem;
		height: 0.45rem;
		border-right: 1px solid var(--ink-2);
		border-bottom: 1px solid var(--ink-2);
		transform: rotate(-45deg);
		justify-self: start;
	}
	.hrow[aria-expanded='true'] .chev {
		transform: rotate(45deg);
	}
	.disc .chev {
		visibility: hidden;
	}
	.hname {
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.hfacts {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.75rem;
		color: var(--ink-2);
	}
	.pips {
		display: flex;
		gap: 3px;
	}
	/* Observed evidence in the member's own ink: filled carbon is proven, a
	   dashed ash rung is not reached, and red marks where the path broke. */
	.pip {
		width: 1.35rem;
		height: 6px;
		border: 1px dashed var(--ash);
	}
	.pip.on {
		border: 1px solid var(--ink);
		background: var(--ink);
	}
	.pip.broke {
		border: 1px solid var(--red);
		background: var(--red);
	}
	.held {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 0.3rem;
	}
	.seat-chip {
		font-size: 0.5625rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		border: 1px solid var(--ink);
		padding: 0.2rem 0.4rem;
		white-space: nowrap;
	}
	.hbody {
		padding: 0.25rem 0 1.25rem 2rem;
		border-bottom: 1px solid var(--rule);
	}
	.mlist {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.mrow {
		display: grid;
		grid-template-columns: minmax(8rem, 1.2fr) 6.5rem minmax(0, 1fr) auto auto;
		gap: 0.9rem;
		align-items: center;
		padding: 0.45rem 0;
		border-bottom: 1px dotted var(--rule);
		font-size: 0.8125rem;
	}
	.mname {
		overflow-wrap: anywhere;
	}
	.tier,
	.range {
		font-size: 0.6875rem;
		color: var(--ink-2);
	}
	.tier {
		border: 1px solid var(--rule-strong);
		padding: 0.15rem 0.4rem;
		justify-self: start;
		white-space: nowrap;
	}
	.range {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.set-default {
		width: auto;
		max-width: 14rem;
		font-size: 0.6875rem;
		padding: 0.25rem 0.5rem;
	}

	/* --- assignments: precedence drawn once, the winning level filled ------- */
	.prec {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem 0.5rem;
		margin: 0 0 0.75rem;
		font-size: 0.75rem;
	}
	.prec .step {
		border: 1px solid var(--ink);
		padding: 0.3rem 0.55rem;
	}
	.prec .step.dim {
		border: 1px dashed var(--rule-strong);
		color: var(--ink-2);
	}
	.prec .arr {
		color: var(--ink-2);
	}
	.seats-table {
		margin-top: 1.25rem;
	}
	.seat-row {
		display: grid;
		grid-template-columns: minmax(7rem, 11rem) minmax(0, 1fr) 13rem;
		gap: 1rem;
		align-items: center;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--rule);
	}
	.seat-row.head {
		border-bottom-color: var(--ink);
	}
	.seat-row select {
		width: 100%;
	}
	.seat-name {
		display: grid;
		gap: 0.2rem;
		font-weight: 500;
	}
	.roles-head {
		margin-top: 1.5rem;
	}
	.levels {
		display: inline-flex;
		gap: 2px;
	}
	.lv {
		font-size: 0.5625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		padding: 0.25rem 0.4rem;
		border: 1px dashed var(--rule-strong);
		color: var(--ink-2);
		white-space: nowrap;
	}
	.lv.won {
		border: 1px solid var(--ink);
		background: var(--ink);
		color: var(--plate);
	}

	/* Below the shell breakpoint the spine turns into a strip across the top
	   of the sheet; rows keep their order and drop their widest columns. */
	@media (max-width: 60rem) {
		.spine {
			grid-template-columns: minmax(0, 1fr);
			gap: 1.5rem;
		}
		.index {
			position: static;
			display: flex;
			overflow-x: auto;
			border-right: 0;
			border-bottom: 1px solid var(--rule);
			scrollbar-width: thin;
		}
		.ix {
			flex: none;
			grid-template-columns: auto;
			padding: 0.5rem 1rem 0.6rem 0;
			border-bottom: 0;
		}
		.ix-no {
			display: none;
		}
		.ix-sum {
			grid-column: 1;
		}
		.ix[aria-current='true']::after {
			top: auto;
			left: 0;
			right: 1rem;
			bottom: -1px;
			width: auto;
			height: 2px;
		}
		.ix-foot {
			display: none;
		}
		.hrow {
			grid-template-columns: 1rem minmax(0, 1fr) auto;
			gap: 0.35rem 0.75rem;
		}
		.hrow .hfacts {
			grid-column: 2 / -1;
		}
		.hrow .held {
			grid-column: 2 / -1;
			justify-content: flex-start;
		}
		.hbody {
			padding-left: 0;
		}
		.mrow {
			grid-template-columns: minmax(0, 1fr) auto;
		}
		.mrow .range,
		.mrow .held,
		.mrow .set-default {
			grid-column: 1 / -1;
			justify-content: flex-start;
		}
		.seat-row {
			grid-template-columns: minmax(0, 1fr);
			gap: 0.35rem;
		}
		.seat-row.head {
			display: none;
		}
	}
</style>
