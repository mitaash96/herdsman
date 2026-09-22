/**
 * The rig, read as an elevation: one column per declared harness, standing on
 * the project's base line, rising exactly as far as observation carried it.
 *
 * The whole module exists to keep one distinction geometric rather than
 * editorial. A column's *height* is observed — how far one bounded `--version`
 * probe actually got. Its *seats* are declared — what `.herdsman/kitchen.json`
 * claims the harness can do, which nothing in the daemon checks. The two are
 * never mixed into a single score, because a project can declare a capability
 * for a harness that is not even installed, and a score would average that into
 * something reassuring.
 *
 * Pure functions only; `ui/dev/field-check.ts` asserts the claims below.
 */
import type {
	AssetSummary,
	CapabilityState,
	FailureKind,
	HarnessFacts,
	Kitchen,
	KitchenAdapter,
	KitchenCapabilities,
	KitchenDefaults,
	KitchenFallback,
	KitchenModel,
	KitchenPrice,
	KitchenReadiness,
	KitchenSaveAdapter,
	KitchenSaveBody,
	KitchenSmoke,
	KitchenSmokeResult,
	MemoryClass,
	ReadinessState
} from './daemon';

/**
 * The four courses of the elevation, in the order observation clears them.
 *
 * Each one is a thing the daemon *saw*, never a thing the project claimed.
 * They stop where the probe stops: `herdsman/discovery.py` resolves an
 * executable, runs `--version` once under a timeout, and reads the first line
 * of stdout. There is no fifth course, because nothing else is observed.
 */
export const COURSES = [
	{ id: 'declared', name: 'Declared', proves: 'this project names the harness' },
	{ id: 'found', name: 'Found', proves: 'an executable resolved on this machine' },
	{ id: 'answered', name: 'Answered', proves: '--version ran and exited 0' },
	{ id: 'versioned', name: 'Versioned', proves: 'it printed a version line' }
] as const;

export type CourseId = (typeof COURSES)[number]['id'];

/** What one probe saw. Absent entirely when no probe has touched the adapter. */
export interface Observed {
	executable: string | null;
	version: string | null;
	health: HarnessFacts['health'];
	/** The probe's own sentence about why it stopped. Quoted, never rewritten. */
	detail: string;
}

/** One declared capability, bolted to the column's head as a seat. */
export interface Seat {
	id: 'output' | 'resume' | 'usage' | 'pty' | 'memory';
	name: string;
	state: CapabilityState;
	/** What the declaration means for this product, in one clause. */
	gloss: string;
}

/** One harness drawn as a column: observed height, declared seats, one action. */
export interface Column {
	harness: string;
	/** Courses proven by observation, 1 to 4. A declaration alone is 1. */
	reached: number;
	/** The daemon's own verdict, not this build's. */
	state: ReadinessState;
	/** Why the verdict is what it is, in the daemon's words. Empty when ready. */
	reason: string;
	/** The single corrective action the daemon names. Empty when there is none. */
	action: string;
	/** `null` when no probe has touched this adapter in this daemon's lifetime. */
	observed: Observed | null;
	seats: Seat[];
}

const MEMORY_CLASS: Record<MemoryClass, string> = {
	A: 'class A — pointer and pull',
	B: 'class B — A, plus project-local sugar',
	C: 'class C — budgeted inline'
};

/**
 * The declared seats, in one fixed order so two columns compare by position.
 *
 * `unknown` is undeclared and is rendered as undeclared. Reading it as a no is
 * the exact inference the substrate refuses to make (`herdsman/kitchen.py`
 * keeps three capability states "never two"), and a UI that quietly collapses
 * it into a cross teaches the operator a fact nobody established.
 */
