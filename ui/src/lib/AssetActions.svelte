<script lang="ts">
	import { untrack } from 'svelte';
	import { daemon, DaemonError, type AssetKind, type AssetSummary } from '$lib/daemon';
	import { KIND_WORD } from '$lib/shelf';

	let { asset = null, create = false, incoming = 0, connected = false, changed = '', autoEdit = false,
		onwrite, onreread, onsuccess, outcome = '', onclear = () => {}, onclose = () => {} } = $props<{
		asset?: AssetSummary | null; create?: boolean; incoming?: number; connected?: boolean;
		changed?: string; autoEdit?: boolean;
		onwrite: (ref: string, edit?: boolean) => Promise<void>;
		onreread: () => Promise<void>;
		onsuccess: (message: string) => void; outcome?: string; onclear?: () => void; onclose?: () => void;
	}>();
	let panel = $state<'edit' | 'copy' | 'rename' | 'archive' | 'new' | null>(untrack(() => create ? 'new' : null));
	let pending = $state(false);
	let error = $state('');
	let conflict = $state(false);
	let name = $state('');
	let title = $state('');
	let kind = $state<AssetKind>('role');
	let path = $state('');
	let clipboard = $state('');
	const leaf = $derived(asset?.kind === 'memory-leaf');
	const displayPath = $derived(path.includes('/.herdsman/') ? path.slice(path.lastIndexOf('/.herdsman/') + 1) : path);
	const kinds: AssetKind[] = ['role', 'contract', 'skill', 'agent', 'checkpoint-template'];
	let identity: string | undefined;
	let pathElement = $state<HTMLElement>();
	let commandElement = $state<HTMLElement>();
	$effect(() => {
		const ref = asset?.ref;
		if (identity === ref) return;
		identity = ref;
		untrack(() => {
			panel = null; error = ''; clipboard = ''; path = ''; onclear();
			if (autoEdit && asset) void edit();
		});
	});

	function arm(next: typeof panel): void {
		if (pending) return;
		error = ''; conflict = false; clipboard = ''; onclear(); panel = next;
		name = next === 'copy' ? `${asset?.name}-copy` : next === 'rename' ? asset?.name ?? '' : '';
	}
	function dismiss(): void {
		panel = null; clipboard = ''; onclear(); onclose();
	}
	async function write(action: () => Promise<void>): Promise<void> {
		if (pending) return;
		pending = true; error = ''; conflict = false; onclear();
		try { await action(); }
		catch (cause) {
			error = cause instanceof Error ? cause.message : 'The write failed.';
			conflict = cause instanceof DaemonError && cause.status === 409;
		} finally { pending = false; }
	}
	async function edit(): Promise<void> {
		if (!asset || pending) return;
		panel = 'edit'; clipboard = '';
		const ref = asset.ref;
		await write(async () => {
			const answer = await daemon.checkoutAsset(ref);
			path = answer.path;
			await onreread();
			if (!autoEdit) onsuccess('Ready to edit in terminal.');
		});
	}
	async function submit(): Promise<void> {
		const mode = panel;
		await write(async () => {
			if (mode === 'new') {
				const result = await daemon.createAsset(kind, name, title);
				await onwrite(result.ref, true);
				panel = null;
				onsuccess(`Created ${result.ref}.`);
			} else if (asset && (mode === 'copy' || mode === 'rename')) {
				const result = await (mode === 'copy' ? daemon.copyAsset(asset.ref, name) : daemon.renameAsset(asset.ref, name));
				await onwrite(result.ref);
				panel = null;
				onsuccess(`${mode === 'copy' ? 'Copied to' : 'Renamed to'} ${result.ref}.`);
			} else if (asset && mode === 'archive') {
				const result = await daemon.archiveAsset(asset.ref);
				await onreread(); panel = null;
				onsuccess(`${leaf ? 'Retired' : 'Archived'} ${result.ref}.`);
			}
		});
	}
	async function restore(): Promise<void> {
		if (!asset) return;
		const ref = asset.ref;
		await write(async () => {
			await daemon.unarchiveAsset(ref); await onreread();
			onsuccess(`Restored ${ref}.`);
		});
	}
	async function copyText(text: string, element: HTMLElement | undefined): Promise<void> {
		try { await navigator.clipboard.writeText(text); clipboard = 'Copied'; }
		catch {
			if (!element) { clipboard = 'Copy failed — copy the code line with your keyboard.'; return; }
			const range = document.createRange();
			range.selectNodeContents(element);
			const selection = window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range);
			clipboard = 'Copy failed — text selected. Copy it with your keyboard.';
		}
	}
