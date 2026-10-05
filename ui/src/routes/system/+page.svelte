<script lang="ts">
	import { ICONS, type IconName } from '$lib/icons';
	import { MARKS, type MarkId } from '$lib/marks';
	import Icon from '$lib/Icon.svelte';
	import Mark from '$lib/Mark.svelte';
	import Button from '$lib/Button.svelte';
	import IconButton from '$lib/IconButton.svelte';
	import Tabs from '$lib/Tabs.svelte';
	import StateMark from '$lib/StateMark.svelte';
	import EffortLine from '$lib/EffortLine.svelte';
	import EffortTicks from '$lib/EffortTicks.svelte';
	import Spectrum from '$lib/Spectrum.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import RichSelect from '$lib/RichSelect.svelte';
	import MarkdownReader from '$lib/MarkdownReader.svelte';
	import { TONE_WORD, type Tone } from '$lib/tones';

	const continuum = ['bg', 'band', 'p1', 'p2', 'p3', 'ln', 'ln2', 'tx', 'tx2', 'dim', 'fnt'];
	const lines = [
		['l405', 'violet', 'plan override, contracts, planner notes'],
		['l436', 'indigo', 'harness codex'],
		['l486', 'cyan', 'harness pi, roles'],
		['l546', 'green', 'harness opencode, pass, live, skills, diff +'],
		['l589', 'sodium', 'needs you — nothing else'],
		['l615', 'orange', 'harness claude-code, orchestration'],
		['l656', 'deep red', 'failed, conflict, destructive hover, diff −']
	];
	const tones = Object.keys(TONE_WORD) as Tone[];
	const motion = [
		['--t-fast', '120ms', 'hover colour, row tint'],
		['--t-fade', '140ms', 'controls appearing in place'],
		['--t-pane', '200ms', 'tab/pane enter'],
		['--t-tab', '220ms', 'tab indicator, index highlight'],
		['--t-side', '240ms', 'sidebar width'],
		['--t-dock', '260ms', 'dock maximize / restore'],
		['--t-land', '320ms', 'an emission line landing']
	];
	const sample = `# Reader sample

## Brief

Let **dispatch** take a start branch and a target branch, with a [link](https://example.com) and \`inline code\`.

- a hairline-dash list item
- another one

1. ordered
2. list

## Checklist

- [x] read dispatch.py
- [ ] write the handoff

### Code

\`\`\`python
def slug(title: str) -> str:
    # derive a deterministic slug
    return "agent/" + title.lower()[:40]  # 40 chars
\`\`\`

> **Planner note**
> Two lanes share one write route; serialise them.

> [!NEEDS] Approve before any worker starts.

| Lane | Chain | Critical |
| --- | --- | --- |
| 01 | D1 › D2 › D5 | yes |
`;
	let tab = $state('a');
	let seg = $state<'all' | 'running' | 'needs'>('all');
	let pool = $state(['medium', 'high']);
	let editing = $state(false);
	let pick = $state<string | null>('pi/gpt');
	let landKey = $state(0);
</script>