export function seatsOf(adapter: KitchenAdapter): Seat[] {
	const c = adapter.capabilities;
	return [
		{
			id: 'output',
			name: 'Structured output',
			state: c.structured_output,
			gloss: 'machine-readable results rather than scraped terminal text'
		},
		{
			id: 'resume',
			name: 'Resume',
			state: c.resume,
			gloss: 'continues a prior session instead of restarting cold'
		},
		{
			id: 'usage',
			name: 'Usage',
			state: c.usage,
			gloss: 'reports provider token usage Herdsman can attribute'
		},
		{
			id: 'pty',
			name: 'Needs a PTY',
			state: c.pty,
			gloss: 'herdr owns supplying one; supported here means it is required'
		},
		{
			id: 'memory',
			name: 'Memory',
			state: c.memory === null ? 'unknown' : 'supported',
			gloss: c.memory === null ? 'no class declared' : MEMORY_CLASS[c.memory]
		}
	];
}

/**
 * How far observation carried this harness.
 *
 * An absent fact is 1, not 0: the declaration is itself true and on disk. The
 * difference between "declared, never looked at" and "declared, looked at,
 * nothing there" is carried by `Column.observed` being null, not by the height.
 */
export function courseReached(fact: HarnessFacts | undefined): number {
	if (!fact || fact.executable === null) return 1;
	if (fact.health !== 'healthy') return 2;
	return fact.version === null ? 3 : 4;
}

/**
 * Every declared harness as a column, in declaration order, plus anything the
 * daemon reported readiness for that the project never declared.
 *
 * The second half is not hypothetical bookkeeping: `Kitchen.readiness` emits an
 * `unconfigured` row for a fact whose harness is not in `adapters`. Today's
 * discovery lane cannot produce one — it only probes what is declared — so the
 * row is carried rather than drawn from, and nothing here invents a seat for a
 * harness that has no declaration to read seats from.
 */
export function columnsOf(kitchen: Kitchen): Column[] {
	const facts = new Map(kitchen.discovery.facts.map((fact) => [fact.harness, fact]));
	const verdicts = new Map(kitchen.readiness.map((item) => [item.harness, item]));
	const declared = kitchen.adapters.map((adapter) => {
		const fact = facts.get(adapter.name);
		const verdict = verdicts.get(adapter.name);
		return column(adapter.name, fact, verdict, seatsOf(adapter));
	});
	const known = new Set(kitchen.adapters.map((adapter) => adapter.name));
	const undeclared = kitchen.readiness
		.filter((item) => !known.has(item.harness))
		.map((item) => column(item.harness, facts.get(item.harness), item, []));
	return [...declared, ...undeclared];
}

function column(
	harness: string,
	fact: HarnessFacts | undefined,
	verdict: KitchenReadiness | undefined,
	seats: Seat[]
): Column {
	return {
		harness,
		reached: courseReached(fact),
		state: verdict?.state ?? 'unknown',
		reason: verdict?.reason ?? 'the daemon reported no readiness for this adapter',
		action: verdict?.action ?? '',
		observed: fact
			? {
					executable: fact.executable,
					version: fact.version,
					health: fact.health,
					detail: fact.detail
				}
			: null,
		seats
	};
}

/** The base line's own reading: what stands on it, and how much of it is known. */
export interface Rig {
	declared: number;
	ready: number;
	unavailable: number;
	/** Declared harnesses no probe has touched in this daemon's lifetime. */
	unprobed: number;
	/**
	 * Measured, and settled as neither: `degraded` and `unconfigured`.
	 *
	 * The readout prints this rather than letting it vanish. Ready plus
	 * unavailable plus unprobed is not the whole rig, and a reading whose parts
	 * quietly fail to sum to what was declared is the kind of arithmetic an
	 * operator trusts once and is wrong about afterwards.
	 */
	other: number;
}

export function rigReading(columns: Column[]): Rig {
	const ready = columns.filter((c) => c.state === 'ready').length;
	const unavailable = columns.filter((c) => c.state === 'unavailable').length;
	const unprobed = columns.filter((c) => c.observed === null).length;
	return {
		declared: columns.length,
		ready,
		unavailable,
		unprobed,
		other: columns.length - ready - unavailable - unprobed
	};
}

