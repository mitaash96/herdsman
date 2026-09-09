/**
 * The four views, and the exact substrate each one waits on.
 *
 * This is the shell's single source for navigation and for the unavailable
 * presentation: a gated view is a member seated in the structure that carries
 * no load yet. Gates are quoted from `notes/ui-views.md`; a later unit clears
 * its own entry here when its substrate lands, and nothing else changes.
 */
export interface Gate {
	/** The substrate that must land first, in the project's own words. */
	needs: string;
	/** The build unit that owns this view's first slice. */
	unit: string;
	/** Anything else genuinely blocking, beyond the sprint gate. */
	also?: string;
}

export interface View {
	id: 'run' | 'home' | 'library' | 'kitchen';
	href: string;
	name: string;
	purpose: string;
	/** `null` once the substrate has landed. */
	gate: Gate | null;
}

export const VIEWS: readonly View[] = [
	{
		id: 'run',
		href: '/run',
		name: 'Run',
		purpose: 'Supervise one plan',
		gate: null
	},
	{
		id: 'home',
		href: '/home',
		name: 'Home',
		purpose: 'Understand the fleet',
		gate: {
			needs: 'Sprint 10',
			unit: 'H1 — fleet overview',
			also: 'The daemon exposes no GET /plans, so plans cannot be enumerated yet.'
		}
	},
	{
		id: 'library',
		href: '/library',
		name: 'Library',
		purpose: 'Inspect reusable assets',
		gate: { needs: 'Sprint 9', unit: 'L1 — asset browser and preview' }
	},
	{
		id: 'kitchen',
		href: '/kitchen',
		name: 'Kitchen',
		purpose: 'Configure the local environment',
		gate: { needs: 'Sprint 8', unit: 'K1 — harness discovery and readiness' }
	}
];

export const viewFor = (pathname: string): View | undefined =>
	VIEWS.find((v) => pathname === v.href || pathname.startsWith(`${v.href}/`));
