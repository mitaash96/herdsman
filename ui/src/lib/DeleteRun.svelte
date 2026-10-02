<script lang="ts">
	/* One trashcan: erases a run from the store for good (events and all), after a confirm. */
	import { daemon } from '$lib/daemon';

	let { planId, ondeleted, large = false }: { planId: string; ondeleted: () => void; large?: boolean } = $props();
	let busy = $state(false);

	async function erase() {
		if (busy || !confirm(`Delete ${planId} permanently? Its whole record is erased and cannot be recovered.`)) return;
		busy = true;
		try {
			await daemon.deletePlan(planId);
			ondeleted();
		} catch (cause) {
			alert(cause instanceof Error ? cause.message : 'The delete failed.');
		} finally {
			busy = false;
		}
	}
</script>

<button class="trash" class:large type="button" aria-label="Delete {planId}" title="Delete run" disabled={busy} onclick={erase}>
	<svg viewBox="0 0 16 16" width={large ? 18 : 12} height={large ? 18 : 12} fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
		<path d="M2.5 4h11M6 4V2.5h4V4M4 4l.6 9.5h6.8L12 4M6.8 6.5v5M9.2 6.5v5" />
	</svg>
</button>

<style>
	.trash {
		display: inline-flex;
		color: var(--ink-2);
		background: none;
		border: 0;
		padding: 0;
		cursor: pointer;
		vertical-align: middle;
	}
	.trash.large {
		padding: 4px;
	}
	.trash:hover {
		color: var(--red);
	}
</style>
