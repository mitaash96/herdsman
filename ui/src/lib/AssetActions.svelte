<script lang="ts">
	import { untrack } from 'svelte';
	import Button from './Button.svelte';
	import IconButton from './IconButton.svelte';
	import { daemon, DaemonError, type AssetKind, type AssetSummary } from '$lib/daemon';
	import { KIND_WORD } from '$lib/shelf';

	/*
	  One asset's write flows (DS §9.18 icon bar). Edit, Copy as new, Rename and
	  Archive are the writes; Copy ref and Versions are reads. Name-a-copy, rename,
	  archive confirm and New asset open in place as a form below the head - no
	  modal - and every outcome shows beside the controls as a state mark.
	*/
	let { asset = null, create = false, incoming = 0, connected = false, changed = '', autoEdit = false,
		label = '', heading = '', onversions,
		onwrite, onreread, onsuccess, outcome = '', onclear = () => {}, onclose = () => {} } = $props<{
		asset?: AssetSummary | null; create?: boolean; incoming?: number; connected?: boolean;
		changed?: string; autoEdit?: boolean;
		/** Rule label and section title of the reader head this bar belongs to. */
		label?: string; heading?: string; onversions?: () => void;
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
	const bundled = $derived(asset?.origin === 'bundled');
	const actionHelp = $derived(leaf
		? 'A leaf keeps its id: no copy or rename. Retire it when it stops being true.'
		: bundled
		? 'Bundled assets are read-only; this creates a project override that shadows it. Rename is unavailable; copy under a new name.'
		: asset?.shadows_bundled
		? 'This project override shadows the bundled original. Archiving retires the override; it continues to shadow the original.'
		: '');
	const editLabel = $derived(bundled ? 'Edit as project copy' : 'Edit in terminal');
	const retired = $derived(asset?.status === 'retired');
	const archiveLabel = $derived(retired ? 'Restore' : leaf ? 'Retire' : 'Archive');
	const displayPath = $derived(path.includes('/.herdsman/') ? path.slice(path.lastIndexOf('/.herdsman/') + 1) : path);
	const kinds: AssetKind[] = ['role', 'contract', 'skill', 'agent', 'checkpoint-template'];
	const status = $derived(pending ? 'Writing…' : [outcome, clipboard].filter(Boolean).join(' · '));
	let identity: string | undefined;
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
		panel = null; clipboard = ''; error = ''; onclear(); onclose();
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
			if (!autoEdit) onsuccess('Ready to edit in terminal');
		});
	}
	async function submit(): Promise<void> {
		const mode = panel;
		await write(async () => {
			if (mode === 'new') {
				const result = await daemon.createAsset(kind, name, title);
				await onwrite(result.ref, true);
				panel = null;
				onsuccess(`Created ${result.ref}`);
			} else if (asset && (mode === 'copy' || mode === 'rename')) {
				const result = await (mode === 'copy' ? daemon.copyAsset(asset.ref, name) : daemon.renameAsset(asset.ref, name));
				await onwrite(result.ref);
				panel = null;
				onsuccess(`${mode === 'copy' ? 'Copied to' : 'Renamed to'} ${result.ref}`);
			} else if (asset && mode === 'archive') {
				const result = await daemon.archiveAsset(asset.ref);
				await onreread(); panel = null;
				onsuccess(`${leaf ? 'Retired' : 'Archived'} ${result.ref}`);
			}
		});
	}
	async function restore(): Promise<void> {
		if (!asset) return;
		const ref = asset.ref;
		await write(async () => {
			await daemon.unarchiveAsset(ref); await onreread();
			onsuccess(`Restored ${ref}`);
		});
	}
	async function copyText(text: string, what: string): Promise<void> {
		onclear();
		try { await navigator.clipboard.writeText(text); clipboard = `${what} copied`; }
		catch { clipboard = 'Copy failed — select it with your keyboard'; }
	}
	const submitLabel = $derived(panel === 'new' ? 'Create asset' : panel === 'copy' ? 'Copy asset' : panel === 'rename' ? 'Rename asset' : leaf ? 'Confirm retire' : 'Confirm archive');
	const submitIcon = $derived(panel === 'new' ? 'plus' : panel === 'copy' ? 'layers' : panel === 'rename' ? 'arrow-left-right' : 'archive');
</script>

