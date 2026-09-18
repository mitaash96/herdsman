<script lang="ts">
	import { parseMarkdown, type Block, type Span } from './markdown';

	let {
		source,
		/** Shown when the body is empty, in the caller's own words. */
		empty = 'This asset has no body. Its frontmatter is all there is.'
	}: { source: string; empty?: string } = $props();

	const blocks = $derived<Block[]>(parseMarkdown(source));

	/** The widest line number a gutter has to hold, so it never reflows mid-block. */
	const gutter = (lines: string[]) => `${String(lines.length).length}ch`;
</script>

<!--
  The document, in the Force Diagram's two voices. Chivo Mono sets everything
  here: Archivo is the display, the mark and the in-sheet headline, and a
  document heading is none of those. Hierarchy is scale, weight and a hairline.

  Nothing on this path renders HTML. `parseMarkdown` emits tokens and every one
  of them lands in a Svelte element below, so an asset body carrying `<script>`
  reads as the characters it contains — which is also the only correct thing for
  a reader to do with them.
-->
{#snippet inline(spans: Span[])}
	{#each spans as span}
		{#if span.kind === 'text'}{span.value}{:else if span.kind === 'code'}<code
				>{span.value}</code
			>{:else if span.kind === 'strong'}<strong>{@render inline(span.spans)}</strong
			>{:else if span.kind === 'em'}<em>{@render inline(span.spans)}</em
			>{:else if span.kind === 'link'}<a
				href={span.href}
				rel="noreferrer noopener"
				target={span.href.startsWith('#') ? null : '_blank'}>{@render inline(span.spans)}</a
			>{/if}
	{/each}
{/snippet}

<div class="doc prose">
	{#if blocks.length === 0}
		<p class="quiet">{empty}</p>
	{/if}
	{#each blocks as block, index (index)}
		{#if block.kind === 'heading'}
			{#if block.level === 1}
				<h3 class="h h1">{@render inline(block.spans)}</h3>
			{:else if block.level === 2}
				<h4 class="h h2">{@render inline(block.spans)}</h4>
			{:else if block.level === 3}
				<h5 class="h h3">{@render inline(block.spans)}</h5>
			{:else}
				<h6 class="h h4">{@render inline(block.spans)}</h6>
			{/if}
		{:else if block.kind === 'paragraph'}
			<p>{@render inline(block.spans)}</p>
		{:else if block.kind === 'rule'}
			<hr />
		{:else if block.kind === 'code'}
			<!-- The reading priority of this unit. A code block keeps its own
			     lines: it never wraps, it scrolls inside its own plate, and the
			     gutter numbers let an operator talk about line 40 of a skill. -->
			<figure class="code plate">
				<figcaption class="code-head">
					<span class="label">{block.language === '' ? 'Code' : block.language}</span>
					<span class="rule"></span>
					<span class="label n">{block.lines.length} lines</span>
				</figcaption>
				<pre style="--gutter: {gutter(block.lines)}"><code>{#each block.lines as line, n (n)}<span
								class="ln" aria-hidden="true">{n + 1}</span><span class="src">{line}
</span>{/each}</code></pre>
			</figure>
		{:else if block.kind === 'list'}
			{#if block.ordered}
				<ol start={block.start}>
					{#each block.items as item, n (n)}
						<li style="--depth: {item.depth}">{@render inline(item.spans)}</li>
					{/each}
				</ol>
			{:else}
				<ul>
					{#each block.items as item, n (n)}
						<li style="--depth: {item.depth}">
							<span class="mark" aria-hidden="true"></span>{@render inline(item.spans)}
						</li>
					{/each}
				</ul>
			{/if}
		{:else if block.kind === 'quote'}
			<blockquote>
				{#each block.paragraphs as spans, n (n)}
					<p>{@render inline(spans)}</p>
				{/each}
			</blockquote>
		{:else}
			<!-- A table is wide content, so it scrolls inside its own region and
			     never makes the page scroll sideways. -->
			<div class="scroller">
				<table>
					<thead>
						<tr>
							{#each block.head as cell, n (n)}
								<th scope="col" style="text-align: {block.align[n] ?? 'left'}"
									>{@render inline(cell)}</th
								>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each block.rows as row, r (r)}
							<tr>
								{#each row as cell, n (n)}
									<td style="text-align: {block.align[n] ?? 'left'}"
										>{@render inline(cell)}</td
									>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/each}
</div>

<style>
	.doc {
		/* The reading measure. Code, tables and rules run the full column; only
		   prose is held to it, which is what the 68ch is for. */
		max-width: none;
	}
	.doc > p,
	.doc > ul,
	.doc > ol,
	.doc > blockquote,
	.doc > .h {
		max-width: 68ch;
	}

	p {
		margin: 0 0 1rem;
		overflow-wrap: anywhere;
	}
	.quiet {
		color: var(--ink-2);
	}

	/* --- headings: Chivo Mono, because Archivo is display, mark and in-sheet
	   headline and a document heading is none of the three. More space above
	   than below, so a heading belongs to what follows it. ------------------- */
	.h {
		margin: 2rem 0 0.75rem;
		font-weight: 500;
		color: var(--ink);
		letter-spacing: -0.01em;
		line-height: 1.3;
		text-wrap: balance;
		overflow-wrap: anywhere;
	}
	.doc > .h:first-child {
		margin-top: 0;
	}
	.h1 {
		font-size: 1.125rem;
	}
	.h2 {
		font-size: 1rem;
		padding-bottom: 0.4rem;
		border-bottom: 1px solid var(--rule);
	}
	.h3 {
		font-size: 0.9375rem;
	}
	.h4 {
		font-size: 0.875rem;
		color: var(--ink-2);
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	hr {
		max-width: 68ch;
		margin: 1.75rem 0;
		border: 0;
		border-top: 1px solid var(--rule);
	}

	/* --- lists: the marker is drawn, never a glyph ------------------------- */
	ul,
	ol {
		margin: 0 0 1rem;
		padding: 0;
		list-style: none;
	}
	li {
		position: relative;
		margin: 0 0 0.35rem;
		padding-left: calc(1.5rem + var(--depth, 0) * 1.25rem);
		overflow-wrap: anywhere;
	}
	.mark {
		position: absolute;
		left: calc(var(--depth, 0) * 1.25rem + 0.35rem);
		top: 0.8em;
		width: 0.5rem;
		height: 1px;
		background: var(--member-line);
	}
	ol {
		counter-reset: item;
	}
	ol > li {
		counter-increment: item;
	}
	ol > li::before {
		content: counter(item) '.';
		position: absolute;
		left: calc(var(--depth, 0) * 1.25rem);
		color: var(--ink-2);
		font-variant-numeric: tabular-nums;
	}

	blockquote {
		margin: 0 0 1rem;
		padding-left: 1rem;
		border-left: 1px solid var(--rule-strong);
		color: var(--ink-2);
	}
	blockquote p:last-child {
		margin-bottom: 0;
	}

	/* --- inline ------------------------------------------------------------ */
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
		overflow-wrap: anywhere;
	}
	strong {
		font-weight: 600;
		color: var(--ink);
	}
	em {
		font-style: normal;
		/* No italic in this system's two voices. Emphasis is a dashed rule, the
		   same mark a slack reading carries. */
		border-bottom: 1px dashed var(--rule-strong);
	}

	/* --- the code block ----------------------------------------------------
	   The unit's stated reading priority. It is a plate like every other: plate
	   tone, one hairline, two cuts, no shadow. It never wraps — wrapped code
	   lies about its own lines — so it scrolls inside itself, and the gutter is
	   divided from the source by the same hairline as everything else. */
	.code {
		--cut: 10px;
		margin: 0 0 1.25rem;
		background: var(--plate);
		border: 1px solid var(--rule);
		overflow: hidden;
	}
	.code-head {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.5rem 0.875rem;
		border-bottom: 1px solid var(--rule);
	}
	.code-head .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
	}
	pre {
		margin: 0;
		padding: 0.75rem 0;
		overflow-x: auto;
		font-size: 0.8125rem;
		line-height: 1.7;
	}
	pre code {
		display: block;
		min-width: max-content;
		background: none;
		border: 0;
		padding: 0;
		overflow-wrap: normal;
	}
	.ln {
		display: inline-block;
		width: var(--gutter, 2ch);
		margin-right: 0.875rem;
		padding-right: 0.875rem;
		border-right: 1px solid var(--rule);
		color: var(--ink-2);
		text-align: right;
		user-select: none;
		font-variant-numeric: tabular-nums;
	}
	.src {
		color: var(--ink);
		white-space: pre;
		padding-right: 0.875rem;
	}

	/* --- tables: the load schedule's rules, unchanged ---------------------- */
	.scroller {
		max-width: 100%;
		margin: 0 0 1.25rem;
		overflow-x: auto;
	}
	table {
		border-collapse: collapse;
		width: 100%;
		min-width: 30rem;
	}
	th,
	td {
		padding: 0.5rem 0.875rem 0.5rem 0;
		border-bottom: 1px solid var(--rule);
		vertical-align: top;
		/* `anywhere` also shrinks a column to its narrowest breakable width, so a
		   table of short identifiers came out with `required_checks` split across
		   two lines. Break between words here and let the scroller carry anything
		   that genuinely does not fit. */
		overflow-wrap: break-word;
	}
	th code,
	td code {
		overflow-wrap: normal;
		white-space: nowrap;
	}
	th {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-2);
		border-bottom: 1px solid var(--rule-strong);
		white-space: nowrap;
	}
	td {
		color: var(--ink-2);
	}

	@media (max-width: 60rem) {
		pre {
			font-size: 0.75rem;
		}
		.ln {
			margin-right: 0.5rem;
			padding-right: 0.5rem;
		}
	}
</style>