/**
 * The member state this world already has, for one column.
 *
 * `loaded` is never returned. The Load-Only Red Rule reserves red for a member
 * actually under load, and nothing in Kitchen is under load — so red appears
 * here only as `failed`, the broken path, which is what an unavailable harness
 * is. A degraded or unprobed column is slack, not alarming.
 */
export function memberState(state: ReadinessState): 'seated' | 'balanced' | 'slack' | 'failed' {
	if (state === 'ready') return 'seated';
	if (state === 'unavailable') return 'failed';
	if (state === 'unconfigured') return 'balanced';
	return 'slack';
}

/* --- K2: the write path and the smoke reading ------------------------------
 *
 * The setup form writes the whole canonical document under a revision
 * precondition, so the payload must carry declarations this unit never renders
 * (models, tiers, defaults, fallbacks): a save replaces everything except launch
 * templates, and dropping a field because the form has no editor for it would
 * destroy it silently. Launch templates are the opposite — never received from
 * the daemon, so an untouched replacement field is *omitted*, and an omission
 * is the daemon's keep-the-stored-one rule. Nothing here sends `""`.
 */

/** One adapter as the setup form holds it: effective capabilities plus any
 * replacement template the operator actually typed (`null` = untouched). */
export interface AdapterEdit {
	name: string;
	capabilities: KitchenCapabilities;
	argv: string[] | null;
	model_argv: string[] | null;
}

/** The identity of a model anywhere on this surface: the pair, never the label. */
export function pairKey(assignment: { harness: string; model: string }): string {
	return `${assignment.harness}/${assignment.model}`;
}

/** Every pair the catalog currently offers, in catalog order. */
export function pairsOf(models: { harness: string; model: string }[]): string[] {
	return models.map((model) => pairKey(model));
}

/** One payload builder, one save, one dirty model spanning every editor (K5):
 * adapters keep their K2 rule, and K3's three editors supply their own slices.
 *
 * Two invariants live here and nowhere else:
 * - `tier` is stripped to `null` on every model entry. The served entry carries
 *   the RESOLVED tier, and the daemon refuses a declared tier on an entry —
 *   "declare it under tiers instead" — so a verbatim round-trip 400s the moment
 *   any tier is mapped. The map rides separately and the daemon re-resolves.
 * - no projection field is ever sent (`extra="forbid"` on the route). */
export function savePayload(
	view: Kitchen,
	edits: AdapterEdit[],
	k3: KitchenEdits,
	expectRevision: string
): KitchenSaveBody {
	const stored = new Map(view.adapters.map((adapter) => [adapter.name, adapter]));
	return {
		version: view.version,
		adapters: edits.map((edit) => {
			const entry: KitchenSaveAdapter = { name: edit.name, capabilities: edit.capabilities };
			const prior = stored.get(edit.name);
			if (prior !== undefined) entry.source = prior.source;
			if (edit.argv !== null) entry.argv = edit.argv;
			if (edit.model_argv !== null) entry.model_argv = edit.model_argv;
			return entry;
		}),
		models: modelsPayload(view, k3.models),
		tiers: tiersPayload(view, k3.models),
		frontier_tiers: view.frontier_tiers,
		defaults: defaultsPayload(k3),
		fallbacks: fallbacksPayload(k3),
		context_warning_tokens: view.context_warning_tokens,
		expect_revision: expectRevision
	};
}

/* --- K3: the catalog, the ladder and the chains -------------------------- *
 *
 * The catalog is declaration-fed and stays that way: discovery never adds a
 * model (`herdsman/discovery.py`), price/capability absent stays unknown, and
 * tier is read from — written to — the project-local map, never onto an entry.
 * The role vocabulary comes from the Library's enumeration; a declared key it
 * has never heard of still renders as the current value but cannot be typed
 * back into existence. Fallback chains and role defaults are declarations no
 * runtime consumes yet; the surface says so rather than implying a live rule.
 *
 * Every editor keys its rows by the document's own identity (pair for models
 * and tiers, role key for role defaults, primary pair for chains) so a race
 * can be merged the way `mergeRacedRows` merges adapters: the operator's
 * entries survive, the racing writer's rows arrive, untouched rows follow the
 * document. Policy — escalation, cycles, pairs outside the catalog — is the
 * daemon's refusal on save and is never mirrored here. */

