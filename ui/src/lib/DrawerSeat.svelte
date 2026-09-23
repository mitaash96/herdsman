<script lang="ts">
	/*
	  DrawerSeat — the one right-hand seat.

	  InitiativeDrawer, PlanGate and every margin-index section render inside
	  it; it owns the geometry (three widths, the edge cut, the hairline
	  leading edge), the toolbar, the keys, the text zoom, arrival focus, the
	  polite announcement and the scroll re-pin that keeps the reading anchor
	  when the width changes. The occupants own their content and nothing
	  about the chrome.
	*/
	import { tick, untrack, type Snippet } from 'svelte';
	import {
		TEXT_STEPS,
		isTop,
		loadText,
		mountSeat,
		setText,
		sync,
		text,
		viewport,
		wireViewport,
		type SeatWidth
	} from './seat.svelte';

	let {
		open = false,
		focusOnOpen = false,
		label,
		tag,
		title,
		titleId,
		width = $bindable<SeatWidth>('docked'),
		onclose,
		returnFocus,
		tools,
		strip,
		children
	}: {
		open?: boolean;
		focusOnOpen?: boolean;
		/** The ridden rule-label: `label ——— tag`. */
		label: string;
		tag: string;
		title: string;
		/** The heading's id: the occupant's address-open focus target. */
		titleId: string;
		width?: SeatWidth;
		onclose: () => void;
		/** Where closing prefers to send focus; null falls through to the
		    element recorded when the seat opened. */
		returnFocus?: () => HTMLElement | null;
		/** Extra icon buttons ahead of the seat's own, e.g. locate, focus pane. */
		tools?: Snippet;
		/** Shown only at width `max`, above the header (Run: the member's lane). */
		strip?: Snippet;
		children: Snippet;
	} = $props();

	let asideEl = $state<HTMLElement | null>(null);
	let bodyEl = $state<HTMLElement | null>(null);

	/* Plain on purpose: the stack compares entries by identity, and a plain
	   object keeps that comparison exact. Its field writes are mirrored into
	   `viewport.tick`, which is the reactive signal other seats read. */
	const entry = { open: false, width: 'docked' as SeatWidth, el: null as HTMLElement | null };

	const top = $derived.by(() => {
		void viewport.tick;
		return isTop(entry);
	});

	$effect(() => loadTextAndWire());
	function loadTextAndWire(): void {
		loadText();
		wireViewport();
	}

	$effect(() => untrack(() => mountSeat(entry)));

	/* Mirror this seat into the stack and the shell's root attributes. The
	   guarded tick bump re-runs other seats' effects when top-ness changes,
	   and settles because the second run finds nothing left to bump. */
	$effect(() => {
		if (entry.open !== open) {
			entry.open = open;
			viewport.tick++;
		}
		entry.width = width;
		entry.el = asideEl;
		void viewport.tick;
		sync();
	});

	/* --- focus: record on open, return on close ---------------------------
	   Recorded before any arrival focus can move (this effect runs in the same
	   flush, a beat before the occupant's deferred title focus), so it is the
	   element the operator came from. An occupant-initiated close captures its
	   preferred target first — by close time the page has already cleared the
	   id its return-focus reads. */
	let recorded: HTMLElement | null = null;
	let preferred: HTMLElement | null = null;
	let wasOpen = false;
	let announced = $state('');

	$effect(() => {
		if (open) {
			if (!wasOpen) {
				wasOpen = true;
				recorded =
					document.activeElement instanceof HTMLElement ? document.activeElement : null;
				announced = `Opened ${title}`;
				if (focusOnOpen) void tick().then(() => document.getElementById(titleId)?.focus());
			}
			return;
		}
		if (!wasOpen) return;
		wasOpen = false;
		announced = '';
		const target = preferred ?? returnFocus?.() ?? recorded;
		preferred = null;
		void tick().then(() => focusReturn(target));
	});

	/* Never hand the caret to `<body>`: a live target wins, then the recorded
	   element, then the sheet itself — which is focusable by design. */
	function focusReturn(target: HTMLElement | null): void {
		const live = (candidate: HTMLElement | null | undefined): HTMLElement | null =>
			candidate && candidate.isConnected && candidate !== document.body ? candidate : null;
		(live(target) ?? live(recorded) ?? document.getElementById('field'))?.focus();
	}

	function close(): void {
		preferred = returnFocus?.() ?? null;
		onclose();
	}

	/* --- the reading anchor ------------------------------------------------
	   Ported from the drawer's `setExpanded`: measure the seat's reading
	   anchor against the top of the scroll box, change the width, put it back.
	   The anchor is the section the viewport top is actually inside — the
	   checkpoint reader or the packet inspector — so widening never throws the
	   operator to a different part of the seat. */
	function seatAnchor(body: HTMLElement): HTMLElement | null {
		const base = body.getBoundingClientRect().top;
		let inside: { el: HTMLElement; pos: number } | null = null;
		let below: { el: HTMLElement; pos: number } | null = null;
		for (const el of body.querySelectorAll<HTMLElement>('[data-seat-anchor]')) {
			const item = { el, pos: el.getBoundingClientRect().top - base };
			if (item.pos <= body.scrollTop + 1) {
				if (!inside || item.pos > inside.pos) inside = item;
			} else if (!below || item.pos < below.pos) below = item;
		}
		return (inside ?? below)?.el ?? null;
	}

	async function setWidth(next: SeatWidth): Promise<void> {
		if (!open || next === width) return;
		const body = bodyEl;
		const anchor = body ? seatAnchor(body) : null;
		const before =
			anchor && body ? anchor.getBoundingClientRect().top - body.getBoundingClientRect().top : null;
		width = next;
		await tick();
		if (before === null || !anchor || !bodyEl) return;
		const after = anchor.getBoundingClientRect().top - bodyEl.getBoundingClientRect().top;
		bodyEl.scrollTop += after - before;
	}

	/* --- maximize/restore --------------------------------------------------
	   M returns to the width it was taken from, so maximize never loses the
	   operator's place in the docked/wide axis. */
	let preMax: SeatWidth = 'docked';
	function toggleMax(): void {
		if (width === 'max') void setWidth(preMax === 'max' ? 'docked' : preMax);
		else {
			preMax = width;
			void setWidth('max');
		}
	}
	function toggleWide(): void {
		void setWidth(width === 'wide' ? 'docked' : 'wide');
	}

	/* Keys, live only while focus is inside this seat and not in a field that
	   eats text. The seat must be the top one — an uncovered seat below is
	   visibility-hidden anyway, but the guard keeps the stack's rule explicit. */
	function onkeydown(event: KeyboardEvent): void {
		if (!open || !top || event.metaKey || event.ctrlKey || event.altKey) return;
		const target = event.target;
		if (
			target instanceof HTMLElement &&
			(target.matches('input, textarea, select') || target.isContentEditable)
		)
			return;
		const key = event.key;
		if (key === 'm' || key === 'M') {
			event.preventDefault();
			toggleMax();
		} else if (key === 'w' || key === 'W') {
			event.preventDefault();
			toggleWide();
		} else if (key === '+' || key === '=') {
			event.preventDefault();
			setText(text.step + 1);
		} else if (key === '-') {
			event.preventDefault();
			setText(text.step - 1);
		} else if (key === '0') {
			event.preventDefault();
			setText(1);
		} else if (key === 'l' || key === 'L') {
			const locate = asideEl?.querySelector<HTMLButtonElement>('[aria-label^="Locate in field"]');
			if (locate) { event.preventDefault(); locate.click(); }
		}
	}

	/* Escape is window-level, as it always was: step the top seat down
	   max → wide → docked, then close it. A seat underneath never acts, and an
	   armed decision on the gate claims the key first (capture phase) so a
	   half-made decision is cancelled rather than dismissed through. */
	$effect(() => {
		if (!open) return;
		const handler = (event: KeyboardEvent) => {
			if (event.key !== 'Escape' || !isTop(entry)) return;
			if (width === 'max') void setWidth('wide');
			else if (width === 'wide') void setWidth('docked');
			else close();
		};
		window.addEventListener('keydown', handler);
		return () => window.removeEventListener('keydown', handler);
	});

	const wide = $derived(width !== 'docked');
