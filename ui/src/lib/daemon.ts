/**
 * The daemon is the only source of state and the only write path.
 *
 * Every type here mirrors a model the daemon already serialises
 * (`herdsman/graph.py`, `herdsman/classes.py`, and for navigation
 * `herdsman/nav.py` via `NavIndex.to_dict()`); nothing is invented and nothing
 * is widened. `PlanGraph` is documented in `graph.py` as "the stable projection
 * the UI and CLI render a running plan from", and it is what this app renders.
 *
 * The UI and the CLI are peers over this API, not layers: `herdsman run` is
 * itself an HTTP client to the same routes (`herdsman/cli.py`).
 */

/** `herdsman/classes.py` — Initiative.state. */
export type InitiativeState = 'pending' | 'running' | 'settled' | 'failed' | 'cancelled';

/** `herdsman/graph.py` — NodeStatus. */
export interface NodeStatus {
	initiative_id: string;
	name: string;
	digest: string;
	/** Widened to `str` by the daemon; `InitiativeState` is the observed domain. */
	state: string;
	depends_on: string[];
	harness: string;
	model: string;
	attempts: number;
	checkpoint_id: string | null;
	/** Computed, never stored — readiness is derived from the folded plan. */
	ready: boolean;
}

/** `herdsman/graph.py` — Overhead. The crude Sprint 2 ratio, not Sprint 4's ledger. */
export interface Overhead {
	orchestration_tokens: number;
	productive_tokens: number;
	/** `null` when there is no denominator yet. Null is unknown, never zero. */
	ratio: number | null;
	target: number;
	/** `null` when `ratio` is unknown. */
	within_target: boolean | null;
}

/** `herdsman/graph.py` — PlanGraph. */
export interface PlanGraph {
	plan_id: string;
	version: number;
	approval: string;
	nodes: NodeStatus[];
	edges: [string, string][];
	ready: string[];
	critical_path: string[];
	max_concurrency: number;
	overhead: Overhead;
}

/** `herdsman/graph.py` — ContentionKind. */
export type ContentionKind = 'write_write' | 'write_read';

/** `herdsman/graph.py` — Contention. */
export interface Contention {
	/** Sorted, so the pair has one stable identity regardless of direction. */
	initiatives: [string, string];
	paths: string[];
	kind: ContentionKind;
	writer: string | null;
	reader: string | null;
}

/** `herdsman/graph.py` — NodeRisk. */
export interface NodeRisk {
	initiative_id: string;
	digest: string;
	blast_radius: number;
	articulation: boolean;
	on_critical_path: boolean;
}

/** `herdsman/graph.py` — RiskReport. */
export interface RiskReport {
	plan_id: string;
	version: number;
	critical_path: string[];
	max_concurrency: number;
	nodes: NodeRisk[];
	conflicts: Contention[];
	suggested_edges: Contention[];
	warnings: string[];
}

/** `herdsman/nav.py` — one indexed file (`NavIndex.files`). */
export interface NavFile {
	path: string;
	loc: number;
}

/** `herdsman/nav.py` — one indexed symbol (`NavIndex.symbols`). */
export interface NavSymbol {
	name: string;
	kind: 'class' | 'function' | 'method';
	module: string;
	file: string;
	line: number;
	end_line: number;
	signature: string;
	bases: string[];
	returns: string;
	exported: boolean;
	doc: string;
}

/** `herdsman/nav.py` — one resolved edge (`NavIndex.edges`). */
export interface NavEdge {
	kind: 'contains' | 'imports' | 'calls' | 'instantiates' | 'references';
	src: string;
	dst: string;
	file: string;
	line: number;
	resolution: 'static' | 'dynamic' | 'external';
}

/** `herdsman/nav.py` — one unresolvable call (`NavIndex.unresolved`). */
export interface NavUnresolved {
	kind: string;
	src: string;
	name: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — PEP 621 `[project.scripts]` entry. */
export interface NavConsoleScript {
	name: string;
	target: string;
	file: string;
	line: number | null;
}

/** `herdsman/nav.py` — one Typer command (`entry_points.cli`). */
export interface NavCliCommand {
	command: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — one `add_api_route` call (`entry_points.routes`). */
export interface NavRoute {
	method: string;
	path: string;
	handler: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — one `test*` function in a pytest test file (`entry_points.tests`). */
export interface NavTestEntry {
	node: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — `_EntryPoints.to_dict()`. */
export interface NavEntryPoints {
	console_script: NavConsoleScript | null;
	cli: NavCliCommand[];
	routes: NavRoute[];
	tests: NavTestEntry[];
}

/** `herdsman/nav.py` — one codegraph-only cross-check row (`coverage.deep_conflicts`). */
export interface NavDeepConflict {
	symbol: string;
	direction: string;
	edge: string;
	note: string;
}

/** `herdsman/nav.py` — `NavIndex.coverage`. */
export interface NavCoverage {
	languages: string[];
	excluded: string[];
	deep: boolean;
	deep_note?: string;
	deep_conflicts?: NavDeepConflict[];
}

/** `herdsman/nav.py` — `NavIndex.to_dict()`, the `GET /nav/codemap` body. */
export interface NavIndex {
	repo_ref: string | null;
	fingerprint: string;
	coverage: NavCoverage;
	files: NavFile[];
	symbols: NavSymbol[];
	edges: NavEdge[];
	entry_points: NavEntryPoints;
	unresolved: NavUnresolved[];
}

/** The text envelope for `GET /nav/tour`, `/nav/flow/{name}`, and `/nav/symbol/{name}`. */
export interface NavText {
	text: string;
}

/**
 * Why a request did not produce data. The distinction matters on screen: a
 * daemon that is not running is a different sentence from a plan that does not
 * exist, and both are different from a plan the daemon refused to act on.
 */
export type FailureKind = 'unreachable' | 'not_found' | 'conflict' | 'bad_response' | 'aborted';

export class DaemonError extends Error {
	readonly kind: FailureKind;
	readonly status: number | null;