/** One catalog row: the entry as served, plus the tier control's own state.
 * The control writes the tiers MAP keyed by the pair; `tier` on the entry is
 * resolved display and is never a form field. */
export interface ModelRow {
	harness: string;
	model: string;
	source: string;
	usage: CapabilityState;
	counting: CapabilityState;
	price: KitchenPrice | null;
	/** What the map holds for THIS pair ('' = no pair-keyed mapping yet). */
	tierValue: string;
	tierTouched: boolean;
	stored: boolean;
}

/** One role default. `assignment: null` is a picked-but-unconfigured row,
 * which never enters the payload — a role is chosen from the Library's list,
 * then given a pair. */
export interface RoleRow {
	role: string;
	assignment: { harness: string; model: string } | null;
	stored: boolean;
}

/** One fallback chain, candidates in their declared order. */
export interface ChainRow {
	primary: { harness: string; model: string };
	candidates: { harness: string; model: string }[];
	stored: boolean;
}

/** The whole K3 edit state: one dirty model, one payload, one race merge. */
export interface KitchenEdits {
	models: ModelRow[];
	planner: { harness: string; model: string } | null;
	initiative: { harness: string; model: string } | null;
	roles: RoleRow[];
	chains: ChainRow[];
}

export function modelRowsFrom(view: Kitchen): ModelRow[] {
	return view.models.map((entry) => ({
		harness: entry.harness,
		model: entry.model,
		source: entry.source,
		usage: entry.usage,
		counting: entry.counting,
		price: entry.price,
		tierValue: view.tiers[pairKey(entry)] ?? '',
		tierTouched: false,
		stored: true
	}));
}

export function editsFrom(view: Kitchen): KitchenEdits {
	return {
		models: modelRowsFrom(view),
		planner: view.defaults.planner,
		initiative: view.defaults.initiative,
		roles: Object.entries(view.defaults.roles).map(([role, assignment]) => ({
			role,
			assignment,
			stored: true
		})),
		chains: view.fallbacks.map((chain) => ({
			primary: chain.primary,
			candidates: [...chain.candidates],
			stored: true
		}))
	};
}

/** Role names the Library actually enumerates, in the daemon's order. */
export function roleNamesFrom(assets: AssetSummary[]): string[] {
	return assets.map((asset) => asset.name);
}

/** Every tier name the document already uses: the map's values plus the
 * frontier names — the option set a tier control offers before typing. */
export function tierNames(view: Kitchen): string[] {
	const names = new Set<string>(Object.values(view.tiers));
	for (const name of view.frontier_tiers) names.add(name);
	return [...names];
}

function modelsPayload(view: Kitchen, rows: ModelRow[]): KitchenModel[] {
	const prior = new Map(view.models.map((entry) => [pairKey(entry), entry]));
	return rows.map((row) => {
		const entry = prior.get(pairKey(row));
		if (entry === undefined) {
			return {
				harness: row.harness,
				model: row.model,
				source: 'declared' as const,
				tier: null,
				usage: 'unknown' as const,
				counting: 'unknown' as const,
				price: null
			};
		}
		/* Spread the served entry but null its resolved tier — the map below is
		   the only place a tier may live. */
		return { ...entry, tier: null };
	});
}

function tiersPayload(view: Kitchen, rows: ModelRow[]): Record<string, string> {
	const alive = new Set(rows.map((row) => pairKey(row)));
	const tiers: Record<string, string> = {};
	for (const [key, value] of Object.entries(view.tiers)) {
		/* Pair-keyed mappings follow their model out; bare model keys are the
		   hand-written form and are never touched by this editor. */
		if (key.includes('/') && !alive.has(key)) continue;
		tiers[key] = value;
	}
	for (const row of rows) {
		if (!row.tierTouched) continue;
		const key = pairKey(row);
		if (row.tierValue === '') delete tiers[key];
		else tiers[key] = row.tierValue;
	}
	return tiers;
}

