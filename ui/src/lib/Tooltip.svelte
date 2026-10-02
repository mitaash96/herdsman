<script lang="ts">
	import type { Snippet } from 'svelte';

	let { description, disabled = false, label, children }: {
		description: string;
		disabled?: boolean;
		label?: string;
		children: Snippet<[string | undefined]>;
	} = $props();
	const id = $props.id();
	let trigger = $state<HTMLSpanElement>();
	let note = $state<HTMLSpanElement>();
	let hovered = false;
	let focused = false;

	function position() {
		if (!note?.matches(':popover-open') || !trigger) return;
		const anchor = trigger.getBoundingClientRect();
		const box = note.getBoundingClientRect();
		note.style.left = `${Math.max(8, Math.min(anchor.left, innerWidth - box.width - 8))}px`;
		const below = anchor.bottom;
		note.style.top = `${Math.max(8, Math.min(below + box.height <= innerHeight - 8 ? below : anchor.top - box.height, innerHeight - box.height - 8))}px`;
	}
	function reveal() {
		if (!description || !note) return;
		note.showPopover();
		position();
	}
	function hide() { note?.hidePopover(); }
	function leave() { if (!hovered && !focused) hide(); }
	const events = {
		onpointerenter: () => { hovered = true; reveal(); },
		onpointerleave: () => { hovered = false; leave(); },
		onfocusin: () => { focused = true; reveal(); },
		onfocusout: (event: FocusEvent) => {
			if (!trigger?.contains(event.relatedTarget as Node | null)) { focused = false; leave(); }
		}
	};
	$effect(() => {
		document.addEventListener('scroll', position, true);
		return () => document.removeEventListener('scroll', position, true);
	});
</script>

<svelte:window onresize={position} onkeydown={(event) => { if (event.key === 'Escape') hide(); }} />

{#snippet content()}
	{@render children(description ? id : undefined)}
	<span bind:this={note} id={id} role="tooltip" popover="manual"
		onpointerenter={() => { hovered = true; }}
		onpointerleave={() => { hovered = false; leave(); }}
	>{description}</span>
{/snippet}

{#if disabled && description}
	<!-- Focusable disabled proxy exposes help; the native button remains disabled. -->
	<span class="tooltip-trigger" bind:this={trigger} {...events} role="button"
		tabindex="0" aria-disabled="true" aria-label={label} aria-describedby={id}
	>{@render content()}</span>
{:else}
	<span class="tooltip-trigger" bind:this={trigger} {...events}>{@render content()}</span>
{/if}

<style>
	.tooltip-trigger { display: inline-flex; min-width: 0; max-width: 100%; }
	/* Native top layer escapes clipped plates and scrolling drawer seats. */
	[role='tooltip'] {
		position: fixed;
		inset: auto;
		margin: 0;
		width: max-content;
		max-width: min(38ch, calc(100vw - 16px));
		max-height: calc(100vh - 16px);
		overflow: auto;
		overflow-wrap: anywhere;
		padding: 0.5rem 0.65rem;
		border: 1px solid var(--rule-strong);
		background: var(--plate);
		color: var(--ink-2);
		font: inherit;
		font-size: 0.75rem;
		line-height: 1.6;
		text-transform: none;
		letter-spacing: normal;
	}
</style>
