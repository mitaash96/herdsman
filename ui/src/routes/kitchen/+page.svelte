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

What this unit does NOT build, deliberately: adapter forms and smoke tests (K2),
the model catalog, assignment defaults and fallbacks (K3). Discovery here is
read-only over global configuration and writes nothing anywhere.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import AsyncField from '$lib/AsyncField.svelte';
	import { Resource } from '$lib/resource.svelte';
	import {
		daemon,
		DaemonError,
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
		hasReach,
		memberState,
		outcomeCopy,
		reachOf,
		reachValue,
		rigReading,
		savePayload,
		type AdapterEdit,
		type Column,
		type SaveFailure
	} from '$lib/kitchen';

	const kitchen = new Resource<Kitchen>((signal) => daemon.kitchen(signal));

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
		return () => kitchen.dispose();
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
	/* The fold opens itself once for an unconfigured project and is the
	   operator's from then on: `open={...}` re-applied on every render would
		collapse the fold under their cursor the moment anything on the page
		changed state — typing a field, arming a test, a save landing. */
	let setupOpen = $state(false);
	let setupSeeded = false;
	$effect(() => {
		const view = kitchen.data;
		if (!view || setupSeeded) return;
		setupSeeded = true;
		setupOpen = !view.configured;
	});
	let saveOutcome = $state<
		{ kind: 'saved'; cleared: boolean } | { kind: 'failed'; failure: SaveFailure } | null
	>(null);
	let saveEl = $state<HTMLDivElement | null>(null);

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

	async function save(): Promise<void> {
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
		saveOutcome = null;
		try {
			const resultsBefore = view.smoke.results.length;
			const fresh = await daemon.saveKitchen(savePayload(view, edits, view.revision));
			rows = rowsFrom(fresh);
			formKey = fresh.adapters.map((adapter) => adapter.name).join('\n');
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
				await kitchen.load();
				const fresh = kitchen.data;
				if (fresh) {
					rows = mergeRacedRows(held, prior, fresh);
					formKey = fresh.adapters.map((adapter) => adapter.name).join('\n');
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

	const columnLabel = (column: Column): string => {
		const course = COURSES[column.reached - 1];
		const claims = column.seats.filter((s) => s.state !== 'unknown').length;
		return `${column.harness} — ${stateWord[column.state]}, observed as far as ${course.name.toLowerCase()}, ${claims} of ${column.seats.length} capabilities declared`;
	};
</script>

<section class="kitchen">
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

	<AsyncField resource={kitchen} reading="the kitchen" onretry={() => void kitchen.load()}>
		{#snippet children(view: Kitchen)}
			<dl class="readout plate">
				<div>
					<dt class="label">Declared</dt>
					<dd class="value">{rig.declared}</dd>
					<p class="gloss">harnesses named in this project's kitchen</p>
				</div>
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
					<dt class="label">Measurement</dt>
					<dd class="value member" data-state={probedAt ? 'seated' : 'slack'}>
						{view.discovery.facts.length}
						<span class="of">of {rig.declared}</span>
					</dd>
					<p class="gloss">{measured(view)}</p>
				</div>
			</dl>

			<div class="rig-body">
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
										aria-controls="rig-reading"
										tabindex={index === at ? 0 : -1}
										aria-label={columnLabel(column)}
										onclick={() => (selected = column.harness)}
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
							<p class="prose gloss-line">
								A measurement resolves each declared executable and runs one bounded
								<code>--version</code> on it. It writes nothing — not this project's kitchen,
								and nothing any harness owns — and it starts a real process per harness, which
								is why it is asked for rather than polled.
							</p>
						{/if}
					</div>
				</div>

				{#if current}
					<div
						class="reading plate"
						id="rig-reading"
						role="tabpanel"
						aria-labelledby="column-{at}"
						tabindex="0"
					>
						<h2 class="name-head">
							<span class="value">{current.harness}</span>
							<span class="member state-chip" data-state={memberState(current.state)}>
								{stateWord[current.state]}
							</span>
						</h2>

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
			</div>

			{#if view.blockers.length > 0}
				<section class="blockers" aria-labelledby="blockers-head">
					<p class="label rule-label">
						<span id="blockers-head">Blocking a run</span>
						<span class="rule"></span>
						<span class="member" data-state="failed">{view.blockers.length}</span>
					</p>
					<ul class="daemon-words">
						{#each view.blockers as blocker (blocker)}
							<li class="member" data-state="failed">{blocker}</li>
						{/each}
					</ul>
				</section>
			{/if}

			<section class="setup" aria-labelledby="setup-head">
				<p class="label rule-label">
					<span id="setup-head">Setting up</span>
					<span class="rule"></span>
				</p>
				<details class="setup-fold" bind:open={setupOpen}>
					<summary
						>{view.configured
							? 'Edit declarations in .herdsman/kitchen.json'
							: 'Declare a harness in .herdsman/kitchen.json'}</summary
					>
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
						{#if dirty}
							<p class="prose gloss-line" id="save-consequence">
								Saving replaces this project's .herdsman/kitchen.json declaration. It also
								clears every model test result, because those describe the configuration being
								replaced. No harness setting outside this project is touched.
							</p>
						{/if}
						<div class="acts">
							<button
								type="button"
								class="plate act"
								disabled={!dirty || saving}
								onclick={() => void save()}
								aria-describedby={dirty ? 'save-consequence' : undefined}>{saving ? 'Saving' : 'Save'}</button
							>
						</div>
						{#if saveOutcome !== null}
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
					</div>
				</details>
			</section>

			<section class="smoke" aria-labelledby="smoke-head">
				<p class="label rule-label">
					<span id="smoke-head">Testing a model</span>
					<span class="rule"></span>
				</p>
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

			{#if view.notes.length > 0}
				<section class="notes" aria-labelledby="notes-head">
					<p class="label rule-label">
						<span id="notes-head">Provenance</span>
						<span class="rule"></span>
					</p>
					<ul class="daemon-words">
						{#each view.notes as note (note)}
							<li class="member" data-state="slack">{note}</li>
						{/each}
					</ul>
				</section>
			{/if}
		{/snippet}
	</AsyncField>
</section>

<style>
	.kitchen {
		max-width: 74rem;
	}

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
	.rig-body {
		display: grid;
		grid-template-columns: minmax(0, 1.6fr) minmax(22rem, 1fr);
		gap: 1.5rem 2rem;
		align-items: start;
		margin-top: 2rem;
	}
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

	/* --- the reading beside the drawing -------------------------------------- */
	.reading {
		--cut: 12px;
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 1rem 1.25rem 1.25rem;
	}
	.name-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.75rem;
		margin: 0 0 1rem;
		font: inherit;
	}
	.name-head .value {
		font-size: 1.0625rem;
	}
	.state-chip {
		font-size: 0.625rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--member-ink);
	}
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
	.blockers,
	.setup,
	.smoke,
	.notes {
		margin-top: 2.5rem;
	}
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
	.setup-fold summary {
		margin: 0 0 0.35rem;
		color: var(--ink);
		cursor: pointer;
	}
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
	/* Floored at min-content: the widest declaration a select can hold is the
	   one the operator is about to save — "declared unsupported" must never
	   clip to "declared unsuppo…". */
	.cap-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min-content, 1fr));
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

	@media (max-width: 60rem) {
		.rig-body {
			grid-template-columns: minmax(0, 1fr);
		}
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