function defaultsPayload(edits: KitchenEdits): KitchenDefaults {
	const roles: Record<string, { harness: string; model: string }> = {};
	for (const row of edits.roles) {
		if (row.assignment !== null) roles[row.role] = row.assignment;
	}
	return { planner: edits.planner, initiative: edits.initiative, roles };
}

function fallbacksPayload(edits: KitchenEdits): KitchenFallback[] {
	return edits.chains.map((chain) => ({ primary: chain.primary, candidates: [...chain.candidates] }));
}

/** JSON with object keys sorted, so two documents built in different orders
 * compare by value. Key order is not meaning anywhere in this document. */
function stable(value: unknown): string {
	if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
	if (value !== null && typeof value === 'object') {
		const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
			a < b ? -1 : a > b ? 1 : 0
		);
		return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`).join(',')}}`;
	}
	return JSON.stringify(value ?? null);
}

/** True when any K3 editor's payload slice differs from the document as read —
 * a touch that sets a value back where it was is not dirty, and adapter dirt
 * is the caller's (K2's own test). */
export function editsDirty(view: Kitchen, edits: KitchenEdits): boolean {
	const modelsSame =
		stable(modelsPayload(view, edits.models)) ===
		stable(view.models.map((entry) => ({ ...entry, tier: null })));
	const tiersSame = stable(tiersPayload(view, edits.models)) === stable(view.tiers);
	const defaultsSame = stable(defaultsPayload(edits)) === stable(view.defaults);
	const chainsSame = stable(fallbacksPayload(edits)) === stable(view.fallbacks);
	return !(modelsSame && tiersSame && defaultsSame && chainsSame);
}

/** The K5 race merge, over the three editors: capture the held rows before the
 * reload, merge them onto the fresh document, pre-seed the rebuild key.
 * Operator entries and picked rows are carried; racing additions arrive;
 * untouched rows follow the document — including a racing writer's deletion,
 * exactly as adapter rows behave. Scalars win only when they really moved. */
export function mergeRacedEdits(held: KitchenEdits, prior: Kitchen, fresh: Kitchen): KitchenEdits {
	const freshEdits = editsFrom(fresh);
	const priorEdits = editsFrom(prior);
	const freshModels = new Set(freshEdits.models.map((row) => pairKey(row)));
	const priorModels = new Map(prior.models.map((entry) => [pairKey(entry), entry]));
	const modelChanged = (row: ModelRow): boolean => {
		const before = priorModels.get(pairKey(row));
		return (
			before === undefined ||
			row.tierTouched ||
			row.source !== before.source ||
			row.usage !== before.usage ||
			row.counting !== before.counting ||
			JSON.stringify(row.price) !== JSON.stringify(before.price)
		);
	};
	const models = freshEdits.models.map((row) => {
		const old = held.models.find((item) => pairKey(item) === pairKey(row));
		if (old !== undefined && modelChanged(old)) return { ...row, ...old };
		return row;
	});
	const carriedModels = held.models.filter(
		(old) => !freshModels.has(pairKey(old)) && (!old.stored || modelChanged(old))
	);
	const take = <T extends { stored: boolean }>(
		heldRows: T[],
		freshRows: T[],
		priorRows: T[],
		key: (row: T) => string,
		changed: (row: T, before: T | undefined) => boolean
	): T[] => {
		const freshByKey = new Map(freshRows.map((row) => [key(row), row]));
		const priorByKey = new Map(priorRows.map((row) => [key(row), row]));
		const merged = freshRows.map((row) => {
			const old = heldRows.find((item) => key(item) === key(row));
			if (old !== undefined && changed(old, priorByKey.get(key(row)))) return { ...row, ...old };
			return row;
		});
		const carried = heldRows.filter(
			(old) => !freshByKey.has(key(old)) && (!old.stored || changed(old, priorByKey.get(key(old))))
		);
		return [...merged, ...carried];
	};
	const moved = <T>(held: T | null, before: T | null, freshValue: T | null): T | null =>
		held !== before ? held : freshValue;
	return {
		models: [...models, ...carriedModels],
		planner: moved(held.planner, prior.defaults.planner, fresh.defaults.planner),
		initiative: moved(held.initiative, prior.defaults.initiative, fresh.defaults.initiative),
		roles: take(
			held.roles,
			freshEdits.roles,
			priorEdits.roles,
			(row) => row.role,
			(row, before) =>
				before === undefined || stable(row.assignment) !== stable(before.assignment)
		),
		chains: take(
			held.chains,
			freshEdits.chains,
			priorEdits.chains,
			(row) => pairKey(row.primary),
			(row, before) =>
				before === undefined || stable(row.candidates) !== stable(before.candidates)
		)
	};
}

