<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';
	import type { Resource } from './resource.svelte';

	let {
		resource,
		reading,
		onretry,
		children
	}: {
		resource: Resource<T>;
		/** What is being read, for the loading and error sentences. */
		reading: string;
		onretry: () => void;
		children: Snippet<[T]>;
	} = $props();
</script>

<!--
  The daemon's read states in the structure's own vocabulary. A failed refresh
  keeps the last known values on screen and marks them stale; only a first read
  with nothing to preserve shows the broken member.
-->
{#if resource.phase === 'loading'}
	<p class="state member" data-state="balanced" aria-busy="true">
		<span class="bar"></span>
		<span class="label">Reading {reading}</span>
	</p>
{:else if resource.phase === 'error' && resource.error}
	<div class="state member" data-state="failed" role="alert">
		<svg class="break" viewBox="0 0 96 16" aria-hidden="true">
			<path d="M0 8h34" stroke="currentColor" stroke-width="1.25" fill="none" />
			<path d="M62 8h34" stroke="currentColor" stroke-width="1.25" fill="none" />
			<path d="M36 3l4 10M42 3l4 10M50 3l4 10M56 3l4 10" stroke="currentColor"
				stroke-width="1" fill="none" opacity="0.7" />
		</svg>
		<p class="broke-title">The load path is broken</p>
		<p class="broke-msg">{resource.error.message}</p>
		{#if resource.error.kind === 'unreachable'}
			<p class="prose hint">
				Start it with <code>herdsman serve</code> in the project root, then read again.
			</p>
		{/if}
		<button class="plate" type="button" onclick={onretry}>Read again</button>
	</div>
{:else if resource.data !== null}
	{#if resource.stale}
		<p class="stale member plate" data-state="slack" role="status">
			<span class="label">Stale</span>
			<span>
				These values were last confirmed{resource.loadedAt
					? ` at ${resource.loadedAt.toLocaleTimeString()}`
					: ''}. The daemon has stopped answering, so they are preserved, not current.
			</span>
			<button class="plate" type="button" onclick={onretry}>Read again</button>
		</p>
	{/if}
	{@render children(resource.data)}
{/if}

<style>
	.state {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.5rem;
		color: var(--member-ink);
		margin: 0;
	}
	.bar {
		display: block;
		width: 8rem;
		height: 1.25px;
		background: currentColor;
		transform-origin: left center;
		animation: take-up-load 1.1s cubic-bezier(0.16, 1, 0.3, 1) infinite;
	}
	.break {
		width: 6rem;
		height: 1rem;
	}
	.broke-title {
		margin: 0;
		font-weight: 500;
		color: var(--ink);
	}
	.broke-msg {
		margin: 0;
		color: var(--ink-2);
	}
	.hint {
		margin: 0;
	}
	code {
		background: var(--plate);
		border: 1px solid var(--rule);
		padding: 0.05em 0.4em;
	}
	.stale {
		--cut: 10px;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem 0.75rem;
		margin: 0 0 1.5rem;
		padding: 0.625rem 0.875rem;
		background: var(--plate);
		border: 1px solid var(--rule);
		color: var(--ink-2);
	}
	.stale .label {
		color: var(--ink-2);
	}
	button {
		--cut: 9px;
		font: inherit;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink);
		background: transparent;
		border: 1px solid var(--rule-strong);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}
	button:hover {
		border-color: var(--red);
		color: var(--red);
	}
</style>
