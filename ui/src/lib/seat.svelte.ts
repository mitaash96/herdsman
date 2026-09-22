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

export function isTop(entry: Seat): boolean {
	return stack.filter((seat) => seat.open).at(-1) === entry;
}

export function sync(): void {
	if (typeof document === 'undefined') return;
	const root = document.documentElement;
	const top = stack.filter((seat) => seat.open).at(-1) ?? null;
	if (!top) {
		root.removeAttribute('data-seat');
		root.removeAttribute('data-seat-cover');
		root.style.removeProperty('--seat-w');
		return;
	}
	root.setAttribute('data-seat', top.width);
	root.style.setProperty(
		'--seat-w',
		top.width === 'docked' && top.el
			? `${Math.round(top.el.getBoundingClientRect().width)}px`
			: '0px'
	);
	/* Covering the field is the top seat's max, or any seat at all where there
	   is no room beside it. The shell hides the sheet; the seat never dims it. */
	if (top.width === 'max' || viewport.narrow) root.setAttribute('data-seat-cover', '');
	else root.removeAttribute('data-seat-cover');
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
