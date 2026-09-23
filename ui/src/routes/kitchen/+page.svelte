<!--
K1 — THE RIG ELEVATION

Kitchen is the local machine drawn once, as an elevation. Every declared harness
is a column standing on one base line — the project — and its height is exactly
how far one bounded `--version` probe actually carried it: declared, found,
answered, versioned. A column that stops two courses short is visibly short
against the ghost of the courses it did not reach, so "can this machine run my
agents right now" is a silhouette rather than a table to read.

The one distinction this surface exists to hold is geometric, not editorial:
height is observed, seats are declared. Nothing in the daemon checks a declared
capability, so a claim never raises a column, and a column never implies a claim.

The declaration editors this unit leaves unbuilt stay unbuilt: Dispatch and a
Settings screen (product scope), and anything CLI. What K3 leaves is named in
its own record — this surface renders no plan (the ladder is explained from
this project's declarations, not from Run's plans), infers no price or
capability from a name, and mirrors no server-side refusal rule. Discovery
here is read-only over global configuration and writes nothing anywhere.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import MarginSheet, { type MarginSection } from '$lib/MarginSheet.svelte';
	import DrawerSeat from '$lib/DrawerSeat.svelte';
	import type { SeatWidth } from '$lib/seat.svelte';
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
		classifySaveFailure,
		columnsOf,
		editsDirty,
		editsFrom,
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

	/* The courses are drawn bottom-up and annotated top-down, on one rhythm the
	   ladder and the columns both measure from. */
	const BAND = 52;
	const BASE = 236;
	const ladder = [...COURSES].reverse();

	let selected = $state<string | null>(null);
	let probing = $state(false);
	let probedAt = $state<Date | null>(null);
	let outcome = $state<{ ok: boolean; message: string } | null>(null);
	/* `POST /kitchen/discovery` is Sprint 8's; a daemon that predates it answers
	   404 and the control becomes an unavailable action rather than a dead
	   button that fails the same way every time it is pressed. */
	let probeRoute = $state<'present' | 'absent'>('present');
	let outcomeEl = $state<HTMLParagraphElement | null>(null);
	/* The strip is measured rather than assumed: a fade that is always on lies
	   about scrollable content when three columns fit, and one that is never on
	   cuts the last harness off mid-word at 390. */
	let strip = $state<HTMLDivElement | null>(null);
	let scrollable = $state(false);

	const columns = $derived(kitchen.data ? columnsOf(kitchen.data) : []);
	const rig = $derived(rigReading(columns));
	const at = $derived(Math.max(columns.findIndex((c) => c.harness === selected), 0));
	const current = $derived(columns[at] ?? null);
	/* The Reach row: present exactly while the daemon holds any result, valued
	   from *this* harness's newest one — never an aggregate, never borrowed. */
	const reach = $derived(
		kitchen.data && current ? reachOf(kitchen.data.smoke, current.harness) : null
	);
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

	/* The columns are a tab strip over one reading: arrow keys move along the
	   elevation and carry focus with the selection, so the drawing is navigable
	   without a pointer and the panel is announced as the thing it belongs to. */
	function onKeys(event: KeyboardEvent): void {
		const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
		if (!keys.includes(event.key) || columns.length === 0) return;
		event.preventDefault();
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? columns.length - 1
					: (at + (event.key === 'ArrowRight' ? 1 : columns.length - 1)) % columns.length;
		selected = columns[next].harness;
		openSection = 'reading';
		const strip = (event.currentTarget as HTMLElement).parentElement;
		(strip?.querySelectorAll('[role="tab"]')[next] as HTMLElement | undefined)?.focus();
	}

	/* Selection survives every re-read: it is keyed on a harness name, never on
	   a position or a state, so a probe that changes what a column says cannot
	   move the operator to a different column while they are reading it. */
	$effect(() => {
		if (columns.length === 0) return;
		if (selected !== null && columns.some((c) => c.harness === selected)) return;
		selected = columns[0].harness;
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
			modelArgvTouched: false
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
				modelArgvTouched: old.modelArgvTouched
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
	let openSection = $state<string | null>(null);
	let sectionsSeeded = false;
	let seatWidth = $state<SeatWidth>('wide');
	$effect(() => {
		const section = openSection;
		if (section !== null) seatWidth = section === 'reading' ? 'docked' : 'wide';
	});
	$effect(() => {
		const view = kitchen.data;
		if (!view || sectionsSeeded) return;
		sectionsSeeded = true;
		if (!view.configured) openSection = 'setup';
	});
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
	const availableRoles = $derived(
		roleNames.filter((name) => !k3.roles.some((row) => row.role === name))
	);

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
				modelArgvTouched: true
			}
		];
		draft = null;
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
			if ((row.argvTouched && argv === null) || (row.modelArgvTouched && modelArgv === null)) {
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
			edits.push({ name: row.name, capabilities: row.capabilities, argv, model_argv: modelArgv });
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

	/* --- the one authored motion ---------------------------------------------
	   A column takes up load when a probe really raised it between two reads,
	   and at no other time. The height is the whole claim of this drawing, so
	   the moment it changes is the moment worth animating; a column that was
	   already standing does not re-enact its own probe on every read. */
	const lastHeight = new Map<string, number>();
	let rising = $state<string[]>([]);
	$effect(() => {
		const drawn = columns;
		if (drawn.length === 0) return;
		const grew: string[] = [];
		for (const column of drawn) {
			const before = lastHeight.get(column.harness);
			if (before !== undefined && column.reached > before) grew.push(column.harness);
			lastHeight.set(column.harness, column.reached);
		}
		if (grew.length === 0) return;
		rising = grew;
		const done = setTimeout(() => (rising = []), 360);
		return () => clearTimeout(done);
	});

	$effect(() => {
		const el = strip;
		if (!el) return;
		// Read on every re-render of the strip's contents, and on resize.
		void columns.length;
		const measure = () => {
			scrollable = el.scrollWidth - el.clientWidth > 1;
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		return () => observer.disconnect();
	});

	const headY = (column: Column): number => BASE - column.reached * BAND;

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

	const sections: MarginSection[] = $derived([
		{ id: 'setup', label: 'Setting up' },
		{ id: 'catalog', label: 'Model catalog', count: `${k3.models.length} models`, state: k3.models.length ? 'balanced' : 'slack' },
		{ id: 'assignments', label: 'Assignments' },
		{ id: 'fallbacks', label: 'Fallbacks', count: k3.chains.length > 0 ? `${k3.chains.length} fallbacks` : undefined, state: k3.chains.length ? 'balanced' : undefined },
		{ id: 'smoke', label: 'Testing a model' },
		{ id: 'notes', label: 'Provenance', hidden: !kitchen.data || kitchen.data.notes.length === 0 }
	]);
	const seatTitle = $derived(openSection === 'reading' ? (current?.harness ?? 'Rig reading') : sections.find((section) => section.id === openSection)?.label ?? 'Kitchen');
	const seatTag = $derived(openSection === 'reading' ? (current ? stateWord[current.state] : '') : openSection === 'catalog' ? `${k3.models.length} models` : openSection === 'fallbacks' && k3.chains.length > 0 ? `${k3.chains.length} fallbacks` : '');

	const columnLabel = (column: Column): string => {
		const course = COURSES[column.reached - 1];
		const claims = column.seats.filter((s) => s.state !== 'unknown').length;
		return `${column.harness} — ${stateWord[column.state]}, observed as far as ${course.name.toLowerCase()}, ${claims} of ${column.seats.length} capabilities declared`;
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
		{#snippet setupSection()}

			<section class="setup">
					<div class="setup-body">
						{#if !view.configured}
							<p class="prose">
								Declarations live in <code>.herdsman/kitchen.json</code>. A minimal document is
								one harness and two model assignments — the documented first run. The form below
								writes that document; nothing else on this page writes anything, and no
								configured project's launch template is ever rendered by this view.
							</p>
						{/if}

						{#each rows as row, index (row.name)}
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
						{/each}

						<div class="adapter plate add">
							{#if draft === null}
								<button
									type="button"
									class="act"
									onclick={() => {
										draft = { name: '', argvText: '', modelArgvText: '' };
										addError = null;
									}}>Add a harness</button
								>
							{:else}
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
										placeholder={'["claude", "-p", "{prompt}"]'}
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
											addError = null;
										}}>Cancel</button
									>
								</div>
							{/if}
						</div>

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
						<p class="prose gloss-line">
							An executor is chosen in one order: a plan's own override wins; otherwise the
							role default for that role; otherwise the initiative default below. A plan
							approved with an assignment keeps it — nothing here re-resolves it afterwards.
						</p>
						<p class="prose gloss-line">
							Read from this project's declarations by this view, not quoted from the daemon.
						</p>
						<div class="fields">
							<p class="field">
								<label class="label" for="planner-pair">Planner</label>
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
							</p>
							<p class="field">
								<label class="label" for="initiative-pair">Initiative executor</label>
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
							</p>
						</div>
						<p class="prose gloss-line">The initiative default is required before this project is ready, and it is the executor a run falls back to. Choosing an executor per initiative is not built yet, so today this is the only executor a plan gets.</p>

						<p class="label section">Role defaults</p>
						{#each k3.roles as row, index (row.role)}
							<div class="adapter plate">
								<p class="label adapter-head">
									<span class="adapter-name">{row.role}</span>
									{#if !row.stored}
										<span class="member" data-state="balanced">Not yet saved</span>
									{/if}
									<button
										type="button"
										class="act row-remove"
										onclick={() => (k3.roles = k3.roles.filter((item) => item.role !== row.role))}
										aria-label={`Remove the ${row.role} role default`}>Remove</button
									>
								</p>
								<p class="prose gloss-line">
									This is a declaration. No run consumes it today; it is written, validated and
									kept, and the unit that reads it is not built.
								</p>
								<p class="field">
									<label class="label" for="role-pair-{index}">Pair</label>
									<select
										id="role-pair-{index}"
										value={row.assignment ? pairKey(row.assignment) : ''}
										onchange={(event) =>
											(row.assignment = parsePair(event.currentTarget.value))}
									>
										<option value="">choose a pair from the catalog</option>
										{#each catalogPairs as pair (pair)}
											<option value={pair}>{pair}</option>
										{/each}
									</select>
								</p>
							</div>
						{/each}

						<div class="adapter plate add">
							{#if rolesResource.phase === 'error'}
								<p class="member prose" data-state="failed">
									The role list could not be read from the Library, so no role can be chosen
									here right now. The declaration on disk is untouched.
								</p>
							{:else if roleNames.length === 0}
								<p class="prose gloss-line">
									No role assets are authored in this project, so there is no role to assign a
									default to. Roles live in the Library; author one there and it appears here.
								</p>
							{:else if availableRoles.length > 0}
								<label class="label" for="add-role">Add a role default</label>
								<select
									id="add-role"
									value=""
									onchange={(event) => {
										const role = event.currentTarget.value;
										if (role !== '')
											k3.roles = [...k3.roles, { role, assignment: null, stored: false }];
										event.currentTarget.value = '';
									}}
								>
									<option value="">choose a role…</option>
									{#each availableRoles as role (role)}
										<option value={role}>{role}</option>
									{/each}
								</select>
							{/if}
						</div>

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

						<div class="adapter plate add">
							<button
								type="button"
								class="act"
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
						</div>

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
		{#snippet readingSection()}
				{#if current}
					<div
						class="reading"
						id="rig-reading"
						role="tabpanel"
						aria-labelledby="column-{at}"
						tabindex="0"
					>
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

						<p class="label section">Declared</p>
						<ul class="seats">
							{#each current.seats as seat (seat.id)}
								<li>
									<span class="mark" data-seat={seat.state} aria-hidden="true"></span>
									<span class="seat-name">{seat.name}</span>
									<span class="seat-state">{seatMark[seat.state]}</span>
									<span class="gloss">{seat.gloss}</span>
								</li>
							{/each}
						</ul>
						<p class="prose gloss-line">
							Declarations are read from this project's kitchen document. Nothing in the daemon
							verifies one, so a claim here is a claim, not a finding.
						</p>

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
				{/if}
		{/snippet}






			<MarginSheet bind:open={openSection} {sections}>
				{#snippet caption()}
	<p class="label rule-label">
		<span>Rig</span>
		<span class="rule"></span>
		<span
			class="member"
			data-state={kitchen.phase === 'error'
				? 'failed'
				: kitchen.stale || !kitchen.data
					? 'slack'
					: kitchen.data.ready
						? 'seated'
						: 'balanced'}
		>
			{headline()}
		</span>
	</p>

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

				{/snippet}
				{#snippet hero()}
				<div class="elevation">
					<div class="floor" class:bare-floor={columns.length === 0}>
						<ol class="ladder" aria-hidden="true">
							{#each ladder as course (course.id)}
								<li><span class="label">{course.name}</span></li>
							{/each}
						</ol>

						{#if columns.length === 0}
							<p class="bare prose">
								The base line carries nothing. This project declares no harness, so there is
								no column to stand on it and nothing to measure.
							</p>
						{:else}
							<div
								bind:this={strip}
								class="columns"
								class:scrollable
								role="tablist"
								aria-label="Declared harnesses, drawn as columns"
							>
								{#each columns as column, index (column.harness)}
									{@const state = memberState(column.state)}
									<button
										type="button"
										role="tab"
										id="column-{index}"
										class="column member"
										data-state={state}
										class:rising={rising.includes(column.harness)}
										aria-selected={index === at}
										aria-controls={openSection === 'reading' ? 'rig-reading' : undefined}
										tabindex={index === at ? 0 : -1}
										aria-label={columnLabel(column)}
										onclick={() => { selected = column.harness; openSection = 'reading'; }}
										onkeydown={onKeys}
									>
										<span class="name value">{column.harness}</span>
										<span class="label state">{stateWord[column.state]}</span>
										<svg viewBox="0 0 72 {BASE}" aria-hidden="true">
											<!-- The courses observation did not clear, kept on the sheet
											     as the ghost they are: a short column is only short
											     against the height it was meant to reach. -->
											<path
												class="ghost"
												d="M36 {headY(column)} V{BASE - COURSES.length * BAND}"
												fill="none"
											/>
											{#each COURSES as course, i (course.id)}
												<path
													class="tick"
													class:cleared={column.reached > i}
													d="M28 {BASE - (i + 1) * BAND} H44"
													fill="none"
												/>
											{/each}

											<!-- The proven height. -->
											<path class="shaft" d="M36 {BASE} V{headY(column)}" fill="none" />

											{#if column.state === 'unavailable'}
												<!-- The load path is discontinuous, drawn where it stopped. -->
												<path
													class="break"
													d="M27 {headY(column) - 5} l18 -7 M27 {headY(column) - 12} l18 -7"
													fill="none"
												/>
											{:else if column.state === 'ready'}
												<!-- Seated: the load transferred and the member is capped. -->
												<path class="cap" d="M24 {headY(column)} H48" fill="none" />
											{/if}

											<!-- Declared capabilities are bolted to the head the
											     probe actually reached — under it, never up in the
											     ghost of the courses it never cleared. Filled is
											     declared supported, open is undeclared, struck is
											     declared unsupported. -->
											{#each column.seats as seat, i (seat.id)}
												{@const x = 10 + i * 11}
												{@const y = Math.min(headY(column) + 9, BASE - 10)}
												<rect
													class="seat"
													class:on={seat.state === 'supported'}
													class:off={seat.state === 'unsupported'}
													{x}
													{y}
													width="8"
													height="8"
												/>
												{#if seat.state === 'unsupported'}
													<!-- Struck through the short way. A diagonal here reads as
													     the break hatch two courses up, and one column head
													     cannot carry the same mark for "declared unsupported"
													     and "the load path is discontinuous". -->
													<path class="strike" d="M{x + 1} {y + 4} h6" fill="none" />
												{/if}
											{/each}
										</svg>
									</button>
								{/each}
							</div>
						{/if}
					</div>

					<dl class="legend">
						<div>
							<dt class="label">Course</dt>
							<dd>observed — how far the probe carried it</dd>
						</div>
						<div>
							<dt class="label">Seat</dt>
							<dd>declared — filled supported, open undeclared, struck unsupported</dd>
						</div>
					</dl>

				</div>
				{/snippet}
				{#snippet margin()}
			<dl class="readout plate">

<div>
					<dt class="label">Ready</dt>
					<dd class="value member" data-state={rig.ready > 0 ? 'seated' : 'slack'}>
						{view.configured ? rig.ready : '—'}
					</dd>
					<p class="gloss">
						{#if !view.configured}
							nothing is declared, so nothing can be ready
						{:else if rig.unprobed > 0 || rig.other > 0}
							observed to run and report a version{#if rig.unprobed > 0}, with {rig.unprobed}
								unmeasured{/if}{#if rig.other > 0}{rig.unprobed > 0 ? ' and' : ', with'}
								{rig.other} measured and settled as neither{/if}
						{:else}
							observed to run and report a version
						{/if}
					</p>
				</div>
<div>
					<dt class="label">Unavailable</dt>
					<dd class="value member" data-state={rig.unavailable > 0 ? 'failed' : 'balanced'}>
						{view.configured ? rig.unavailable : '—'}
					</dd>
					<p class="gloss">
						{view.configured
							? 'looked for and not standing — missing, or refusing to run'
							: 'nothing is declared, so nothing was looked for'}
					</p>
				</div>
<div>
					<dt class="label">Declared</dt>
					<dd class="value">{rig.declared}</dd>
					<p class="gloss">harnesses named in this project's kitchen</p>
				</div>
<div>
					<dt class="label">Measurement</dt>
					<dd class="value member" data-state={probedAt ? 'seated' : 'slack'}>
						{view.discovery.facts.length}
						<span class="of">of {rig.declared}</span>
					</dd>
					<p class="gloss">{measured(view)}</p>
				</div>
			</dl>
					<div class="instrument">
					<div class="probe">
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
							>
								{probing ? 'Measuring' : 'Measure the rig'}
							</button>
							<p class="gloss">
								A measurement resolves each declared executable and runs one bounded
								<code>--version</code> on it. It writes nothing — not this project's kitchen,
								and nothing any harness owns — and it starts a real process per harness, which
								is why it is asked for rather than polled.
							</p>
						{/if}
					</div>
						<button type="button" class="plate act" onclick={() => (openSection = 'smoke')}>Test a model</button>
					</div>
					{#if view.blockers.length > 0}
						<div class="blockers">
							<p class="label rule-label"><span>Blocking a run</span><span class="rule"></span><span class="member" data-state="failed">{view.blockers.length}</span></p>
							<ul class="daemon-words">{#each view.blockers as blocker (blocker)}<li class="member" data-state="failed">{blocker}</li>{/each}</ul>
						</div>
					{/if}
				{/snippet}
			</MarginSheet>
			<DrawerSeat open={openSection !== null} label={openSection === 'reading' ? 'Harness' : 'Index'} tag={seatTag} title={seatTitle} titleId="kitchen-seat-title" bind:width={seatWidth} onclose={() => (openSection = null)}>
				{#if openSection === 'reading'}{@render readingSection()}
				{:else if openSection === 'setup'}{@render setupSection()}
				{:else if openSection === 'catalog'}{@render catalogSection()}
				{:else if openSection === 'assignments'}{@render assignmentsSection()}
				{:else if openSection === 'fallbacks'}{@render fallbacksSection()}
				{:else if openSection === 'smoke'}{@render smokeSection()}
				{:else if openSection === 'notes'}{@render notesSection()}{/if}
			</DrawerSeat>
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

	/* --- readouts ------------------------------------------------------------ */
	.readout {
		--cut: 12px;
		display: flex;
		flex-wrap: wrap;
		gap: 1px;
		background: var(--rule);
		border: 1px solid var(--rule);
	}
	.readout > div {
		flex: 1 1 11rem;
		min-width: 0;
		background: var(--plate);
		padding: 0.75rem 1rem;
	}
	dt {
		margin-bottom: 0.25rem;
	}
	dd {
		margin: 0;
		color: var(--member-ink, var(--ink));
	}
	.of {
		color: var(--ink-2);
		font-size: 0.75rem;
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

	/* --- the elevation --------------------------------------------------------
	   One base line, one column per harness, one rhythm. The ladder annotates
	   the courses from the top down while the columns clear them from the base
	   up, which is how an elevation is drawn and read. */
	.instrument {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: start;
		gap: 0.5rem;
		margin-top: 1rem;
	}
	.instrument :global(.probe) { display: contents; }
	.instrument :global(.probe .act),
	.instrument > .act {
		grid-row: 1;
		font-size: 0.625rem;
		white-space: nowrap;
		padding: 0.25rem 0.45rem;
	}
	.instrument :global(.probe .act) { grid-column: 1; }
	.instrument > .act { grid-column: 2; }
	.instrument :global(.probe .gloss) { grid-column: 1 / -1; margin-top: 0; }
	.instrument :global(.probe .member) { grid-column: 1 / -1; }
	/* One unitless scale drives the drawing, its ladder and the column width
	   together, so the elevation is drafted at the size the viewport affords
	   instead of being rendered at mobile size on a 1440 sheet. The SVG keeps
	   its own coordinate system; only the box it is drawn into grows. */
	.floor {
		--scale: 1;
		--elev: calc(236px * var(--scale));
		--headroom: calc(28px * var(--scale));
		--band: calc(52px * var(--scale));
		--col-w: calc(72px * var(--scale));
		display: flex;
		align-items: flex-end;
		gap: 1rem;
		border-bottom: 1.25px solid var(--member-line);
	}
	@media (min-width: 60rem) {
		.floor {
			/* Sized so the documented first-run rig — and a three-harness one —
			   stands inside the strip without the overflow fade dimming a column
			   that is actually fully there. More harnesses than that really do
			   run off the edge, and the fade then says so. */
			--scale: 1.95;
		}
		/* An empty rig is not worth 500px of blank sheet: with no column to
		   draft, the ladder is an annotation, not the drawing. */
		.floor.bare-floor {
			--scale: 1;
		}
	}
	.ladder {
		display: grid;
		/* The headroom the seats occupy is padding, not a row: a grid row nothing
		   is placed in gets back-filled by auto-placement and the whole ladder
		   slips one course. */
		grid-template-rows: repeat(4, var(--band));
		list-style: none;
		margin: 0;
		padding: var(--headroom) 0 0;
		width: 6.5rem;
		flex: none;
	}
	/* Every label rides its own course line, and the first one starts below the
	   headroom the seats occupy — so the ladder's rules and the columns' ticks
	   are the same four heights, measured from the same base line. */
	.ladder li {
		grid-column: 1;
		border-top: 1px dashed var(--rule);
		padding-top: 0.25rem;
		text-align: right;
	}
	.columns {
		display: flex;
		align-items: flex-end;
		gap: 0.25rem;
		overflow-x: auto;
		overflow-y: hidden;
		flex: 1;
		min-width: 0;
	}
	/* Only when columns really run off the edge: the sheet fades out rather than
	   cutting a harness mid-word with nothing to say more rig exists. */
	.columns.scrollable {
		mask-image: linear-gradient(to right, #000 calc(100% - 2.5rem), transparent);
	}
	.bare {
		margin: 0 0 1.5rem;
		align-self: flex-end;
	}

	.column {
		display: flex;
		flex-direction: column;
		align-items: center;
		flex: none;
		font: inherit;
		background: transparent;
		border: 0;
		border-bottom: 2.5px solid transparent;
		padding: 0 0.25rem;
		cursor: pointer;
		color: var(--member-ink);
	}
	.column svg {
		display: block;
		width: var(--col-w);
		height: var(--elev);
		overflow: visible;
	}
	.column .name {
		margin-bottom: 0.15rem;
		color: var(--ink);
		max-width: 6rem;
		overflow-wrap: anywhere;
	}
	.column .state {
		margin-bottom: 0.6rem;
		color: var(--member-ink);
	}
	/* Which column is being read is location, not load: a harder edge, never red. */
	.column[aria-selected='true'] {
		border-bottom-color: var(--member-line);
	}
	.column:hover .name,
	.column[aria-selected='true'] .name {
		text-decoration: underline;
		text-underline-offset: 0.3em;
	}

	.shaft {
		stroke: var(--member-ink);
		stroke-width: 2.5;
	}
	.column[data-state='slack'] .shaft {
		stroke-width: 1.25;
	}
	.ghost {
		stroke: var(--ash);
		stroke-width: 1;
		stroke-dasharray: 3 4;
	}
	.tick {
		stroke: var(--rule-strong);
		stroke-width: 1;
	}
	.tick.cleared {
		stroke: var(--member-ink);
	}
	.cap {
		stroke: var(--member-ink);
		stroke-width: 2.5;
	}
	.break {
		stroke: var(--red);
		stroke-width: 1.25;
	}
	/* Seats are declarations, so they are drawn in the world's own declaration
	   ink and never in the member's state colour: a capability this project
	   claimed does not become a tension because the probe failed to find the
	   harness. The reading panel's marks use the same ink. */
	.seat {
		fill: none;
		stroke: var(--ash);
		stroke-width: 1;
	}
	.seat.on {
		fill: var(--seat);
		stroke: var(--seat);
	}
	.seat.off {
		stroke: var(--rule-strong);
	}
	.strike {
		stroke: var(--rule-strong);
		stroke-width: 1;
	}

	/* The authored moment, on the axis this drawing uses: a column that a probe
	   really raised settles into its new height rather than appearing at it. */
	@keyframes stand-up {
		0% {
			transform: scaleY(0.94);
		}
		62% {
			transform: scaleY(1.012);
		}
		100% {
			transform: scaleY(1);
		}
	}
	.column.rising svg {
		transform-origin: bottom center;
		animation: stand-up 0.34s cubic-bezier(0.16, 1, 0.3, 1);
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1.5rem;
		margin: 0.75rem 0 0;
		font-size: 0.75rem;
		color: var(--ink-2);
	}
	.legend > div {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}
	.legend dd {
		color: var(--ink-2);
	}

	.probe {
		margin-top: 1.5rem;
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
	.seats {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.seats li {
		display: grid;
		grid-template-columns: 0.75rem minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.25rem 0.6rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--rule);
	}
	.seats li:last-child {
		border-bottom: 0;
	}
	.seats .gloss {
		grid-column: 2 / -1;
		margin: 0;
	}
	.seat-state {
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-2);
		text-align: right;
	}
	/* The same three seat marks as the drawing, at reading size. */
	.mark {
		width: 0.5rem;
		height: 0.5rem;
		border: 1px solid var(--ash);
		align-self: center;
	}
	.mark[data-seat='supported'] {
		background: var(--ink);
		border-color: var(--ink);
	}
	.mark[data-seat='unsupported'] {
		border-color: var(--rule-strong);
		background: linear-gradient(
			to bottom,
			transparent calc(50% - 0.5px),
			var(--rule-strong) calc(50% - 0.5px),
			var(--rule-strong) calc(50% + 0.5px),
			transparent calc(50% + 0.5px)
		);
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

	@media (max-width: 48rem) {
		.ladder {
			width: 4.5rem;
		}
		.floor {
			/* Not smaller than this: below it the seat marks and the shaft come
			   within a pixel of each other in weight and the row smears. */
			--scale: 0.95;
			gap: 0.5rem;
		}
	}
</style>
