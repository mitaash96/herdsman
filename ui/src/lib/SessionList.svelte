<script lang="ts">
	import type { AgentSession } from './daemon';

	let { sessions, label }: { sessions: AgentSession[]; label: string } = $props();

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
				<span class="quiet">{session.agent} {session.kind === 'path' ? 'session file' : 'session'}</span>
				<code>{session.value}</code>
				<button class="act" type="button" onclick={() => void copy(session.value)} aria-label="Copy {session.kind === 'path' ? 'path' : 'id'} {session.value}">
					{copied === session.value ? 'Copied' : 'Copy'}
				</button>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.sessions {
		list-style: none;
		margin: 0.5rem 0 0;
		padding: 0;
		display: grid;
		gap: 0.25rem;
	}
	.sessions li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem;
		font-size: 0.8125rem;
	}
	.quiet {
		color: var(--ink-2);
	}
	code {
		overflow-wrap: anywhere;
	}
</style>