<div class="view">
	<header class="ph">
		<div class="title">
			<h1 class="h-title">System</h1>
			<div class="meta"><span class="lbl">Living style guide · dev builds only · built from the real components</span></div>
		</div>
	</header>
	<div class="sys">
		<section class="w">
			<p class="rule-h"><span class="lbl">Continuum</span></p>
			<div class="sw-grid">
				{#each continuum as t (t)}
					<div class="swatch"><div class="c" style:background="var(--{t})"></div><b>{t}</b><div>var(--{t})</div></div>
				{/each}
			</div>
		</section>
		<section class="w">
			<p class="rule-h"><span class="lbl">Emission lines</span></p>
			<div class="lines">
				{#each lines as [t, name, use] (t)}
					<div class="ln"><span class="mono" style:color="var(--{t})">{t}</span><span class="lbl">{name}</span><i style:--c="var(--{t})"></i><span class="hint">{use}</span></div>
				{/each}
			</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">State marks</span></p>
			<div class="states">{#each tones as t (t)}<StateMark tone={t} />{/each}</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Type roles</span></p>
			<div class="typespec">
				<div><span class="lbl">Title</span><span class="h-title">Fleet</span></div>
				<div><span class="lbl">Section</span><span class="h-sec">Derive the branch name</span></div>
				<div><span class="lbl">Body</span><span>Barlow 400 13.5 — the UI default.</span></div>
				<div><span class="lbl">Data</span><span class="mono">plan_ebdce7fa · 34.4k tok · 12:40</span></div>
				<div><span class="lbl">Number</span><span class="num">34.4k <small class="muted">/ 120k</small></span></div>
			</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Buttons</span></p>
			<div class="row">
				<Button icon="check-check" kind="primary">Approve v2</Button>
				<Button icon="message-square-diff">Request changes</Button>
				<Button icon="trash-2" kind="danger">Delete</Button>
				<Button icon="play" disabled>Start now</Button>
				<Button icon="rotate-ccw" busy>Retrying</Button>
			</div>
			<div class="row">
				<Button icon="plus" small>Add harness</Button>
				<Button icon="refresh-cw" small>Measure</Button>
				<span class="ibar">
					<IconButton icon="pause" label="Pause run" disabled reason="Pausing a whole run is not available yet" />
					<IconButton icon="history" label="Replay" />
					<span class="sep"></span>
					<IconButton icon="copy" label="Copy plan id" />
					<IconButton icon="maximize-2" label="Maximize" shortcut="M" />
					<IconButton icon="trash-2" label="Delete" danger />
				</span>
			</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Tabs · segmented</span></p>
			<Tabs prefix="sys" label="Demo tabs" selected={tab} onselect={(id) => (tab = id)}
				items={[{ id: 'a', label: 'Field', icon: 'network' }, { id: 'b', label: 'Plan', icon: 'file-text', count: 'v1' }, { id: 'c', label: 'Schedule', icon: 'list-tree', count: 9 }, { id: 'd', label: 'Review', count: 'v2', countTone: 'nd' }]} />
			<div class="row"><Segmented label="Filter" value={seg} onchange={(v) => (seg = v)} options={[{ id: 'all', label: 'All' }, { id: 'running', label: 'Running' }, { id: 'needs', label: 'Needs you' }]} /></div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Effort</span><span class="r"><IconButton icon="pencil" label="Edit" small pressed={editing} onclick={() => (editing = !editing)} /></span></p>
			<EffortLine levels={['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']} selected={pool} {editing} onchange={(n) => (pool = n)} />
			<div class="row"><EffortLine levels={[]} selected={[]} /><EffortTicks effort="high" /><EffortTicks effort="low" /></div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Inputs</span></p>
			<div class="inputs">
				<input class="inp" placeholder="Token cap" aria-label="Token cap" />
				<select class="sel" aria-label="Tier"><option>frontier</option><option>worker-high</option></select>
				<RichSelect label="Planner" value={pick} onchange={(v) => (pick = v)} options={[
					{ value: 'cc/opus', label: 'claude-code › opus', harness: 'claude-code', model: 'opus', word: 'Ready' },
					{ value: 'pi/gpt', label: 'pi › gpt', harness: 'pi', model: 'openai-codex/gpt-6.1-sol', word: 'Ready' },
					{ value: 'pi/ds', label: 'pi › deepseek', harness: 'pi', model: 'opencode-go/deepseek-v4.1-flash', word: 'Not ready', wordTone: 'waiting' },
					{ value: 'pi/x', label: 'pi › default', harness: 'pi', model: 'pi-default' }
				]} />
				<textarea class="ta" placeholder="Brief" aria-label="Brief"></textarea>
			</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Spectrum · chips</span></p>
			<div class="row"><Spectrum settled={2} running={1} needs={1} failed={1} waiting={4} /><span class="mono muted">2/9 settled · 1 need you</span></div>
			<div class="row"><Spectrum tall settled={6} failed={4} waiting={17} /></div>
			<div class="row"><span class="chip">implementer</span><span class="branch"><Icon name="git-branch" size={14} />uat <Icon name="chevron-right" size={12} /> new-branch-a</span><span class="count nd">3</span><span class="count bad">1</span></div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Marks</span></p>
			<div class="marks">
				{#each Object.keys(MARKS) as k (k)}
					<div><svg width="24" height="24" viewBox={MARKS[k as MarkId].viewBox} aria-hidden="true">{@html MARKS[k as MarkId].body}</svg><span>{k}</span></div>
				{/each}
				<div><Mark model="pi-default" size={24} /><span>unknown model</span></div>
			</div>
		</section>
		<section>
			<p class="rule-h"><span class="lbl">Motion</span><span class="r"><IconButton icon="play" label="Replay land" small onclick={() => landKey++} /></span></p>
			{#each motion as [t, v, use] (t)}
				<div class="motion-row"><span class="mono">{t}</span><span class="mono muted">{v}</span><span class="hint">{use}</span></div>
			{/each}
			{#key landKey}<div class="demo-land"></div>{/key}
		</section>
		<section class="w">
			<p class="rule-h"><span class="lbl">Markdown reader</span></p>
			<MarkdownReader source={sample} toc compact />
		</section>
		<section class="w">
			<p class="rule-h"><span class="lbl">Icon vocabulary</span><span class="r count">{Object.keys(ICONS).length}</span></p>
			<div class="icongrid">
				{#each Object.keys(ICONS) as n (n)}<div><Icon name={n as IconName} size={18} /><span>{n}</span></div>{/each}
			</div>
		</section>
	</div>
</div>

<style>
	.view { display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; }
	.sys { overflow: auto; padding: 24px 28px 60px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 36px 32px; align-content: start; }
	.w { grid-column: 1 / -1; }
	.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 14px; }
	.sw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 10px; }
	.swatch { border: 1px solid var(--ln); padding-bottom: 8px; }
	.swatch .c { height: 46px; margin-bottom: 8px; border-bottom: 1px solid var(--ln); }
	.swatch b { display: block; padding: 0 10px; font: 500 12px var(--f-label); letter-spacing: 0.12em; text-transform: uppercase; }
	.swatch div:not(.c) { padding: 0 10px; font: 400 11px var(--f-mono); color: var(--dim); }
	.lines { display: grid; gap: 10px; }
	.ln { display: grid; grid-template-columns: 48px 76px 1fr 300px; gap: 14px; align-items: center; }
	.ln i { display: block; height: 2px; background: var(--c); }
	.states { display: grid; grid-template-columns: repeat(2, max-content); gap: 12px 40px; }
	.typespec > div { display: grid; grid-template-columns: 110px 1fr; gap: 16px; align-items: baseline; padding: 10px 0; border-bottom: 1px solid var(--ln); }
	.inputs { display: grid; gap: 10px; }
	.marks { display: flex; gap: 22px; flex-wrap: wrap; }
	.marks div { display: flex; flex-direction: column; align-items: center; gap: 8px; font: 400 11px var(--f-mono); color: var(--dim); }
	.marks svg { color: var(--tx); }
	.motion-row { display: grid; grid-template-columns: 90px 60px 1fr; gap: 14px; padding: 8px 0; border-bottom: 1px solid var(--ln); align-items: center; }
	.demo-land { width: 2px; height: 28px; margin-top: 14px; background: var(--l486); transform-origin: 50% 100%; animation: land var(--t-land) var(--land) both; }
	.icongrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 1px; background: var(--ln); border: 1px solid var(--ln); }
	.icongrid div { background: var(--bg); display: flex; align-items: center; gap: 10px; padding: 10px 12px; font: 400 12px var(--f-ui); color: var(--tx2); }
</style>
