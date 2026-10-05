<script lang="ts">
	import Icon from './Icon.svelte';
	import type { IconName } from './icons';
	import type { Snippet } from 'svelte';

	/** A labelled decision (DS §9.6). Every button carries an icon. */
	let {
		icon,
		kind = 'secondary',
		small = false,
		busy = false,
		disabled = false,
		type = 'button',
		href,
		title,
		onclick,
		children
	}: {
		icon: IconName;
		kind?: 'primary' | 'secondary' | 'danger';
		small?: boolean;
		busy?: boolean;
		disabled?: boolean;
		type?: 'button' | 'submit';
		href?: string;
		title?: string;
		onclick?: (event: MouseEvent) => void;
		children: Snippet;
	} = $props();
</script>

{#if href && !disabled}
	<a class="btn" class:pri={kind === 'primary'} class:danger={kind === 'danger'} class:sm={small} {href} {title}>
		<Icon name={icon} size={small ? 13 : 15} />{@render children()}
	</a>
{:else}
	<button
		{type}
		class="btn"
		class:pri={kind === 'primary'}
		class:danger={kind === 'danger'}
		class:sm={small}
		disabled={disabled || busy}
		aria-busy={busy || undefined}
		{title}
		{onclick}
	>
		{#if busy}<span class="busy" aria-hidden="true"></span>{:else}<Icon name={icon} size={small ? 13 : 15} />{/if}
		{@render children()}
	</button>
{/if}

<style>
	.busy {
		width: 13px; height: 8px; display: inline-grid; place-items: center; flex: none;
		background: linear-gradient(currentColor, currentColor) center / 1px 100% no-repeat;
		transform-origin: 50% 100%;
		animation: land var(--t-land) var(--land) infinite alternate;
	}
</style>
