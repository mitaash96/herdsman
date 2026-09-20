import type {
	NavCliCommand,
	NavEdge,
	NavEntryPoints,
	NavIndex,
	NavRoute as DaemonRoute,
	NavSymbol,
	NavTestEntry
} from './daemon';

export type RouteKind = 'tour' | 'flow' | 'derived';
export type Resolution = NavEdge['resolution'];

export interface Citation {
	file: string;
	line: number;
	ref: string | null;
}

export interface RouteStop {
	id: string;
	label: string;
	citations: Citation[];
	fact: string | null;
	checkpoint: string | null;
	resolution: Resolution | null;
	symbol: NavSymbol | null;
	depth: number;
	terminal: boolean;
	reason: string | null;
}

export interface Route {
	id: string;
	kind: RouteKind;
	label: string;
	source: string;
	stops: RouteStop[];
	/** Present only on a derived route, so its selected walk need not re-index every entry head. */
	head?: NavSymbol | null;
}

interface Entry {
	id: string;
	label: string;
	source: string;
	symbol: NavSymbol | null;
}

const citation = (file: string, line: number, ref: string | null = null): Citation => ({ file, line, ref });
const source = (file: string, line: number | null): string => `${file}:${line ?? '—'}`;
export const symbolRef = (symbol: NavSymbol): string => `${symbol.module}:${symbol.name}`;
const byName = (index: NavIndex, name: string): NavSymbol | null =>
	index.symbols.find((symbol) => symbol.name === name || symbolRef(symbol) === name) ?? null;
const byFileLine = (index: NavIndex, file: string, line: number): NavSymbol | null =>
	index.symbols.find((symbol) => symbol.file === file && symbol.line === line) ?? null;
const bySuffix = (index: NavIndex, name: string): NavSymbol | null =>
	index.symbols.find((symbol) => symbol.name === name || symbol.name.endsWith(`.${name}`)) ?? null;
const byFileName = (index: NavIndex, file: string, name: string): NavSymbol | null =>
	index.symbols.find((symbol) => symbol.file === file && (symbol.name === name || symbol.name.endsWith(`.${name}`))) ?? null;

function entrySymbol(index: NavIndex, entry: NavCliCommand | DaemonRoute | NavTestEntry): NavSymbol | null {
	if ('handler' in entry) return byFileName(index, entry.file, entry.handler) ?? bySuffix(index, entry.handler);
	if ('node' in entry) {
		const name = entry.node.split('::').at(-1) ?? '';
		return byFileName(index, entry.file, name) ?? bySuffix(index, name);
	}
	return byFileLine(index, entry.file, entry.line);
}

/** Every declared entry remains a route head; the register never silently caps one class. */
function entriesOf(index: NavIndex): Entry[] {
	const entries: Entry[] = [];
	const script = index.entry_points.console_script;
	if (script) {
		const name = script.target.replace(':', '.');
		entries.push({
			id: `script:${script.name}`,
			label: script.name,
			source: source(script.file, script.line),
			symbol: byName(index, name) ?? bySuffix(index, name.split('.').at(-1) ?? '')
		});
	}
	for (const item of index.entry_points.cli) {
		entries.push({
			id: `cli:${item.command}:${item.file}:${item.line}`,
			label: item.command,
			source: source(item.file, item.line),
			symbol: entrySymbol(index, item)
		});
	}
	for (const item of index.entry_points.routes) {
		entries.push({
			id: `route:${item.method}:${item.path}`,
			label: `${item.method} ${item.path}`,
			source: source(item.file, item.line),
			symbol: entrySymbol(index, item)
		});
	}
	for (const item of index.entry_points.tests) {
		entries.push({
			id: `test:${item.node}`,
			label: item.node,
			source: source(item.file, item.line),
			symbol: entrySymbol(index, item)
		});
	}
	return entries;
}

/**
 * One deterministic structural walk from an entry head. Every hop is an indexed
 * calls/instantiates edge; an unindexed destination is kept as the visible end.
 */
