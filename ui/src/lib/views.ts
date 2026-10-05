/**
 * The views, for the shell's sidebar, the Locate index and the `g` chords.
 * Map is retired (redesign 2026-10); System is a dev-only style guide.
 */
import type { IconName } from './icons';

export interface View {
	id: 'run' | 'home' | 'library' | 'kitchen' | 'system';
	href: string;
	name: string;
	purpose: string;
	icon: IconName;
	/** Present only in dev builds. */
	dev?: boolean;
}

const ALL: readonly View[] = [
	{ id: 'home', href: '/home', name: 'Home', purpose: 'Understand the fleet', icon: 'layout-grid' },
	{ id: 'run', href: '/run', name: 'Run', purpose: 'Supervise one plan', icon: 'workflow' },
	{ id: 'library', href: '/library', name: 'Library', purpose: 'Inspect reusable assets', icon: 'library-big' },
	{ id: 'kitchen', href: '/kitchen', name: 'Kitchen', purpose: 'Configure the local environment', icon: 'chef-hat' },
	{ id: 'system', href: '/system', name: 'System', purpose: 'The living style guide', icon: 'layers', dev: true }
];

const dev = (() => {
	try {
		return Boolean(import.meta.env?.DEV);
	} catch {
		return false;
	}
})();

export const VIEWS: readonly View[] = ALL.filter((v) => !v.dev || dev);

export const viewFor = (pathname: string): View | undefined =>
	VIEWS.find((v) => pathname === v.href || pathname.startsWith(`${v.href}/`));