/** What a refused save means, classified from the daemon's own answer.
 * `race` is the revision precondition firing — the document moved under the
 * form; `absent` is a daemon that predates the save route entirely. */
export interface SaveFailure {
	kind: 'race' | 'invalid' | 'unreachable' | 'absent' | 'unknown';
	detail: string;
}

export function classifySaveFailure(error: {
	kind: FailureKind;
	status: number | null;
	message: string;
}): SaveFailure {
	if (error.kind === 'unreachable' || error.kind === 'aborted')
		return { kind: 'unreachable', detail: error.message };
	if (error.status === 409) return { kind: 'race', detail: error.message };
	if (error.status === 400) return { kind: 'invalid', detail: error.message };
	if (error.kind === 'not_found') return { kind: 'absent', detail: error.message };
	return { kind: 'unknown', detail: error.message };
}

/** The smoke request this build sends: fixed, matching the daemon's default
 * and the figure every consequence sentence interpolates. */
export const SMOKE_TIMEOUT = 30;

/** The daemon's own absence sentence, carried and never re-authored. */
export function absenceOf(smoke: KitchenSmoke): string | null {
	return smoke.absence;
}

/** The most recent result for one harness, across that harness's tested pairs.
 * `results` holds each pair's latest, so this is a pick, never an aggregate. */
export function reachOf(smoke: KitchenSmoke, harness: string): KitchenSmokeResult | null {
	let newest: KitchenSmokeResult | null = null;
	for (const result of smoke.results) {
		if (result.harness !== harness) continue;
		if (newest === null || Date.parse(result.at) >= Date.parse(newest.at)) newest = result;
	}
	return newest;
}

/** The Reach row exists exactly while the daemon holds any result; a harness
 * never tested reads `not tested` rather than borrowing another's finding. */
export function hasReach(smoke: KitchenSmoke): boolean {
	return smoke.results.length > 0;
}

export function reachValue(result: KitchenSmokeResult | null): string {
	if (result === null) return 'not tested';
	const model = result.model;
	switch (result.state) {
		case 'passed':
			return `answered — ${model}, ${formatDuration(result.duration)}s`;
		case 'failed':
			return `no answer — ${model}`;
		case 'refused':
			return `refused — ${model}`;
		case 'timed_out':
			return `timed out — ${model}`;
	}
}

/** The approved outcome sentence per state, with the daemon's own detail
 * quoted on screen beneath it — the detail is never folded into this copy. */
export function outcomeCopy(result: KitchenSmokeResult, timeout: number): string {
	switch (result.state) {
		case 'passed':
			return `Answered in ${formatDuration(result.duration)}s.`;
		case 'failed':
			return `${result.harness} did not answer this prompt.`;
		case 'refused':
			return `${result.harness} refused the prompt.`;
		case 'timed_out':
			return `No answer within ${timeout}s. The harness may still be working; nothing here retries for you.`;
	}
}

/** Milliseconds rounded as the daemon rounds them, with no trailing-zero theatre. */
function formatDuration(seconds: number): number {
	return Number(seconds.toFixed(3));
}
