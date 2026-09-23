/*
  The seat stack — module state for the one right-hand seat.

  Two occupants can be open at once (the plan gate under the member drawer),
  so the stack, not a component, decides which seat is on top, what the shell
  needs to know about it, and how the viewport is watched. Everything that
  writes to `<html>` funnels through `sync`, and every seat's effect calls it:
  the write is derived from the stack, so N seats writing the same values is
  idempotent rather than racy.
*/
export type SeatWidth = 'docked' | 'wide' | 'max';

/** Text size: four steps, applied as `zoom` on the seat body — components in
    this world size in rem, so a font-size on a parent would not reach them. */
export const TEXT_STEPS = [0.9, 1, 1.125, 1.25] as const;
const TEXT_KEY = 'herdsman-seat-text';

type Seat = { open: boolean; width: SeatWidth; el: HTMLElement | null };

/** Open seats, first = lowest. Plain structure, membership changes are
    untracked pushes; `viewport.tick` is the signal seats re-read. */
const stack: Seat[] = [];
export const viewport = $state({ narrow: false, tick: 0 });
export const text = $state({ step: 1 });
let widthObserver: ResizeObserver | null = null;
let observedSeat: HTMLElement | null = null;
let widthTransitioning = false;

export function isTop(entry: Seat): boolean {
	return stack.filter((seat) => seat.open).at(-1) === entry;
}

export function sync(): void {
	if (typeof document === 'undefined') return;
	const root = document.documentElement;
	const top = stack.filter((seat) => seat.open).at(-1) ?? null;
	if (!top) {
		widthTransitioning = false;
		observedSeat = null;
		widthObserver?.disconnect();
		root.removeAttribute('data-seat');
		root.removeAttribute('data-seat-cover');
		root.style.removeProperty('--seat-w');
		return;
	}
	root.setAttribute('data-seat', top.width);
	/* The component ref can still be null on the first open flush; the top
	   seat's contract id is present after that flush. */
	const measuredSeat = top.el ?? root.querySelector<HTMLElement>('#seat');
	if (!widthTransitioning && measuredSeat !== observedSeat) {
		widthObserver?.disconnect();
		observedSeat = measuredSeat;
		if (measuredSeat && typeof ResizeObserver !== 'undefined') {
			widthObserver ??= new ResizeObserver(() => sync());
			widthObserver.observe(measuredSeat);
		}
	}
	if (!widthTransitioning) publishMeasuredWidth(root, measuredSeat);
	/* Covering the field is the top seat's max, or any seat at all where there
	   is no room beside it. The shell hides the sheet; the seat never dims it. */
	if ((top.width === 'max' && !widthTransitioning) || viewport.narrow) root.setAttribute('data-seat-cover', '');
	else root.removeAttribute('data-seat-cover');
}

function publishMeasuredWidth(root: HTMLElement, seat: HTMLElement | null): void {
	const target = seat ? `${Math.round(seat.getBoundingClientRect().width)}px` : '0px';
	if (root.style.getPropertyValue('--seat-w') !== target) root.style.setProperty('--seat-w', target);
}

/** Publish the destination once; the sheet's grid transition follows that value. */
export function beginWidthTransition(width: SeatWidth): void {
	const root = document.documentElement;
	widthTransitioning = true;
	widthObserver?.disconnect();
	observedSeat = null;
	if (width !== 'max' && !viewport.narrow) root.removeAttribute('data-seat-cover');
}

/** Called after Svelte applies the seat width, before the browser paints. */
export function publishWidthTarget(width: SeatWidth): void {
	const root = document.documentElement;
	const rem = Number.parseFloat(getComputedStyle(root).fontSize) || 16;
	const strutValue = getComputedStyle(root).getPropertyValue('--strut-w').trim();
	const strutSize = Number.parseFloat(strutValue) || 0;
	const strut = strutValue.endsWith('rem')
		? strutSize * (Number.parseFloat(getComputedStyle(root).fontSize) || 16)
		: strutSize;
	const target =
		width === 'docked'
			? Math.min(30 * rem, window.innerWidth)
			: width === 'wide'
				? Math.min(74 * rem, window.innerWidth - strut - 7 * rem)
				: Math.max(0, window.innerWidth - strut);
	root.style.setProperty('--seat-w', viewport.narrow ? '0px' : `${target}px`);
}

/** Called once after the edge arrives (or immediately when motion is reduced). */
export function finishWidthTransition(): void {
	if (!widthTransitioning) return;
	widthTransitioning = false;
	sync();
}

export function mountSeat(entry: Seat): () => void {
	stack.push(entry);
	viewport.tick++;
	sync();
	return () => {
		const at = stack.indexOf(entry);
		if (at >= 0) stack.splice(at, 1);
		viewport.tick++;
		sync();
	};
}

/** The one viewport watcher, wired by whichever seat mounts first. */
export function wireViewport(): void {
	if (typeof window === 'undefined' || wireViewport.done) return;
	wireViewport.done = true;
	const media = window.matchMedia('(max-width: 60rem)');
	const apply = () => {
		viewport.narrow = media.matches;
		viewport.tick++;
	};
	media.addEventListener('change', apply);
	window.addEventListener('resize', () => viewport.tick++);
	apply();
}
wireViewport.done = false;

/** Read once per session; every read and write of the key is guarded, because
    storage can be blocked outright in private mode. */
export function loadText(): void {
	if (typeof window === 'undefined' || loadText.done) return;
	loadText.done = true;
	try {
		const stored = Number(localStorage.getItem(TEXT_KEY));
		if (Number.isInteger(stored) && stored >= 0 && stored < TEXT_STEPS.length) text.step = stored;
	} catch {
		/* no storage, no persistence: the default step stands */
	}
}
loadText.done = false;

export function setText(step: number): void {
	text.step = Math.max(0, Math.min(TEXT_STEPS.length - 1, step));
	try {
		localStorage.setItem(TEXT_KEY, String(text.step));
	} catch {
		/* see loadText */
	}
}
