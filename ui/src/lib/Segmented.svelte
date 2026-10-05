<script lang="ts" generics="T extends string">
	/** A bordered group of pressed/unpressed text buttons (DS §9.12). */
	let { options, value, label, onchange }: {
		options: readonly { id: T; label: string }[];
		value: T;
		label: string;
		onchange: (id: T) => void;
	} = $props();
</script>

<div class="seg" role="group" aria-label={label}>
	{#each options as o (o.id)}
		<button type="button" aria-pressed={o.id === value} onclick={() => onchange(o.id)}>{o.label}</button>
	{/each}
</div>
