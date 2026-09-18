/**
 * A Markdown subset, parsed into typed tokens the Svelte side renders.
 *
 * Why this exists rather than a dependency: the UI has no runtime dependencies
 * and PRODUCT.md makes a no-network fresh-machine install a hard requirement,
 * so every byte of the reading surface ships in this repository. The parser
 * emits tokens, never HTML — there is no `{@html}` anywhere downstream, so an
 * asset body cannot inject markup and no sanitizer is needed. What the subset
 * does not cover degrades to the literal characters the file contains, which is
 * the only honest failure mode for a document reader: never render it wrong.
 *
 * Covered: ATX headings, paragraphs, fenced code with an info string, ordered
 * and unordered lists nested by indent, blockquotes, GFM pipe tables, thematic
 * breaks, and inline code, links, autolinks, strong, emphasis and backslash
 * escapes. Not covered, and deliberately: setext headings, indented code
 * blocks, reference links, footnotes, images, and raw HTML.
 */

export interface TextSpan {
	kind: 'text';
	value: string;
}

export interface CodeSpan {
	kind: 'code';
	value: string;
}

export interface EmphasisSpan {
	kind: 'strong' | 'em';
	spans: Span[];
}

export interface LinkSpan {
	kind: 'link';
	/** Already filtered: only http, https, mailto and same-document targets. */
	href: string;
	spans: Span[];
}

export type Span = TextSpan | CodeSpan | EmphasisSpan | LinkSpan;

export interface HeadingBlock {
	kind: 'heading';
	level: 1 | 2 | 3 | 4 | 5 | 6;
	spans: Span[];
}

export interface ParagraphBlock {
	kind: 'paragraph';
	spans: Span[];
}

export interface CodeBlock {
	kind: 'code';
	/** The fence's info string, trimmed to its first word; '' when absent. */
	language: string;
	text: string;
	/** Counted once here so the reader can offer a line gutter without splitting twice. */
	lines: string[];
}

export interface ListItem {
	/** Nesting depth, 0 for a top-level item. */
	depth: number;
	spans: Span[];
}

export interface ListBlock {
	kind: 'list';
	ordered: boolean;
	/** The first number of an ordered list; 1 for an unordered one. */
	start: number;
	items: ListItem[];
}

export interface QuoteBlock {
	kind: 'quote';
	/** Each paragraph of the quote, in order. */
	paragraphs: Span[][];
}

export type Align = 'left' | 'center' | 'right';

export interface TableBlock {
	kind: 'table';
	head: Span[][];
	rows: Span[][][];
	align: Align[];
}

export interface RuleBlock {
	kind: 'rule';
}

export type Block =
	| HeadingBlock
	| ParagraphBlock
	| CodeBlock
	| ListBlock
	| QuoteBlock
	| TableBlock
	| RuleBlock;

const FENCE = /^(\s{0,3})(`{3,}|~{3,})\s*([^`]*)$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const DIVIDER = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

/**
 * A link target this build is willing to render.
 *
 * Everything downstream binds it to `href`, and Svelte escapes attribute values
 * but does not judge schemes, so `javascript:` and `data:` are refused here
 * rather than in the component. A target that does not pass renders as the
 * link's own text, so the document still reads and nothing is silently dropped.
 */
function safeHref(raw: string): string | null {
	const href = raw.trim();
	if (href === '') return null;
	if (/^(?:https?:|mailto:)/i.test(href)) return href;
	// A scheme-less target is a relative path or a fragment, both harmless.
	// Anything carrying a colon before the first slash is claiming a scheme.
	const colon = href.indexOf(':');
	const slash = href.indexOf('/');
	if (colon >= 0 && (slash < 0 || colon < slash)) return null;
	return href;
}

/** Split a table row on unescaped pipes, dropping the leading and trailing one. */
function cells(line: string): string[] {
	const parts: string[] = [];
	let current = '';
	for (let i = 0; i < line.length; i += 1) {
		const char = line[i];
		if (char === '\\' && i + 1 < line.length) {
			current += char + line[i + 1];
			i += 1;
		} else if (char === '|') {
			parts.push(current);
			current = '';
		} else {
			current += char;
		}
	}
	parts.push(current);
	if (parts.length > 0 && parts[0].trim() === '') parts.shift();
	if (parts.length > 0 && parts[parts.length - 1].trim() === '') parts.pop();
	return parts.map((part) => part.trim());
}

