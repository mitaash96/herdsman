<script lang="ts">
	import { ICONS, type IconName } from './icons';
	let { name, size = 16 }: { name: IconName; size?: number } = $props();
</script>

<!-- Vendored Lucide path data (a trusted constant, never daemon text). -->
<svg class="i" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
	stroke-width="1.5" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true" focusable="false"
>{@html ICONS[name]}</svg>

<style>
	.i { flex: none; display: inline-block; vertical-align: middle; }
</style>
