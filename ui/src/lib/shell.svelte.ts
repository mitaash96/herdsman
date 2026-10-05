/**
 * What a view hands the shell: its titleblock actions (DS §9.2). A view sets
 * `titleblock.actions` to a snippet on mount and clears it on destroy.
 */
import type { Snippet } from 'svelte';

class Titleblock {
	actions = $state<Snippet | null>(null);
}

export const titleblock = new Titleblock();

/** Register a view's titleblock actions for its lifetime. Call inside a component. */
export function useTitleActions(get: () => Snippet | null) {
	$effect(() => {
		titleblock.actions = get();
		return () => {
			titleblock.actions = null;
		};
	});
}