function alignments(divider: string): Align[] {
	return cells(divider).map((cell) => {
		const left = cell.startsWith(':');
		const right = cell.endsWith(':');
		if (left && right) return 'center';
		if (right) return 'right';
		return 'left';
	});
}

/** Emphasis and link delimiters, so a run of plain text can be taken whole. */
const SPECIAL = new Set(['`', '*', '_', '[', '<', '\\']);

/**
 * Inline spans, left to right, code first.
 *
 * Code spans bind tighter than anything else, exactly as CommonMark has them,
 * so a backtick run swallows `*` and `[` without them meaning emphasis or a
 * link. An opener with no closer is not a delimiter at all: it is the literal
 * character, which is what keeps a contract's `required_paths: src/*` readable.
 */
export function parseInline(source: string): Span[] {
	const spans: Span[] = [];
	let text = '';

	const flush = () => {
		if (text !== '') {
			spans.push({ kind: 'text', value: text });
			text = '';
		}
	};

	let i = 0;
	while (i < source.length) {
		const char = source[i];

		if (!SPECIAL.has(char)) {
			// Take the whole run of ordinary characters in one step; a
			// character-at-a-time loop over an 8KB document is the slow way to
			// do nothing.
			let end = i + 1;
			while (end < source.length && !SPECIAL.has(source[end])) end += 1;
			text += source.slice(i, end);
			i = end;
			continue;
		}

		if (char === '\\') {
			// An escape covers only ASCII punctuation; a backslash before a
			// letter is a backslash.
			const next = source[i + 1];
			if (next !== undefined && /[!-/:-@[-`{-~]/.test(next)) {
				text += next;
				i += 2;
			} else {
				text += char;
				i += 1;
			}
			continue;
		}

		if (char === '`') {
			let ticks = 0;
			while (source[i + ticks] === '`') ticks += 1;
			const fence = '`'.repeat(ticks);
			const close = source.indexOf(fence, i + ticks);
			// A closing run must be exactly this long, or the span runs on.
			if (close >= 0 && source[close + ticks] !== '`') {
				flush();
				const value = source.slice(i + ticks, close);
				// CommonMark strips one leading and trailing space from a code
				// span that has both, which is how ``` ` ``` writes a backtick.
				spans.push({
					kind: 'code',
					value:
						value.length > 1 && value.startsWith(' ') && value.endsWith(' ')
							? value.slice(1, -1)
							: value
				});
				i = close + ticks;
				continue;
			}
			text += fence;
			i += ticks;
			continue;
		}

		if (char === '<') {
			const close = source.indexOf('>', i + 1);
			const inner = close > 0 ? source.slice(i + 1, close) : '';
			if (close > 0 && /^(?:https?:\/\/|mailto:)\S+$/i.test(inner)) {
				flush();
				spans.push({
					kind: 'link',
					href: inner,
					spans: [{ kind: 'text', value: inner }]
				});
				i = close + 1;
				continue;
			}
			// Not an autolink, so it is a literal angle bracket. This is also
			// where raw HTML lands, and rendering it as text is the point.
			text += char;
			i += 1;
			continue;
		}

		if (char === '[') {
			const label = matchBracket(source, i);
			if (label >= 0 && source[label + 1] === '(') {
				const target = matchParen(source, label + 1);
				if (target >= 0) {
					const raw = source.slice(label + 2, target);
					// A title after the URL is accepted and ignored; nothing in
					// this surface has a place to show one.
					const href = safeHref(raw.split(/\s+/)[0] ?? '');
					const inner = parseInline(source.slice(i + 1, label));
					flush();
					if (href === null) {
						spans.push(...inner);
					} else {
						spans.push({ kind: 'link', href, spans: inner });
					}
					i = target + 1;
					continue;
				}
			}
			text += char;
			i += 1;
			continue;
		}

		// `*` or `_`. A run of two opens strong, one opens emphasis; an
		// underscore inside a word is not a delimiter, which is what keeps
		// snake_case identifiers intact in prose.
		const run = char === source[i + 1] ? 2 : 1;
		const marker = char.repeat(run);
		const wordInner =
			char === '_' && i > 0 && /[A-Za-z0-9_]/.test(source[i - 1] ?? '');
		const close = wordInner ? -1 : findClose(source, i + run, marker);
		if (close < 0) {
			text += marker;
			i += run;
			continue;
		}
		flush();
		spans.push({
			kind: run === 2 ? 'strong' : 'em',
			spans: parseInline(source.slice(i + run, close))
		});
		i = close + run;
	}

	flush();
	return spans;
}

/** The index of the `]` closing the `[` at `open`, honouring nesting and escapes. */
function matchBracket(source: string, open: number): number {
	let depth = 0;
	for (let i = open; i < source.length; i += 1) {
		const char = source[i];
		if (char === '\\') {
			i += 1;
		} else if (char === '[') {
			depth += 1;
		} else if (char === ']') {
			depth -= 1;
			if (depth === 0) return i;
		}
	}
	return -1;
}

/** The index of the `)` closing the `(` at `open`, honouring nesting and escapes. */
function matchParen(source: string, open: number): number {
	let depth = 0;
	for (let i = open; i < source.length; i += 1) {
		const char = source[i];
		if (char === '\\') {
			i += 1;
		} else if (char === '(') {
			depth += 1;
		} else if (char === ')') {
			depth -= 1;
			if (depth === 0) return i;
		}
	}
	return -1;
}

/**
 * Where `marker` closes, skipping code spans so a backtick run wins.
 *
 * A closer must not be followed by more of the same character, or `**a**` would
 * close its strong run on the first of the two trailing asterisks and leave one
 * behind.
 */
function findClose(source: string, from: number, marker: string): number {
	for (let i = from; i < source.length; i += 1) {
		const char = source[i];
		if (char === '\\') {
			i += 1;
			continue;
		}
		if (char === '`') {
			let ticks = 0;
			while (source[i + ticks] === '`') ticks += 1;
			const close = source.indexOf('`'.repeat(ticks), i + ticks);
			if (close < 0) return -1;
			i = close + ticks - 1;
			continue;
		}
		if (source.startsWith(marker, i) && source[i + marker.length] !== marker[0]) {
			// An empty run (`**` immediately closed) is literal, not emphasis.
			return i === from ? -1 : i;
		}
	}
	return -1;
}

/** How deep an indent nests a list item: two spaces per level, tabs as four. */
function depthOf(indent: string): number {
	const columns = indent.replace(/\t/g, '    ').length;
	return Math.min(Math.floor(columns / 2), 5);
}

/**
 * Parse one Markdown document into blocks.
 *
 * Single pass, line by line. Nothing here throws: a malformed document is a
 * document, and an asset the operator cannot read is worse than one whose
 * italics did not resolve.
 */
export function parseMarkdown(source: string): Block[] {
	const lines = source.replace(/\r\n?/g, '\n').split('\n');
	const blocks: Block[] = [];
	let paragraph: string[] = [];

	const closeParagraph = () => {
		if (paragraph.length === 0) return;
		blocks.push({ kind: 'paragraph', spans: parseInline(paragraph.join(' ')) });
		paragraph = [];
	};

	let i = 0;
	while (i < lines.length) {
		const line = lines[i];

		const fence = FENCE.exec(line);
		if (fence) {
			closeParagraph();
			const marker = fence[2][0];
			const width = fence[2].length;
			const body: string[] = [];
			i += 1;
			// An unclosed fence runs to the end of the document rather than
			// swallowing the rest as prose — a truncated file still reads.
			while (i < lines.length) {
				const candidate = lines[i].trim();
				if (
					candidate.startsWith(marker.repeat(width)) &&
					candidate.split('').every((char) => char === marker)
				) {
					i += 1;
					break;
				}
				body.push(lines[i]);
				i += 1;
			}
			blocks.push({
				kind: 'code',
				language: (fence[3] ?? '').trim().split(/\s+/)[0] ?? '',
				text: body.join('\n'),
				lines: body
			});
			continue;
		}

		if (line.trim() === '') {
			closeParagraph();
			i += 1;
			continue;
		}

		// A rule is tested before a bullet, or `- - -` reads as a list item.
		if (RULE.test(line)) {
			closeParagraph();
			blocks.push({ kind: 'rule' });
			i += 1;
			continue;
		}

		const heading = HEADING.exec(line);
		if (heading) {
			closeParagraph();
			blocks.push({
				kind: 'heading',
				level: heading[1].length as HeadingBlock['level'],
				spans: parseInline(heading[2])
			});
			i += 1;
			continue;
		}

		if (QUOTE.test(line)) {
			closeParagraph();
			const collected: string[] = [];
			while (i < lines.length) {
				const quoted = QUOTE.exec(lines[i]);
				if (quoted) {
					collected.push(quoted[1]);
					i += 1;
					continue;
				}
				// A lazy continuation: an unmarked line still inside the quote.
				if (lines[i].trim() !== '' && collected.length > 0) {
					collected.push(lines[i]);
					i += 1;
					continue;
				}
				break;
			}
			const paragraphs: Span[][] = [];
			let held: string[] = [];
			for (const quoted of collected) {
				if (quoted.trim() === '') {
					if (held.length > 0) paragraphs.push(parseInline(held.join(' ')));
					held = [];
				} else {
					held.push(quoted.trim());
				}
			}
			if (held.length > 0) paragraphs.push(parseInline(held.join(' ')));
			blocks.push({ kind: 'quote', paragraphs });
			continue;
		}

		const bullet = BULLET.exec(line);
		const ordered = ORDERED.exec(line);
		if (bullet || ordered) {
			closeParagraph();
			const isOrdered = ordered !== null && bullet === null;
			const start = isOrdered ? Number(ordered[2]) : 1;
			const items: ListItem[] = [];
			while (i < lines.length) {
				const nextBullet = BULLET.exec(lines[i]);
				const nextOrdered = ORDERED.exec(lines[i]);
				const match = nextBullet ?? nextOrdered;
				if (match) {
					// A list stops when its marker family changes at the top
					// level, so a bulleted list under a numbered one is two
					// blocks rather than one mislabelled one.
					const thisOrdered = nextOrdered !== null && nextBullet === null;
					if (depthOf(match[1]) === 0 && thisOrdered !== isOrdered) break;
					items.push({
						depth: depthOf(match[1]),
						spans: parseInline(match[match.length - 1])
					});
					i += 1;
					continue;
				}
				// A continuation line: indented, non-blank, and not a new
				// block. It belongs to the item above it.
				if (
					items.length > 0 &&
					lines[i].trim() !== '' &&
					/^\s+\S/.test(lines[i]) &&
					!FENCE.test(lines[i])
				) {
					const last = items[items.length - 1];
					last.spans = parseInline(
						renderPlain(last.spans) + ' ' + lines[i].trim()
					);
					i += 1;
					continue;
				}
				break;
			}
			blocks.push({ kind: 'list', ordered: isOrdered, start, items });
			continue;
		}

		// A pipe table is a header line whose next line is the divider. Both
		// must be present, or the pipes are ordinary characters in a sentence.
		if (line.includes('|') && i + 1 < lines.length && DIVIDER.test(lines[i + 1])) {
			closeParagraph();
			const align = alignments(lines[i + 1]);
			const head = cells(line).map(parseInline);
			const rows: Span[][][] = [];
			i += 2;
			while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
				rows.push(cells(lines[i]).map(parseInline));
				i += 1;
			}
			blocks.push({ kind: 'table', head, rows, align });
			continue;
		}

		paragraph.push(line.trim());
		i += 1;
	}

	closeParagraph();
	return blocks;
}

/** A span tree flattened back to its source-ish text, for list continuations. */
function renderPlain(spans: Span[]): string {
	return spans
		.map((span) => {
			switch (span.kind) {
				case 'text':
					return span.value;
				case 'code':
					return '`' + span.value + '`';
				case 'strong':
					return '**' + renderPlain(span.spans) + '**';
				case 'em':
					return '*' + renderPlain(span.spans) + '*';
				case 'link':
					return '[' + renderPlain(span.spans) + '](' + span.href + ')';
			}
		})
		.join('');
}

/**
 * The document's headings, for a reader that wants to say how long a read is.
 *
 * Derived on demand rather than stored on the blocks, because a heading list
 * that can disagree with the document it describes is worse than no list.
 */
export function outline(blocks: Block[]): { level: number; text: string }[] {
	return blocks
		.filter((block): block is HeadingBlock => block.kind === 'heading')
		.map((block) => ({ level: block.level, text: renderPlain(block.spans) }));
}
