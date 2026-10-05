<script lang="ts">
	import Icon from './Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import type { IconName } from './icons';

	/**
	 * An icon-only action (DS §9.7): always named, always tipped. A disabled
	 * button keeps a focusable proxy so its reason stays reachable.
	 */
	let {
		icon,
		label,
		shortcut,
		reason,
		danger = false,
		small = false,
		disabled = false,
		pressed,
		onclick
	}: {
		icon: IconName;
		label: string;
		shortcut?: string;
		/** Why the action is unavailable; shown as the tooltip when disabled. */
		reason?: string;
		danger?: boolean;
		small?: boolean;
		disabled?: boolean;
		pressed?: boolean;
		onclick?: (event: MouseEvent) => void;
	} = $props();

	const tip = $derived(disabled && reason ? reason : shortcut ? `${label} ( ${shortcut} )` : label);
</script>

<Tooltip description={tip} {disabled} {label}>
	{#snippet children(descriptionId)}
		<button
			type="button"
			class="ib"
			class:sm={small}
			class:danger
			aria-label={label}
			aria-describedby={tip !== label ? descriptionId : undefined}
			aria-pressed={pressed}
			{disabled}
			{onclick}
		><Icon name={icon} size={small ? 14 : 16} /></button>
	{/snippet}
</Tooltip>
