<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';
	import type { Resource } from './resource.svelte';
	import Button from './Button.svelte';

	let {
		resource,
		reading,
		onretry,
		skeleton,
		children
	}: {
		resource: Resource<T>;
		/** What is being read, for the loading and error sentences ("the plan"). */
		reading: string;
		onretry: () => void;
		/** Optional skeleton in the region's own geometry; a label shows either way. */
		skeleton?: Snippet;
		children: Snippet<[T]>;
	} = $props();
</script>

<!--
  The daemon's read states (DS §9.22). A failed refresh keeps the last values
  and marks them stale; only a first read with nothing to preserve breaks.
-->
{#if resource.phase === 'loading' || (resource.phase === 'idle' && resource.data === null)}
	<div class="loading" aria-busy="true">
		{#if skeleton}{@render skeleton()}{/if}
		<span class="lbl">Reading {reading}…</span>
	</div>
{:else if resource.phase === 'error' && resource.error}
	<div class="broken" role="alert">
		<span class="line" aria-hidden="true"></span>
		<div>
			<p class="what">Could not read {reading}</p>
			<p class="mono msg">{resource.error.message}</p>
			{#if resource.error.kind === 'unreachable'}
				<p class="hint">Start the daemon with <code>uv run herdsman serve</code> in the project root, then read again.</p>
			{/if}
			<div class="btnrow"><Button icon="rotate-ccw" small onclick={onretry}>Read again</Button></div>
		</div>
	</div>
{:else if resource.data !== null}
	{#if resource.stale}
		<p class="async-strip" role="status">
			<span class="state" data-tone="waiting">Stale</span>
			<span class="mono muted">last confirmed {resource.loadedAt ? resource.loadedAt.toLocaleTimeString() : '—'}</span>
			<Button icon="rotate-ccw" small onclick={onretry}>Read again</Button>
		</p>
	{/if}
	{@render children(resource.data)}
{/if}

<style>
	.loading { padding: 24px; display: grid; gap: 16px; align-content: start; }
	.broken { display: grid; grid-template-columns: 2px minmax(0, 1fr); gap: 16px; padding: 28px 24px; max-width: 72ch; }
	.line { height: 56px; background: linear-gradient(var(--l656) 0 20px, transparent 20px 34px, var(--l656) 34px); }
	.what { font: 500 19px/1.15 var(--f-label); letter-spacing: 0.03em; text-transform: uppercase; color: var(--l656); }
	.msg { color: var(--tx2); margin: 6px 0; overflow-wrap: anywhere; }
	.hint code { color: var(--tx); }
</style>