function walkFromEntry(index: NavIndex, entry: Entry, outgoingBySource: ReadonlyMap<string, readonly NavEdge[]>): Route {
	const stops: RouteStop[] = [];
	const edgesFrom = (symbol: NavSymbol) =>
		outgoingBySource.get(symbolRef(symbol)) ?? outgoingBySource.get(symbol.name) ?? [];

	function walk(symbol: NavSymbol, depth: number, trail: Set<string>, incoming: NavEdge | null): void {
		stops.push({
			id: `${symbol.file}:${symbol.line}:${incoming?.dst ?? symbol.name}:${depth}`,
			label: symbol.name,
			citations: [citation(symbol.file, symbol.line)],
			fact: null,
			checkpoint: null,
			resolution: incoming?.resolution ?? null,
			symbol,
			depth,
			terminal: false,
			reason: null
		});
		const current = stops.at(-1)!;
		const outgoing = edgesFrom(symbol);
		if (outgoing.length === 0) {
			current.terminal = true;
			current.reason = 'No calls or constructions are indexed below this symbol.';
			return;
		}
		// A route is one legible descent, not a node cloud. The selected symbol
		// still exposes every incident edge, summarised before its full list.
		const edge = outgoing[0];
		const next = byName(index, edge.dst);
		if (!next) {
			stops.push({
				id: `${edge.file}:${edge.line}:${edge.dst}:${depth + 1}`,
				label: edge.dst,
				citations: [citation(edge.file, edge.line)],
				fact: null,
				checkpoint: null,
				resolution: edge.resolution,
				symbol: null,
				depth: depth + 1,
				terminal: true,
				reason: 'This edge has no indexed destination, so the structural route ends here.'
			});
			return;
		}
		if (trail.has(symbolRef(next))) {
			stops.push({
				id: `${edge.file}:${edge.line}:${next.name}:cycle`,
				label: next.name,
				citations: [citation(edge.file, edge.line)],
				fact: null,
				checkpoint: null,
				resolution: edge.resolution,
				symbol: next,
				depth: depth + 1,
				terminal: true,
				reason: 'This walk reached a symbol already on its path, so it stops rather than claiming a loop is a new route.'
			});
			return;
		}
		walk(next, depth + 1, new Set([...trail, symbolRef(next)]), edge);
	}

	if (entry.symbol) walk(entry.symbol, 0, new Set([symbolRef(entry.symbol)]), null);
	else {
		const [file, rawLine] = entry.source.split(':');
		stops.push({
			id: `${entry.id}:unindexed`,
			label: entry.label,
			citations: Number.isFinite(Number(rawLine)) ? [citation(file, Number(rawLine))] : [],
			fact: null,
			checkpoint: null,
			resolution: null,
			symbol: null,
			depth: 0,
			terminal: true,
			reason: 'This declared entry point has no indexed symbol head.'
		});
	}
	return { id: `derived:${entry.id}`, kind: 'derived', label: entry.label, source: entry.source, stops, head: entry.symbol };
}

const outgoingByIndex = new WeakMap<NavIndex, ReadonlyMap<string, readonly NavEdge[]>>();

function outgoingIndex(index: NavIndex): ReadonlyMap<string, readonly NavEdge[]> {
	const outgoingBySource = new Map<string, NavEdge[]>();
	for (const edge of index.edges) {
		if (edge.kind !== 'calls' && edge.kind !== 'instantiates') continue;
		const edges = outgoingBySource.get(edge.src) ?? [];
		edges.push(edge);
		outgoingBySource.set(edge.src, edges);
	}
	for (const edges of outgoingBySource.values())
		edges.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line || a.dst.localeCompare(b.dst));
	return outgoingBySource;
}

/** Walk one selected route head only; the rail never pays to expand 747 graphs it is not reading. */
export function walkDerivedRoute(index: NavIndex, route: Route): Route {
	if (route.kind !== 'derived') return route;
	let outgoing = outgoingByIndex.get(index);
	if (!outgoing) {
		outgoing = outgoingIndex(index);
		outgoingByIndex.set(index, outgoing);
	}
	return walkFromEntry(index, {
		id: route.id.slice('derived:'.length), label: route.label, source: route.source, symbol: route.head ?? null
	}, outgoing);
}

/** The 1 + CLI + daemon routes + tests route heads, in daemon emission order. */
export function derivedRoutes(index: NavIndex): Route[] {
	return entriesOf(index).map((entry) => ({
		id: `derived:${entry.id}`,
		kind: 'derived',
		label: entry.label,
		source: entry.source,
		stops: [],
		head: entry.symbol
	}));
}

