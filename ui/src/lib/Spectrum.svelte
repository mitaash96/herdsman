<script lang="ts">
	/** One line per member, ordered settled → running → needs → failed → waiting (DS §9.4). Always aria-hidden. */
	let {
		settled = 0,
		running = 0,
		needs = 0,
		failed = 0,
		waiting = 0,
		tall = false
	}: { settled?: number; running?: number; needs?: number; failed?: number; waiting?: number; tall?: boolean } = $props();
	const cap = (n: number) => Math.max(0, Math.min(n, 60));
</script>

<span class="spec" class:tall aria-hidden="true">
	{#each { length: cap(settled) } as _, i (i)}<i style:--c="var(--tx2)"></i>{/each}
	{#each { length: cap(running) } as _, i (i)}<i style:--c="var(--tx)"></i>{/each}
	{#each { length: cap(needs) } as _, i (i)}<i class="nd" style:--c="var(--l589)"></i>{/each}
	{#each { length: cap(failed) } as _, i (i)}<i style:--c="var(--l656)"></i>{/each}
	{#each { length: cap(waiting) } as _, i (i)}<i class="w"></i>{/each}
</span>

<style>
	.tall { height: 22px; }
	.tall :global(i.w) { height: 6px; }
</style>