</script>

<!-- No scrim, no shadow, no entrance: the field stays live beside the seat,
     and the hairline leading edge carries the separation. The bottom-left cut
     is the Edge-Cut Exception — a top-right cut against the browser frame
     would read as a notch rather than a direction. -->
<aside
	class="seat plate"
	class:top
	class:wide
	id={top ? 'seat' : undefined}
	hidden={!open}
	data-width={width}
	aria-labelledby={titleId}
>
	<p class="sr" role="status">{announced}</p>

	{#if width === 'max' && strip}
		<div class="strip">{@render strip()}</div>
	{/if}

	<header>
		<div class="head-rule-row">
			<p class="label rule-label">
				<span>{label}</span><span class="rule"></span><span>{tag}</span>
			</p>
			<div class="tools" role="toolbar" aria-label="Seat controls">
				{@render tools?.()}
				<button
					type="button"
					aria-label="Text smaller (-)"
					title="Text smaller (-)"
					onclick={() => setText(text.step - 1)}
				>
					<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
						<path d="M2 12.5 4.75 4.5 7.5 12.5M2.9 10.2h3.7" />
						<path d="M10 13h4" />
					</svg>
				</button>
				<button
					type="button"
					aria-label="Text larger (+)"
					title="Text larger (+)"
					onclick={() => setText(text.step + 1)}
				>
					<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
						<path d="M1.5 13.5 5.75 1.5 10 13.5M2.6 10.4h6.3" />
						<path d="M12 5.5v3M10.5 7h3" />
					</svg>
				</button>
				{#if !viewport.narrow}
					<button
						type="button"
						aria-label="Reading width (W)"
						title="Reading width (W)"
						aria-pressed={width === 'wide'}
						onclick={toggleWide}
					>
						<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
							<path d="M1.5 8h13M4 5 1 8l3 3M12 5l3 3-3 3" />
						</svg>
					</button>
					<button
						type="button"
						aria-label={width === 'max' ? 'Restore (M)' : 'Maximize (M)'}
						title={width === 'max' ? 'Restore (M)' : 'Maximize (M)'}
						aria-pressed={width === 'max'}
						onclick={toggleMax}
					>
						<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
							{#if width === 'max'}
								<path d="M7 7 3.5 3.5M3.5 6V3.5h2.5M9 7l3.5-3.5M10 3.5h2.5V6M7 9l-3.5 3.5M3.5 10v2.5H6M9 9l3.5 3.5M12.5 10v2.5H10" />
							{:else}
								<path d="M6 3.5H3.5V6M10 3.5h2.5V6M12.5 10v2.5H10M6 12.5H3.5V10" />
							{/if}
						</svg>
					</button>
				{/if}
				<button type="button" aria-label="Close (Escape)" title="Close (Escape)" onclick={close}>
					<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
						<path d="M4 4l8 8M12 4l-8 8" />
					</svg>
				</button>
			</div>
		</div>
		<h2 id={titleId} tabindex="-1">{title}</h2>
	</header>

	<div class="body" bind:this={bodyEl}>
		<!-- The zoom lives on the content, not on the scroll box: components
		     size in rem, and a zoomed flex child would also overflow the seat. -->
		<div class="zoom" style="zoom: {TEXT_STEPS[text.step]}">
			{@render children()}
		</div>
	</div>
</aside>

<style>
	.seat {
		--cut: 12px;
		position: fixed;
		top: 0;
		bottom: 0;
		right: 0;
		/* Above the layout chrome (10) and the field's seats (1); the lower
		   seat in the stack sits under the top one and is hidden, not inert. */
		z-index: 19;
		width: min(30rem, 100%);
		max-width: 100%;
		height: 100dvh;
		display: flex;
		flex-direction: column;
		padding: 0;
		background: var(--plate);
		color: var(--ink);
		border: 1px solid var(--rule);
		font: inherit;
		overflow: hidden;
		border-radius: 0 0 0 var(--cut);
	}
	.seat.top {
		z-index: 20;
	}
	.seat:not(.top) {
		visibility: hidden;
	}
	/* Beats the UA's `[hidden]` rule, which `.seat`'s own display would win. */
	.seat[hidden] {
		display: none;
	}
	/* Overriding the shared chamfer means overriding its fallback in the same
	   breath, or the fallback still cuts both corners. */
	@supports not (corner-shape: bevel) {
		.seat {
			border-radius: 0;
			clip-path: polygon(0 0, 100% 0, 100% 100%, var(--cut) 100%, 0 calc(100% - var(--cut)));
		}
	}
	.seat[data-width='wide'] {
		width: min(74rem, 100%);
	}
	/* Max runs from the strut's member to the right edge: the whole field,
	   with the rail kept. */
	.seat[data-width='max'] {
		left: var(--strut-w, 17rem);
		width: auto;
	}

	/* A visually hidden status line: a click-opened seat says so, because only
	   an address-open may claim the caret. */
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}

	.strip {
		flex: none;
		padding: 0.6rem 1.5rem;
		border-bottom: 1px solid var(--rule);
	}

	header {
		flex: none;
		padding: 1.5rem 1.5rem 1.25rem;
		border-bottom: 1px solid var(--rule);
	}
	.head-rule-row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.head-rule-row .rule-label {
		flex: 1;
		min-width: 0;
		margin: 0;
		white-space: nowrap;
	}
	/* The title is the headline role, not the display role: the drawing owns
	   display type, a panel heading does not. */
	h2 {
		font-family: 'Archivo', ui-sans-serif, system-ui, sans-serif;
		font-variation-settings: 'wdth' 70, 'wght' 620;
		font-weight: 620;
		text-transform: uppercase;
		letter-spacing: -0.01em;
		font-size: 1.5rem;
		line-height: 1.05;
		margin: 0;
		text-wrap: balance;
		min-width: 0;
		overflow-wrap: anywhere;
	}

	/* --- the toolbar: one row of 30px icon buttons, no chips --------------- */
	.rule-label {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin: 0 0 0.75rem;
	}
	.rule-label .rule {
		flex: 1;
		height: 1px;
		background: var(--rule);
		align-self: center;
	}
	.rule-label > span:last-child {
		flex: none;
		max-width: 55%;
		overflow-wrap: anywhere;
		text-align: right;
	}
	.tools {
		display: flex;
		flex: none;
		gap: 0.15rem;
	}
	.tools :global(button) {
		width: 30px;
		height: 30px;
		display: grid;
		place-items: center;
		padding: 0;
		background: transparent;
		border: 0;
		border: 0;
		color: var(--ink);
		cursor: pointer;
	}
	.tools :global(button svg) {
		width: 16px;
		height: 16px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
		stroke-linecap: square;
	}
	.tools :global(button:hover:not(:disabled)) {
		color: var(--red);
	}
	.tools :global(button:disabled) {
		color: var(--ink-2);
		cursor: not-allowed;
	}
	.tools :global(button[aria-pressed='true']) {
		box-shadow: 0 0 0 3px var(--plate), 0 0 0 4px var(--member-line);
	}
	.tools :global(button:focus-visible) {
		outline: 2px solid var(--red);
		outline-offset: 2px;
	}

	.body {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: 0 1.5rem 2.5rem;
	}
	/* A wider seat earns wider margins; the prose measure stays 68ch either
	   way, so this is the plate breathing, not the text sprawling. */
	.wide header {
		padding: 1.5rem 2.25rem 1.25rem;
	}
	.wide .body {
		padding: 0 2.25rem 3rem;
	}
	.wide .strip {
		padding-inline: 2.25rem;
	}

	/* A narrow viewport has no room beside the field: every width renders as
	   the whole viewport, and the width controls hide themselves — there is
	   nothing to choose between. */
	@media (max-width: 60rem) {
		.seat,
		.seat[data-width='wide'],
		.seat[data-width='max'] {
			left: auto;
			width: 100%;
		}
		header {
			padding: 1.25rem 1rem 1rem;
		}
		.body {
			padding: 0 1rem 2rem;
		}
		.strip {
			padding-inline: 1rem;
		}
	}
</style>