function parseCitation(line: string): Citation | null {
	const match = line.trim().match(/^`(.+?):(\d+)`(?:\s+\(`([^`]+)`\))?$/);
	return match ? citation(match[1], Number(match[2]), match[3] ?? null) : null;
}

function parseStops(text: string): { number: number; label: string; lines: string[] }[] {
	const stops: { number: number; label: string; lines: string[] }[] = [];
	let current: { number: number; label: string; lines: string[] } | null = null;
	for (const line of text.split('\n')) {
		const heading = line.match(/^(\d+)\.\s+(.+?)\s*$/);
		if (heading) {
			current = { number: Number(heading[1]), label: heading[2], lines: [] };
			stops.push(current);
		} else if (current) current.lines.push(line.trim());
	}
	return stops;
}

/** Parse only Herdsman's authored five-stop envelope; any other shape is an absence, not a guess. */
export function parseTour(text: unknown): Route | null {
	if (typeof text !== 'string' || !text.startsWith('Guided tour —')) return null;
	const parsed = parseStops(text);
	if (parsed.length !== 5 || parsed.some((step, at) => step.number !== at + 1 || !step.label)) return null;
	const stops: RouteStop[] = [];
	for (const [at, step] of parsed.entries()) {
		const citations = step.lines.map(parseCitation).filter((item): item is Citation => item !== null);
		const checkpoint = step.lines.find((line) => line.startsWith('Checkpoint:'))?.slice('Checkpoint:'.length).trim();
		const fact = step.lines.find((line) => /^(Fact:|Interpretation:|Drill down:)\s*\S/.test(line));
		if (!checkpoint || !fact) return null;
		stops.push({
			id: `tour:${at + 1}`,
			label: step.label,
			citations,
			fact,
			checkpoint,
			resolution: null,
			symbol: null,
			depth: 0,
			terminal: false,
			reason: null
		});
	}
	return { id: 'tour', kind: 'tour', label: 'The curated tour', source: 'authored facts · 5 stops', stops };
}

/** Parse the known flow envelope without promoting a malformed response into a route. */
export function parseFlow(text: unknown, name: string): Route | null {
	if (typeof text !== 'string' || !text.startsWith(`Flow: ${name} —`)) return null;
	const parsed = parseStops(text);
	if (parsed.length === 0) return null;
	return {
		id: `flow:${name}`,
		kind: 'flow',
		label: 'The curated flow',
		source: name,
		stops: parsed.map((step, at) => ({
			id: `flow:${name}:${at + 1}`,
			label: step.label,
			citations: step.lines.map(parseCitation).filter((item): item is Citation => item !== null),
			fact: step.lines.find((line) => /^(Fact:|Interpretation:|\[absent\])/.test(line)) ?? null,
			checkpoint: null,
			resolution: null,
			symbol: null,
			depth: 0,
			terminal: false,
			reason: null
		}))
	};
}

export function routeSet(index: NavIndex, tour: Route | null, flow: Route | null): Route[] {
	return [...(tour ? [tour] : []), ...(flow ? [flow] : []), ...derivedRoutes(index)];
}

/** Case-folded filtering over a route already on screen; no class is capped or hidden. */
export function filterRoutes(routes: readonly Route[], query: string): Route[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return [...routes];
	return routes.filter((route) =>
		`${route.label} ${route.source} ${route.stops.map((stop) => `${stop.label} ${stop.fact ?? ''}`).join(' ')}`
			.toLowerCase()
			.includes(needle)
	);
}

export function filterSymbols(symbols: readonly NavSymbol[], query: string): NavSymbol[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return [...symbols];
	return symbols.filter((symbol) =>
		`${symbol.name} ${symbol.file} ${symbol.signature}`.toLowerCase().includes(needle)
	);
}

export function summarizeEdges(edges: readonly NavEdge[]): Record<Resolution, number> {
	const summary: Record<Resolution, number> = { static: 0, dynamic: 0, external: 0 };
	for (const edge of edges) summary[edge.resolution] += 1;
	return summary;
}

export function symbolForCitation(index: NavIndex, ref: string | null): NavSymbol | null {
	if (!ref) return null;
	const name = ref.split(':').at(-1) ?? '';
	return byName(index, name);
}
