<script lang="ts">
	import type { AgentSession } from './daemon';
	import IconButton from './IconButton.svelte';

	let { sessions, label }: { sessions: (AgentSession & { version?: number })[]; label: string } = $props();

	let copied = $state<string | null>(null);

	/* A clipboard can be refused (insecure origin, no permission); the value is
	   already on screen as selectable text, so a refusal says so and stops. */
	async function copy(value: string) {
		try {
			await navigator.clipboard.writeText(value);
			copied = value;
		} catch {
			copied = null;
		}
	}
</script>

{#if sessions.length > 0}
	<ul class="sessions" aria-label={label}>
		{#each sessions as session (session.at + session.value)}
			<li>
				<span class="lbl">{session.version === undefined ? '' : `Plan v${session.version} · `}{session.agent} {session.kind === 'path' ? 'session file' : 'session'}</span>
				<code>{session.value}</code>
				<IconButton small icon="copy" label="Copy {session.kind === 'path' ? 'path' : 'id'}" onclick={() => void copy(session.value)} />
				{#if copied === session.value}<span class="state" data-tone="pass" role="status">Copied</span>{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	.sessions { display: grid; gap: 4px; }
	.sessions li { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
	code { overflow-wrap: anywhere; color: var(--tx2); }
</style>