<div class="aa" aria-busy={pending}>
	{#if !create && asset}
		<div class="head">
			<div class="t">
				<span class="lbl">{label}</span>
				<h2 class="h-sec ellipsis">{heading}</h2>
			</div>
			<span class="stat">{#if status}<span class="state" data-tone={pending ? 'running' : 'settled'} role="status">{status}</span>{/if}</span>
			<span class="ibar" role="toolbar" aria-label="Asset actions">
				<IconButton icon="pencil" label={editLabel} reason={actionHelp} disabled={pending} onclick={edit} />
				{#if !leaf}<IconButton icon="layers" label="Copy as new asset" disabled={pending} onclick={() => arm('copy')} />{/if}
				{#if !leaf && !bundled}<IconButton icon="arrow-left-right" label="Rename" disabled={pending} onclick={() => arm('rename')} />{/if}
				<span class="sep"></span>
				<IconButton icon="copy" label="Copy ref" onclick={() => copyText(asset.ref, 'Ref')} />
				{#if onversions}<IconButton icon="history" label="Versions" onclick={onversions} />{/if}
				<span class="sep"></span>
				<IconButton icon={retired ? 'archive-restore' : 'archive'} label={archiveLabel} disabled={pending} onclick={() => (retired ? restore() : arm('archive'))} />
			</span>
		</div>
	{/if}
	{#if panel}
		<div class="panel" class:new-panel={create}>
			{#if !create}<span class="x"><IconButton icon="x" label="Close" small disabled={pending} onclick={dismiss} /></span>{/if}
			{#if panel === 'edit'}
				{#if path}
					<div class="line"><span class="lbl">File</span><code title={path}>{displayPath}</code><IconButton icon="copy" label="Copy file path" small onclick={() => copyText(path, 'Path')} /></div>
					<div class="line"><span class="lbl">Command</span><code>herdsman library edit {asset?.ref}</code><IconButton icon="copy" label="Copy command" small onclick={() => copyText(`herdsman library edit ${asset?.ref}`, 'Command')} /></div>
					<p class="hint">{leaf ? 'Evidence is not edited here. ' : ''}{changed ? `Saved ${changed} · re-read` : connected ? 'Watching for your save' : 'Live updates disconnected — return to this window to re-read.'}</p>
				{/if}
			{:else}
				<form class:new-form={create} onsubmit={(event) => { event.preventDefault(); void submit(); }}>
					{#if panel === 'archive'}
						<p class="prose">{#if leaf}Retired leaves are no longer offered to agents. The file stays in .herdsman/memory and can be restored.{:else}Leaves the active shelf. Approved plans keep their frozen copy; {incoming === 0 ? 'nothing references it' : `${incoming} ${incoming === 1 ? 'asset references' : 'assets reference'} it and will report an archived reference`}.{/if}
						{#if asset?.shadows_bundled} This override shadows the bundled original and keeps shadowing it.{/if}</p>
					{:else}
						{#if panel === 'new'}
							<label class="field-l"><span class="lbl">Kind</span><select class="sel" bind:value={kind} disabled={pending}>{#each kinds as option (option)}<option value={option}>{KIND_WORD[option]}</option>{/each}</select></label>
						{/if}
						<label class="field-l"><span class="lbl">{panel === 'new' ? 'Name' : 'New name'}</span><input class="inp" id={create ? 'asset-new-name' : 'asset-action-name'} bind:value={name} disabled={pending} required /></label>
						{#if panel === 'new'}<label class="field-l"><span class="lbl">Title</span><input class="inp" bind:value={title} placeholder="optional" disabled={pending} /></label>{/if}
					{/if}
					{#if error}<p class="msg" role="alert"><span class="state" data-tone="failed">Not written</span> <span class="mono">{error}</span></p>{/if}
					<div class="btnrow">
						<Button type="submit" icon={submitIcon} kind={panel === 'archive' ? 'danger' : 'primary'} small busy={pending}>{submitLabel}</Button>
						{#if conflict}<Button icon="refresh-cw" small disabled={pending} onclick={() => void onreread()}>Re-read</Button>{/if}
						<Button icon="x" small disabled={pending} onclick={dismiss}>Cancel</Button>
					</div>
				</form>
			{/if}
		</div>
	{/if}
	{#if error && (panel === 'edit' || panel === null)}<p class="msg err" role="alert"><span class="state" data-tone="failed">Not written</span> <span class="mono">{error}</span></p>{/if}
</div>

<style>
	.aa { min-width: 0; }
	.head { display: flex; align-items: center; gap: 16px; padding: 16px 28px 12px; }
	.t { flex: 1; min-width: 0; display: grid; gap: 4px; }
	.t .h-sec { margin: 0; }
	.stat { flex: none; display: flex; align-items: center; max-width: 34%; overflow: hidden; }
	.stat .state { overflow: hidden; text-overflow: ellipsis; }
	.panel { position: relative; padding: 14px 28px 16px; background: var(--p1); border-block: 1px solid var(--ln); display: grid; gap: 10px; }
	.panel .x { position: absolute; right: 20px; top: 8px; }
	.line { display: grid; grid-template-columns: 78px minmax(0, 1fr) auto; gap: 10px; align-items: center; margin-right: 36px; }
	.line code { font: 400 12px var(--f-mono); color: var(--tx); background: var(--bg); border: 1px solid var(--ln); padding: 7px 10px; overflow-x: auto; white-space: pre; min-width: 0; }
	form { display: grid; gap: 12px; max-width: 520px; }
	.new-panel { padding: 14px 24px 16px; border-top: 0; }
	.new-form { max-width: none; }
	.msg { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; color: var(--tx2); overflow-wrap: anywhere; }
	.err { padding: 10px 28px; }
</style>
