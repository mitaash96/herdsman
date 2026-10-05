<script lang="ts">
	/*
	  The one markdown reader (DS §9.18): plans, briefs, handoffs, Library assets.
	  Tokens from `markdown.ts` land in Svelte elements; nothing renders HTML, so a
	  body carrying `<script>` reads as the characters it contains.

	  Layouts: doc only; `toc` adds the contents rail (scroll-spy); a `meta`
	  snippet adds the right column (Run → Plan). The caller's `toolbar` snippet
	  sits above the document on a hairline.
	*/
	import type { Snippet } from 'svelte';
	import { parseMarkdown, type Block, type Span } from './markdown';
	import IconButton from './IconButton.svelte';

	let {
		source,
		blocks: given,
		toc = false,
		compact = false,
		wide = false,
		empty = 'Nothing written here yet.',
		toolbar,
		meta
	}: {
		source?: string;
		/** Pre-composed tokens (the Run plan document); wins over `source`. */
		blocks?: Block[];
		toc?: boolean;
		/** Dock variant: 190px rail, no outer padding. */
		compact?: boolean;
		/** Full width instead of the 72ch measure. */
		wide?: boolean;
		empty?: string;
		toolbar?: Snippet;
		meta?: Snippet;
	} = $props();

	const uid = $props.id();
	const blocks = $derived<Block[]>(given ?? parseMarkdown(source ?? ''));

	const plain = (spans: Span[]): string =>
		spans.map((s) => (s.kind === 'text' || s.kind === 'code' ? s.value : plain(s.spans))).join('');
	const headings = $derived(
		blocks
			.map((b, i) => ({ b, i }))
			.filter(({ b }) => b.kind === 'heading' && (b.level === 2 || b.level === 3))
			.map(({ b, i }) => ({ i, level: (b as { level: number }).level, text: plain((b as { spans: Span[] }).spans) }))
	);
	const hid = (i: number) => `${uid}-h${i}`;

	let doc = $state<HTMLElement>();
	let current = $state<number | null>(null);
	$effect(() => {
		if (!toc || !doc || headings.length === 0) return;
		void blocks;
		const els = headings.map((h) => document.getElementById(hid(h.i))).filter((e): e is HTMLElement => !!e);
		const io = new IntersectionObserver(
			(entries) => {
				for (const e of entries) if (e.isIntersecting) current = Number(e.target.getAttribute('data-i'));
			},
			{ rootMargin: '0px 0px -70% 0px' }
		);
		els.forEach((e) => io.observe(e));
		current = headings[0]?.i ?? null;
		return () => io.disconnect();
	});
	function jump(i: number) {
		document.getElementById(hid(i))?.scrollIntoView({ block: 'start', behavior: 'smooth' });
		current = i;
	}

	/* A light token tint for code blocks: keywords, strings, numbers, comments. */
	type Tok = { t: string; c: '' | 'k' | 's' | 'n' | 'c' };
	const KW = /^(?:const|let|var|function|return|if|else|for|while|import|from|export|def|class|async|await|true|false|null|None|True|False|in|of|new|type|interface|and|or|not|with|as|yield|fn|pub|use|match)\b/;
	function tint(line: string): Tok[] {
		const out: Tok[] = [];
		let rest = line;
		while (rest) {
			let m: RegExpExecArray | null;
			if ((m = /^(#|\/\/).*/.exec(rest)) && !/^#!/.test(rest)) out.push({ t: m[0], c: 'c' });
			else if ((m = /^("([^"\\]|\\.)*"|'([^'\\]|\\.)*'|`[^`]*`)/.exec(rest))) out.push({ t: m[0], c: 's' });
			else if ((m = /^\d[\d._]*/.exec(rest))) out.push({ t: m[0], c: 'n' });
			else if ((m = KW.exec(rest))) out.push({ t: m[0], c: 'k' });
			else if ((m = /^[A-Za-z_$][\w$-]*|^\s+|^./.exec(rest))) out.push({ t: m[0], c: '' });
			rest = rest.slice(m![0].length);
		}
		return out;
	}

	/* `> [!NOTE] text` is a callout (sodium rule); a quote opening with a lone strong line is labelled. */
	function quoteShape(paragraphs: Span[][]): { callout: boolean; label: string | null; body: Span[][] } {
		const first = paragraphs[0] ?? [];
		const head = first[0];
		if (head?.kind === 'text' && /^\[![A-Z]+\]/.test(head.value)) {
			const m = /^\[!([A-Z]+)\]\s*/.exec(head.value)!;
			const rest: Span[] = [{ kind: 'text', value: head.value.slice(m[0].length) }, ...first.slice(1)];
			return { callout: true, label: m[1].toLowerCase(), body: [rest, ...paragraphs.slice(1)] };
		}
		if (first.length === 1 && head?.kind === 'strong' && paragraphs.length > 1)
			return { callout: false, label: plain(head.spans), body: paragraphs.slice(1) };
		return { callout: false, label: null, body: paragraphs };
	}

	let copied = $state<number | null>(null);
	async function copy(i: number, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = i;
			setTimeout(() => (copied = null), 1400);
		} catch {
			/* clipboard refused: nothing claimed */
		}
	}
</script>

{#snippet inline(spans: Span[])}
	{#each spans as span, n (n)}
		{#if span.kind === 'text'}{span.value}{:else if span.kind === 'code'}<code>{span.value}</code
			>{:else if span.kind === 'strong'}<strong>{@render inline(span.spans)}</strong
			>{:else if span.kind === 'em'}<strong>{@render inline(span.spans)}</strong
			>{:else if span.kind === 'link'}<a href={span.href} rel="noreferrer noopener"
				target={span.href.startsWith('#') ? null : '_blank'}>{@render inline(span.spans)}</a
			>{/if}
	{/each}
{/snippet}

<div class="reader" class:toc class:meta={!!meta} class:compact>
	{#if toc}
		<nav class="rail" aria-label="Contents">
			<p class="lbl">Contents</p>
			{#each headings as h (h.i)}
				<a href="#{hid(h.i)}" class:l3={h.level === 3} class:on={current === h.i}
					aria-current={current === h.i ? 'location' : undefined}
					onclick={(e) => { e.preventDefault(); jump(h.i); }}>{h.text}</a>
			{/each}
		</nav>
	{/if}

	<div class="col">
		{#if toolbar}<div class="rtool">{@render toolbar()}</div>{/if}
		<article class="md" class:wide bind:this={doc}>
			{#if blocks.length === 0}<p class="muted">{empty}</p>{/if}
			{#each blocks as block, i (i)}
				{#if block.kind === 'heading'}
					{#if block.level === 1}<h1 id={hid(i)} data-i={i}>{@render inline(block.spans)}</h1>
					{:else if block.level === 2}<h2 id={hid(i)} data-i={i}>{@render inline(block.spans)}</h2>
					{:else if block.level === 3}<h3 id={hid(i)} data-i={i}>{@render inline(block.spans)}</h3>
					{:else}<h4 id={hid(i)}>{@render inline(block.spans)}</h4>{/if}
				{:else if block.kind === 'paragraph'}
					<p>{@render inline(block.spans)}</p>
				{:else if block.kind === 'rule'}
					<hr />
				{:else if block.kind === 'code'}
					<figure class="code">
						<figcaption>
							<span class="lbl">{block.language || 'code'}</span>
							<span class="cp"><IconButton icon={copied === i ? 'check' : 'copy'} label={copied === i ? 'Copied' : 'Copy code'} small onclick={() => copy(i, block.text)} /></span>
						</figcaption>
						<pre><code>{#each block.lines as line, n (n)}{#each tint(line) as tok, k (k)}{#if tok.c}<span class={tok.c}>{tok.t}</span>{:else}{tok.t}{/if}{/each}{'\n'}{/each}</code></pre>
					</figure>
				{:else if block.kind === 'list'}
					{@const isTask = block.items.some((it) => it.task !== undefined)}
					{#if block.ordered}
						<ol start={block.start}>
							{#each block.items as item, n (n)}<li style:margin-left="{item.depth * 18}px">{@render inline(item.spans)}</li>{/each}
						</ol>
					{:else}
						<ul class:task={isTask}>
							{#each block.items as item, n (n)}
								<li class:done={item.task === true} style:margin-left="{item.depth * 18}px">
									{#if item.task !== undefined}<span class="sr-only">{item.task ? 'Done: ' : 'Open: '}</span>{/if}{@render inline(item.spans)}
								</li>
							{/each}
						</ul>
					{/if}
				{:else if block.kind === 'quote'}
					{@const q = quoteShape(block.paragraphs)}
					<blockquote class:callout={q.callout}>
						{#if q.label}<span class="lbl">{q.label}</span>{/if}
						{#each q.body as spans, n (n)}<p>{@render inline(spans)}</p>{/each}
					</blockquote>
				{:else if block.kind === 'table'}
					<div class="scroller">
						<table class="tbl">
							<thead><tr>{#each block.head as cell, n (n)}<th scope="col" style:text-align={block.align[n] ?? 'left'}>{@render inline(cell)}</th>{/each}</tr></thead>
							<tbody>
								{#each block.rows as row, r (r)}
									<tr>{#each row as cell, n (n)}<td style:text-align={block.align[n] ?? 'left'}>{@render inline(cell)}</td>{/each}</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			{/each}
		</article>
	</div>

	{#if meta}<aside class="meta-col">{@render meta()}</aside>{/if}
</div>

<style>
	.reader { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; min-height: 100%; }
	.reader.toc { grid-template-columns: 220px minmax(0, 1fr); }
	.reader.meta { grid-template-columns: minmax(0, 1fr) 280px; }
	.reader.toc.meta { grid-template-columns: 220px minmax(0, 1fr) 280px; }
	.reader.compact.toc { grid-template-columns: 190px minmax(0, 1fr); }
	.rail { position: sticky; top: 0; padding: 22px 12px 22px 24px; display: grid; gap: 1px; }
	.compact .rail { padding: 0 14px 0 0; }
	.rail .lbl { margin: 0 0 10px 10px; }
	.rail a {
		display: block; padding: 5px 10px; font: 400 12.5px/1.3 var(--f-ui); color: var(--dim); text-decoration: none;
		transition: background var(--t-fast), color var(--t-fast);
	}
	.rail a.l3 { padding-left: 22px; font-size: 12px; }
	.rail a:hover { color: var(--tx); background: var(--p1); }
	.rail a.on { color: var(--tx); background: var(--p2); }
	.col { min-width: 0; padding: 22px 28px 0; }
	.compact .col { padding: 0; }
	.rtool { display: flex; align-items: center; gap: 10px; padding: 0 0 14px; margin-bottom: 22px; border-bottom: 1px solid var(--ln); max-width: var(--measure); }
	.rtool:has(+ .md.wide) { max-width: none; }
	.md { max-width: var(--measure); padding-bottom: 60px; color: var(--tx2); font: 400 14.5px/1.65 var(--f-ui); overflow-wrap: break-word; }
	.compact .md { padding-bottom: 30px; }
	.md.wide { max-width: none; }
	.md h1 { font: 500 30px/1.08 var(--f-label); letter-spacing: 0.02em; text-transform: uppercase; color: var(--tx); margin: 0 0 14px; }
	.md h2 { font: 500 13px var(--f-label); letter-spacing: 0.16em; text-transform: uppercase; color: var(--dim); margin: 30px 0 12px; display: flex; align-items: center; gap: 10px; scroll-margin-top: 16px; }
	.md h2::after { content: ''; flex: 1; height: 1px; background: var(--ln); }
	.md h3 { font: 600 15px/1.3 var(--f-ui); color: var(--tx); margin: 22px 0 8px; scroll-margin-top: 16px; }
	.md h4 { font: 600 13.5px/1.3 var(--f-ui); color: var(--tx); margin: 18px 0 6px; }
	.md p { margin: 0 0 12px; }
	.md strong { color: var(--tx); font-weight: 600; }
	.md a { color: var(--tx); text-decoration: underline dashed var(--ln2); text-underline-offset: 4px; }
	.md ul, .md ol { margin: 0 0 14px; display: grid; gap: 5px; }
	.md ul { padding-left: 18px; }
	.md ul > li { position: relative; }
	.md ul > li::before { content: ''; position: absolute; left: -16px; top: 0.8em; width: 8px; height: 1px; background: var(--dim); }
	.md ol { padding-left: 22px; list-style: decimal; }
	.md ol > li::marker { font: 400 11.5px var(--f-mono); color: var(--dim); }
	.md ul.task > li { padding-left: 6px; }
	.md ul.task > li::before { left: -18px; top: 0.42em; width: 11px; height: 11px; border: 1px solid var(--ln2); background: none; }
	.md ul.task > li.done::before { background: var(--tx2); border-color: var(--tx2); }
	.md ul.task > li.done { color: var(--dim); text-decoration: line-through; text-decoration-color: var(--fnt); }
	.md code { font: 400 12px var(--f-mono); background: var(--p2); border: 1px solid var(--ln); padding: 0 5px; color: var(--tx); }
	.code { margin: 4px 0 16px; border: 1px solid var(--ln); background: var(--bg); }
	.code figcaption { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 2px 0 12px; border-bottom: 1px solid var(--ln); }
	.code .cp { margin-left: auto; }
	.code pre { margin: 0; padding: 12px 14px; font: 400 12px/1.7 var(--f-mono); color: var(--tx); overflow: auto; }
	.code pre code { background: none; border: 0; padding: 0; }
	.code :global(.k) { color: var(--l405); }
	.code :global(.s) { color: var(--l546); }
	.code :global(.n) { color: var(--l615); }
	.code :global(.c) { color: var(--fnt); }
	.md blockquote { margin: 0 0 16px; padding: 2px 0 2px 16px; border-left: 2px solid var(--l405); }
	.md blockquote .lbl { display: block; margin-bottom: 4px; color: var(--l405); }
	.md blockquote.callout { border: 1px solid var(--ln2); border-left: 2px solid var(--l589); padding: 10px 14px; background: var(--p1); }
	.md blockquote.callout .lbl { color: var(--l589); }
	.md blockquote p:last-child { margin-bottom: 0; }
	.scroller { overflow-x: auto; margin: 4px 0 18px; }
	.md table { font-size: 13.5px; }
	.md td { vertical-align: top; }
	.md hr { border: 0; height: 1px; background: var(--ln); margin: 24px 0; }
	.meta-col { position: sticky; top: 0; padding: 22px 24px; border-left: 1px solid var(--ln); min-height: 100%; display: grid; gap: 18px; align-content: start; }

	@media (max-width: 1023px) {
		.reader.toc, .reader.meta, .reader.toc.meta { grid-template-columns: minmax(0, 1fr); }
		.rail { display: none; }
		.meta-col { position: static; border-left: 0; border-top: 1px solid var(--ln); }
	}
</style>