	constructor(kind: FailureKind, message: string, status: number | null = null) {
		super(message);
		this.name = 'DaemonError';
		this.kind = kind;
		this.status = status;
	}
}

/** Same origin in production (the daemon serves the built folder); proxied in dev. */
const BASE = '';

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${BASE}${path}`, { signal, headers: { accept: 'application/json' } });
	} catch (cause) {
		if (signal?.aborted) throw new DaemonError('aborted', 'request cancelled');
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', null);
	}
	if (response.status === 404) {
		throw new DaemonError('not_found', await detail(response, 'Not found.'), 404);
	}
	// A dead daemon behind the dev proxy surfaces as a gateway error, not a
	// network failure, so the operator must still be told the daemon is down and
	// how to start it -- not handed the proxy's status code.
	if (response.status === 502 || response.status === 503 || response.status === 504) {
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', response.status);
	}
	if (!response.ok) {
		const kind: FailureKind = response.status === 409 ? 'conflict' : 'bad_response';
		throw new DaemonError(kind, await detail(response, `Daemon returned ${response.status}.`), response.status);
	}
	try {
		return (await response.json()) as T;
	} catch {
		throw new DaemonError('bad_response', 'The daemon returned a body this build cannot read.', response.status);
	}
}

/** FastAPI puts its message in `detail`; fall back rather than showing `[object Object]`. */
async function detail(response: Response, fallback: string): Promise<string> {
	try {
		const body: unknown = await response.json();
		if (body && typeof body === 'object' && 'detail' in body) {
			const value = (body as { detail: unknown }).detail;
			if (typeof value === 'string' && value.length > 0) return value;
		}
	} catch {
		/* fall through */
	}
	return fallback;
}

export const daemon = {
	graph: (planId: string, signal?: AbortSignal): Promise<PlanGraph> =>
		get<PlanGraph>(`/plans/${encodeURIComponent(planId)}/graph`, signal),

	risk: (planId: string, signal?: AbortSignal): Promise<RiskReport> =>
		get<RiskReport>(`/plans/${encodeURIComponent(planId)}/risk`, signal),

	/** `GET /nav/codemap` — the full `NavIndex.to_dict()` JSON. */
	codemap: (signal?: AbortSignal): Promise<NavIndex> =>
		get<NavIndex>('/nav/codemap', signal),

	/** `GET /nav/tour` — the guided-tour projection as text. */
	tour: (signal?: AbortSignal): Promise<NavText> =>
		get<NavText>('/nav/tour', signal),

	/**
	 * `GET /nav/flow/{name}` — one curated-flow projection as text.
	 * An unknown flow 404s, which surfaces as `not_found`.
	 */
	flow: (name: string, signal?: AbortSignal): Promise<NavText> =>
		get<NavText>(`/nav/flow/${encodeURIComponent(name)}`, signal),

	/**
	 * `GET /nav/symbol/{name}` — one symbol projection as text.
	 * An unknown symbol 404s, which surfaces as `not_found`.
	 */
	symbol: (name: string, signal?: AbortSignal): Promise<NavText> =>
		get<NavText>(`/nav/symbol/${encodeURIComponent(name)}`, signal),

	/**
	 * Live domain events for one plan (`GET /plans/{id}/events`, SSE).
	 *
	 * `onEvent` receives the parsed payload; `onStatus` reports whether the
	 * stream is currently connected, so a view can mark its data stale rather
	 * than silently showing a frozen projection as if it were live.
	 */
	events(
		planId: string,
		onEvent: (type: string, data: unknown) => void,
		onStatus: (connected: boolean) => void
	): () => void {
		const source = new EventSource(`${BASE}/plans/${encodeURIComponent(planId)}/events`);
		// The daemon names every event by its domain type, so there is no
		// default-typed message to listen for -- subscribe to the union.
		const types = [
			'plan_created', 'plan_proposed', 'plan_approved',
			'attempt_started', 'attempt_provisioned', 'subtask_advanced',
			'runtime_observed', 'checkpoint_recorded',
			'initiative_settled', 'initiative_failed'
		];
		const handle = (event: MessageEvent<string>) => {
			try {
				onEvent(event.type, JSON.parse(event.data) as unknown);
			} catch {
				/* A malformed frame is not worth tearing the stream down for. */
			}
		};
		for (const type of types) source.addEventListener(type, handle as EventListener);
		source.addEventListener('open', () => onStatus(true));
		source.addEventListener('error', () => onStatus(false));
		return () => {
			for (const type of types) source.removeEventListener(type, handle as EventListener);
			source.close();
		};
	}
};

/**
 * Routes the daemon does not expose yet. Named here so a later unit reads the
 * gap instead of assuming an endpoint exists.
 *
 * - `GET /plans` — no collection route (`herdsman/daemon.py` registers only
 *   create, get-by-id, approve, run, graph, risk, run-initiative, settle,
 *   discard and events). The UI can open a plan by id but cannot enumerate
 *   plans, so Home (H1) is blocked on this route as well as on Sprint 10.
 * - Navigation (`GET /nav/codemap`, `/nav/tour`, `/nav/flow/{name}`,
 *   `/nav/symbol/{name}`) is served by the daemon from the same
 *   `herdsman/nav.py` evidence the CLI reads offline; the typed client above
 *   is the seam future R13/R14 views build on. No nav view exists yet.
 */
export const MISSING_ROUTES = ['GET /plans'] as const;