</script>

<div class="asset-actions" aria-busy={pending}>
	<div class="actions">
		{#if !create && asset}
			<button class="plate ghost" disabled={pending} onclick={edit}>{pending && panel === 'edit' ? 'Checking out…' : asset.origin === 'bundled' ? 'Copy to project & edit' : 'Edit in terminal'}</button>
			{#if !leaf}<button class="plate ghost" disabled={pending} onclick={() => arm('copy')}>Copy…</button>{/if}
			{#if !leaf && asset.origin !== 'bundled'}<button class="plate ghost" disabled={pending} onclick={() => arm('rename')}>Rename…</button>{/if}
			<button class="plate ghost" disabled={pending} onclick={() => asset?.status === 'retired' ? restore() : arm('archive')}>{asset.status === 'retired' ? (pending ? 'Restoring…' : 'Restore') : leaf ? 'Retire' : 'Archive'}</button>
		{/if}
	</div>
	{#if leaf}
		<p class="gloss">Memory leaves are addressed by their id; retire the leaf and author a new one.</p>
	{:else if asset?.origin === 'bundled'}
		<p class="gloss">Bundled assets are read-only; this creates a project override that shadows it. Rename is unavailable; copy under a new name.</p>
	{:else if asset?.shadows_bundled}
		<p class="gloss">This project override shadows the bundled original. Archiving retires the override; it continues to shadow the original.</p>
	{/if}
	{#if panel}
		<div class="panel plate" class:new-panel={create}>
			{#if !create}<button class="close" aria-label="Close asset action" disabled={pending} onclick={dismiss}>×</button>{/if}
			{#if panel === 'edit'}
				{#if path}
					<div class="code-line"><p class="rule-label label"><span>File</span><span class="rule"></span><button class="plate ghost" onclick={() => copyText(path, pathElement)}>Copy</button></p><code title={path} bind:this={pathElement} oncopy={(event) => { event.clipboardData?.setData('text/plain', path); event.preventDefault(); }}>{displayPath}</code></div>
					{#if leaf}<p class="gloss">Evidence is not edited here.</p>{/if}
					<div class="code-line"><p class="rule-label label"><span>Command</span><span class="rule"></span><button class="plate ghost" onclick={() => copyText(`herdsman library edit ${asset?.ref}`, commandElement)}>Copy</button></p><code bind:this={commandElement}>herdsman library edit {asset?.ref}</code></div>
					<p>Save in your editor — this page follows the file.</p>
					<p class="gloss">{changed ? `Saved ${changed} · re-read` : connected ? 'Watching for your save' : 'Live updates disconnected — return to this window to re-read.'}</p>
				{/if}
			{:else}
				<form class:new-form={create} onsubmit={(event) => { event.preventDefault(); void submit(); }}>
					{#if panel === 'archive'}
						{#if leaf}<p>Retired leaves are no longer offered to agents. The file stays in .herdsman/memory and can be restored.</p>{:else}<p>Leaves the active shelf. Plans already approved keep their frozen copy; assets that reference it will report an archived reference.</p>
						<p class="gloss">Referenced by {incoming} {incoming === 1 ? 'asset' : 'assets'}.</p>{/if}
					{:else}
						{#if panel === 'new'}
							<label>Kind<span class="pick"><select class="plate" bind:value={kind} disabled={pending}>{#each kinds as option}<option value={option}>{KIND_WORD[option]}</option>{/each}</select></span></label>
						{/if}
						<label>{panel === 'new' ? 'Name' : 'New name'}<input class="plate" id={create ? 'asset-new-name' : 'asset-action-name'} bind:value={name} disabled={pending} required /></label>
						{#if panel === 'new'}<label>Title<input class="plate" bind:value={title} placeholder="optional" disabled={pending} /></label>{/if}
					{/if}
					{#if error}<p role="alert">{error}</p>{/if}
					{#if conflict}<button type="button" class="plate ghost" disabled={pending} onclick={() => void onreread()}>Re-read</button>{/if}
					<div class="confirmrow"><button class="plate ghost" type="submit" disabled={pending}>{pending ? 'Writing…' : panel === 'archive' ? (leaf ? 'Confirm retire' : 'Confirm archive') : panel === 'new' ? 'Create asset' : panel === 'copy' ? 'Copy asset' : 'Rename asset'}</button><button class="plate ghost" type="button" disabled={pending} onclick={dismiss}>Cancel</button></div>
				</form>
			{/if}
		</div>
	{/if}
	{#if error && (panel === 'edit' || panel === null)}<p role="alert">{error}</p>{/if}
	<p class="gloss" aria-live="polite">{outcome}{outcome && clipboard ? ' · ' : ''}{clipboard}</p>
</div>


<style>
	.asset-actions { margin-top: 0.75rem; min-width: 0; }
	.actions .ghost { font-size: 0.6875rem; letter-spacing: 0.04em; padding: 0.4rem 0.5rem; }
	.actions, .confirmrow { display: flex; flex-wrap: wrap; gap: 0.5rem; }
	.ghost { font: inherit; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink); background: transparent; border: 1px solid var(--rule-strong); padding: 0.4rem 0.7rem; cursor: pointer; }
	.ghost:hover:not(:disabled) { color: var(--red); border-color: var(--red); }
	.ghost:disabled { color: var(--ink-2); border-color: var(--rule); cursor: not-allowed; }
	.gloss { color: var(--ink-2); font-size: 0.8125rem; max-width: 68ch; margin: 0.6rem 0; }
	.gloss:empty { display: none; }
	.panel { position: relative; border: 1px solid var(--rule-strong); padding: 2.25rem 1rem 1rem; margin-top: 0.75rem; background: var(--plate); }
	.panel p { max-width: 68ch; }
	.close { position: absolute; right: 0.6rem; top: 0.3rem; font: inherit; background: transparent; color: var(--ink); border: 0; padding: 0.3rem; cursor: pointer; }
	.code-line { margin: 0.5rem 0 1rem; min-width: 0; }
	.rule-label { display: flex; align-items: center; gap: 0.5rem; margin: 0 0 0.4rem; }
	.rule { flex: 1; height: 1px; background: var(--rule); }
	code { display: block; box-sizing: border-box; width: 100%; min-width: 0; overflow-x: auto; white-space: pre; padding: 0.4rem; border: 1px solid var(--rule); }
	label { display: flex; flex-direction: column; gap: 0.3rem; margin: 0.7rem 0; }
	input, select { width: 100%; box-sizing: border-box; font: inherit; background: var(--plate); color: var(--ink); border: 1px solid var(--rule-strong); padding: 0.45rem 0.7rem; }
	.pick { position: relative; display: flex; }
	.pick::after { content: ''; position: absolute; right: 0.85rem; top: calc(50% - 0.35em); width: 0.4em; height: 0.4em; border-right: 1px solid var(--ink-2); border-bottom: 1px solid var(--ink-2); transform: rotate(45deg); pointer-events: none; }
	select { appearance: none; padding-right: 2.25rem; }
	.new-panel { padding: 0.75rem 1rem; margin: 0 0 1.25rem; }
	.new-form { display: flex; flex-wrap: wrap; align-items: end; gap: 0.75rem; }
	.new-form label { flex: 1 1 7rem; min-width: 0; margin: 0; }
	.new-form .confirmrow { border: 0; padding: 0; margin: 0; }
	.new-form [role="alert"] { flex-basis: 100%; margin: 0; }
	@media (max-width: 40rem) { .new-form label { flex-basis: 100%; } }
	.confirmrow { border-top: 1px solid var(--rule); padding-top: 0.9rem; margin-top: 1rem; }
</style>
