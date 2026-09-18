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
	CapabilityState,
	HarnessFacts,
	Kitchen,
	KitchenAdapter,
	KitchenReadiness,
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

/**
 * A minimal, valid Kitchen document, for a project that has none.
 *
 * Reference text, not a form: K1 discovers and reports, and K2 owns writing
 * declarations. It is the documented first-run shape — one harness, two model
 * assignments — and it is minimal in the strict sense: every key here is one
 * `Kitchen` validation would refuse to do without. `defaults` must name pairs
 * that exist in `models`, which is why two models and not zero.
 */
export const EXAMPLE_DECLARATION = `{
  "version": 1,
  "adapters": [
    {
      "name": "claude",
      "argv": ["claude", "-p", "{prompt}"],
      "model_argv": ["--model"],
      "capabilities": { "pty": "unsupported", "memory": "A" }
    }
  ],
  "models": [
    { "harness": "claude", "model": "opus" },
    { "harness": "claude", "model": "haiku" }
  ],
  "defaults": {
    "planner": { "harness": "claude", "model": "opus" },
    "initiative": { "harness": "claude", "model": "haiku" }
  }
}`;
