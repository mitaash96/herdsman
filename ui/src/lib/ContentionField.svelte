<script lang="ts" module>
	import type { Attempt } from './daemon';

	/** `m:ss` / `h:mm:ss`; `—` when there is nothing real to show (seeded attempts start and end together). */
	export function clock(seconds: number): string {
		if (!Number.isFinite(seconds) || seconds < 1) return '—';
		const s = Math.floor(seconds);
		const h = Math.floor(s / 3600);
		const m = Math.floor((s % 3600) / 60);
		const r = s % 60;
		return h > 0
			? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
			: `${m}:${String(r).padStart(2, '0')}`;
	}

	/** Minute resolution for a running attempt, so a still field does not change under the eye. */
	export function runningClock(seconds: number): string {
		const m = Math.max(0, Math.floor(seconds / 60));
		return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`;
	}

	const ms = (value: string | null | undefined): number => (value ? Date.parse(value) : NaN);

	/** Seconds the live attempt has run, or `null` when nothing is running. */
	export function liveSeconds(attempts: Attempt[], now: number): number | null {
		const live = attempts[attempts.length - 1];
		if (!live || live.ended_at) return null;
		const at = ms(live.started_at);
		return Number.isNaN(at) ? null : Math.max(0, (now - at) / 1000);
	}

	/** Seconds across every ended attempt. */
	export function spentSeconds(attempts: Attempt[]): number {
		let total = 0;
		for (const a of attempts) {
			const d = (ms(a.ended_at) - ms(a.started_at)) / 1000;
			if (Number.isFinite(d) && d > 0) total += d;
		}
		return total;
	}
</script>

<script lang="ts">
	import type { Field, Member, Touch } from './field';
	import type { Kitchen, Plan } from './daemon';
	import Mark from './Mark.svelte';
	import Icon from './Icon.svelte';
	import EffortTicks from './EffortTicks.svelte';
	import { harnessHue } from './marks';
	import { TONE_WORD, memberTone, type Tone } from './tones';

	let {
		field,
		contention,
		contentionRead,
		selected,
		planId = '',
		plan = null,
		kitchen = null,
		now = Date.now(),
		fit = false,
		waiting = new Set<string>(),
		starting = new Set<string>(),
		onselect
	}: {
		field: Field;
		contention: Map<string, Touch[]>;
		/** False when the risk report could not be read: cords are unknown, not absent. */
		contentionRead: boolean;
		selected: string | null;
		/** Resets the "what changed" memory when another plan is shown. */
		planId?: string;
		/** The folded (or replayed) plan: effort, overrides, attempts. Absent = those read as unknown. */
		plan?: Plan | null;
		kitchen?: Kitchen | null;
		now?: number;
		/** Scale the whole field into the stage width. */
		fit?: boolean;
		/** Members that need the operator (an attention item or an agent stopped at a dialog). */
		waiting?: Set<string>;
		/** Members whose agent was just launched; the daemon is confirming it took its prompt. */
		starting?: Set<string>;
		onselect: (id: string) => void;
	} = $props();

	/* DS §9.13 geometry. */
	const L0 = 170; // lane rail
	const CW = 262; // rank pitch
	const LH = 80; // lane pitch
	const T = 40; // rank marks row
	const BW = 214;
	const BH = 56;

	const width = $derived(L0 + field.columns * CW);
	const height = $derived(T + field.lanes.length * LH);
	const px = (m: Member) => L0 + m.depth * CW;
	const py = (m: Member) => T + m.lane * LH;

	let box = $state<HTMLDivElement>();
	let boxWidth = $state(0);
	let overflows = $state(false);
	let atEnd = $state(true);
	const scale = $derived(fit && boxWidth > 0 ? Math.min(1, boxWidth / width) : 1);

	function measure() {
		if (!box) return;
		boxWidth = box.clientWidth;
		overflows = box.scrollWidth > box.clientWidth + 1;
		atEnd = box.scrollLeft + box.clientWidth >= box.scrollWidth - 2;
	}
	$effect(() => {
		void width;
		void scale;
		if (!box) return;
		measure();
		const ro = new ResizeObserver(measure);
		ro.observe(box);
		return () => ro.disconnect();
	});

	/* --- what each member is -------------------------------------------------- */
	const spec = (id: string) => plan?.initiatives[id]?.spec ?? null;
	const toneOf = (m: Member): Tone => {
		const id = m.node.initiative_id;
		if (starting.has(id) && m.node.state !== 'failed') return 'starting';
		return memberTone(m.node, waiting.has(id));
	};
	function effortOf(id: string): string | null {
		const init = plan?.initiatives[id];
		return init?.assignment_override?.effort ?? init?.spec.assignment.effort ?? null;
	}
	const ladder = (m: Member): string[] | undefined =>
		kitchen?.effort_levels[`${m.node.harness}/${m.node.model}`];
	/** The plan chose a different pair than the role's default would have. */
	function overrides(id: string): boolean {
		const s = spec(id);
		const role = s?.contract?.role;
		const def = role ? kitchen?.defaults.roles[role] : null;
		return !!s && !!def && (def.harness !== s.assignment.harness || def.model !== s.assignment.model);
	}
	const roleOf = (id: string) => spec(id)?.contract?.role ?? '';

	function timeOf(m: Member): { text: string; live: boolean } {
		const attempts = plan?.initiatives[m.node.initiative_id]?.attempts ?? [];
		const live = m.node.state === 'running' ? liveSeconds(attempts, now) : null;
		if (live !== null) return { text: runningClock(live), live: true };
		return { text: attempts.length ? clock(spentSeconds(attempts)) : '', live: false };
	}
	/** Share of the member's estimate already spent; `null` when there is no estimate to measure against. */
	function elapsedShare(m: Member): number | null {
		const attempts = plan?.initiatives[m.node.initiative_id]?.attempts ?? [];
		const live = liveSeconds(attempts, now);
		const estimate = spec(m.node.initiative_id)?.duration_estimate_seconds;
		if (live === null || !estimate) return null;
		return Math.min(1, live / estimate);
	}

	/* --- motion: only a changed member's line lands --------------------------- */
	let just = $state<Record<string, true>>({});
	let seen = new Map<string, string>();
	let seenPlan = '';
	$effect(() => {
		const sig = new Map(
			field.members.map((m) => [
				m.node.initiative_id,
				`${toneOf(m)}|${m.node.attempts}|${m.node.checkpoint_id ?? ''}`
			])
		);
		if (seenPlan !== planId) {
			seenPlan = planId;
			seen = sig;
			return;
		}
		const changed: string[] = [];
		for (const [id, s] of sig) if (seen.has(id) && seen.get(id) !== s) changed.push(id);
		seen = sig;
		if (changed.length === 0) return;
		just = Object.fromEntries(changed.map((id) => [id, true as const]));
		const timer = setTimeout(() => (just = {}), 500);
		return () => clearTimeout(timer);
	});

	/* --- cords ----------------------------------------------------------------- */
	interface Cord {
		key: string;
		d: string;
		kind: 'edge' | 'critical' | 'suggested';
		ends: [string, string];
		title?: string;
	}
	const mid = (m: Member) => py(m) + BH / 2;
	/** Out of `a`'s right edge, down the gutter between ranks, into `b`'s left edge. */
	function elbow(a: Member, b: Member): string {
		const x1 = px(a) + BW;
		const x2 = px(b) - 6;
		if (a.lane === b.lane) return `M${x1} ${mid(a)}H${x2}`;
		const mx = x1 + (x2 - x1) / 2;
		return `M${x1} ${mid(a)}H${mx}V${mid(b)}H${x2}`;
	}
	const onCrit = $derived(new Set(field.criticalPath.map((m) => m.node.initiative_id)));
	const critPairs = $derived(
		new Set(field.criticalPath.slice(1).map((m, i) => `${field.criticalPath[i].node.initiative_id}>${m.node.initiative_id}`))
	);

	const cords = $derived.by(() => {
		const drawn: Cord[] = [];
		const add = (a: Member, b: Member) => {
			const ida = a.node.initiative_id;
			const idb = b.node.initiative_id;
			drawn.push({
				key: `e:${ida}>${idb}`,
				d: elbow(a, b),
				kind: critPairs.has(`${ida}>${idb}`) ? 'critical' : 'edge',
				ends: [ida, idb],
				title: `${idb} depends on ${ida}`
			});
		};
		for (const lane of field.lanes) for (let i = 1; i < lane.length; i++) add(lane[i - 1], lane[i]);
		for (const { from, to } of field.crossings) add(from, to);
		// A critical hop that is not one of the lane/crossing pairs above is still drawn.
		for (const pair of critPairs) {
			if (drawn.some((c) => c.key === `e:${pair}`)) continue;
			const [a, b] = pair.split('>').map((id) => field.byId.get(id));
			if (a && b) add(a, b);
		}
		if (contentionRead) {
			const seenPairs = new Set<string>();
			for (const [id, touches] of contention) {
				const a = field.byId.get(id);
				if (!a) continue;
				for (const touch of touches) {
					if (touch.kind === 'write_write') continue; // drawn as a bracket below
					const b = field.byId.get(touch.peer);
					if (!b || a.lane === b.lane) continue;
					if (!(selected === id || selected === touch.peer)) continue; // advisory: drawn for the member you read
					const pair = [id, touch.peer].sort().join('|');
					if (seenPairs.has(pair)) continue;
					seenPairs.add(pair);
					const [l, r] = a.depth <= b.depth ? [a, b] : [b, a];
					const x1 = px(l) + BW;
					const x2 = px(r) - 6;
					const cx = x1 + 14;
					drawn.push({
						key: `s:${pair}`,
						d: `M${x1} ${mid(l)}H${cx}V${mid(r)}H${Math.max(x2, cx)}`,
						kind: 'suggested',
						ends: [id, touch.peer],
						title: `${touch.paths.join(', ')} is written by one of ${id}, ${touch.peer} and read by the other, with no dependency between them`
					});
				}
			}
		}
		return drawn;
	});

	interface Bracket {
		key: string;
		d: string;
		tagX: number;
		tagY: number;
		text: string;
		title: string;
	}
	const brackets = $derived.by(() => {
		const out: Bracket[] = [];
		if (!contentionRead) return out;
		const seenPairs = new Set<string>();
		for (const [id, touches] of contention) {
			const a = field.byId.get(id);
			if (!a) continue;
			for (const touch of touches) {
				if (touch.kind !== 'write_write') continue;
				const b = field.byId.get(touch.peer);
				if (!b || a.lane === b.lane) continue; // same lane: already ordered
				const pair = [id, touch.peer].sort().join('|');
				if (seenPairs.has(pair)) continue;
				seenPairs.add(pair);
				const [l, r] = a.depth < b.depth || (a.depth === b.depth && a.lane < b.lane) ? [a, b] : [b, a];
				const x1 = px(l) + BW;
				const cx = x1 + 22;
				const end = r.depth > l.depth ? px(r) - 4 : px(r) + BW + 4;
				const top = l.lane < r.lane ? l : r;
				const bottom = top === l ? r : l;
				const y1 = py(l) + (l.lane < r.lane ? BH - 8 : 8);
				const y2 = py(r) + (l.lane < r.lane ? 8 : BH - 8);
				const path = touch.paths[0] ?? '';
				out.push({
					key: `w:${pair}`,
					d: `M${x1} ${y1}H${cx}V${y2}H${end}`,
					tagX: cx + 8,
					tagY: py(top) + BH + 2,
					text: `Both write ${path}${touch.paths.length > 1 ? ` +${touch.paths.length - 1}` : ''}`,
					title: `${top.node.initiative_id} and ${bottom.node.initiative_id} both write ${touch.paths.join(', ')}; they cannot run at the same time`
				});
			}
		}
		return out;
	});

	const incident = (cord: Cord) => selected !== null && cord.ends.includes(selected);

	/* The one tab stop into the drawing: never the selected id alone, or a revision that
	   drops it would leave no tabbable seat at all. */
	const anchor = $derived(
		selected !== null && field.byId.has(selected)
			? selected
			: (field.members[0]?.node.initiative_id ?? null)
	);

	/* Selection follows focus; nothing here is destructive. Keyboard model unchanged. */
	function move(from: Member, key: string) {
		const lane = field.lanes[from.lane];
		const at = lane.indexOf(from);
		let next: Member | undefined;
		if (key === 'ArrowRight') next = lane[at + 1];
		else if (key === 'ArrowLeft') next = lane[at - 1];
		else if (key === 'Home') next = lane[0];
		else if (key === 'End') next = lane[lane.length - 1];
		else if (key === 'ArrowUp' || key === 'ArrowDown') {
			const target = field.lanes[from.lane + (key === 'ArrowDown' ? 1 : -1)];
			if (target)
				next = target.reduce((best, m) =>
					Math.abs(m.depth - from.depth) < Math.abs(best.depth - from.depth) ? m : best
				);
		}
		if (!next) return false;
		onselect(next.node.initiative_id);
		document.getElementById(`seat-${next.node.initiative_id}`)?.focus();
		return true;
	}

	function onkeydown(event: KeyboardEvent, member: Member) {
		if (event.altKey || event.ctrlKey || event.metaKey) return;
		if (move(member, event.key)) event.preventDefault();
	}

	function describe(m: Member): string {
		const id = m.node.initiative_id;
		const touches = contention.get(id) ?? [];
		const conflicts = touches.filter((t) => t.kind === 'write_write').length;
		return [
			`${id}, ${m.node.name}`,
			TONE_WORD[toneOf(m)],
			m.node.state === 'pending' && !m.node.ready ? `waiting on ${m.blockedBy.join(', ')}` : '',
			`${m.node.harness}, ${m.node.model}${effortOf(id) ? `, ${effortOf(id)} effort` : ''}`,
			`lane ${m.lane + 1}, rank ${m.depth}`,
			m.onCriticalPath ? 'on the critical path' : '',
			overrides(id) ? 'plan overrides the role default' : '',
			conflicts ? `${conflicts} write conflict${conflicts > 1 ? 's' : ''}` : ''
		]
			.filter(Boolean)
			.join('. ');
	}
</script>

<div
	class="stage-scroll"
	class:fade={overflows && !atEnd}
	bind:this={box}
	onscroll={measure}
	role="region"
	aria-label="Scrollable contention field"
>
	<div class="sizer" style:width="{width * scale}px" style:height="{height * scale}px">
		<div
			class="field"
			style:width="{width}px"
			style:height="{height}px"
			style:transform={scale < 1 ? `scale(${scale})` : undefined}
			role="group"
			aria-label="Contention field: {field.members.length} initiatives in {field.lanes.length} lanes. Arrow keys move between members."
		>
			{#each { length: field.columns } as _, r (r)}
				<span class="rank" style:left="{L0 + r * CW}px" aria-hidden="true">R{r}</span>
			{/each}

			{#each field.lanes as lane, l (l)}
				<i class="rule" style:top="{T + l * LH + BH + 11}px" aria-hidden="true"></i>
				<div class="lane-lbl" style:top="{T + l * LH + BH - 22}px">
					Lane {String(l + 1).padStart(2, '0')}
					<b>{lane.some((m) => m.onCriticalPath) ? `${lane.length} · critical` : `${lane.length} in chain`}</b>
				</div>
			{/each}

			<svg class="cords" class:reading={selected !== null} width={width} height={height} aria-hidden="true">
				{#each cords as cord (cord.key)}
					<path class="cord {cord.kind}" class:incident={incident(cord)} class:crit={cord.kind === 'critical'} d={cord.d}><title>{cord.title}</title></path>
				{/each}
				{#each brackets as b (b.key)}
					<path class="cord conf" d={b.d}><title>{b.title}</title></path>
				{/each}
			</svg>
			{#each brackets as b (b.key)}
				<span class="conf-tag" style:left="{b.tagX}px" style:top="{b.tagY}px" title={b.title}><Icon name="git-compare" size={12} />{b.text}</span>
			{/each}

			{#each field.members as m (m.node.initiative_id)}
				{@const id = m.node.initiative_id}
				{@const tone = toneOf(m)}
				{@const time = timeOf(m)}
				{@const share = elapsedShare(m)}
				{@const eff = effortOf(id)}
				<button
					id="seat-{id}"
					class="mem"
					class:sel={selected === id}
					class:just={just[id]}
					data-tone={tone}
					style:left="{px(m)}px"
					style:top="{py(m)}px"
					style:--h={harnessHue(m.node.harness)}
					type="button"
					aria-current={selected === id ? 'true' : undefined}
					aria-label={describe(m)}
					tabindex={id === anchor ? 0 : -1}
					onclick={() => onselect(id)}
					onkeydown={(event) => onkeydown(event, m)}
				>
					<span class="tip" class:below={m.lane === 0} aria-hidden="true">
						<b>{m.node.name}</b>
						<span class="who"><Mark harness={m.node.harness} size={14} />{m.node.harness}<Icon name="chevron-right" size={14} /><Mark model={m.node.model} size={14} />{m.node.model} · {eff ?? 'harness default'}</span>
					</span>
					<i class="em" aria-hidden="true"></i>
					{#if share !== null}<i class="grow" style:width="{BW - 2}px" style:transform="scaleX({Math.min(1, Math.max(0, share))})" aria-hidden="true"></i>{/if}
					<span class="r1"><span class="id">{id}</span>{#if roleOf(id)}<span class="role">{roleOf(id)}</span>{/if}<span class="sw">{TONE_WORD[tone]}</span></span>
					<span class="nm" class:struck={tone === 'cancelled'}>{m.node.name}</span>
					<span class="r3">
						<Mark model={m.node.model} size={14} />
						<EffortTicks effort={eff} ladder={ladder(m)} />
						{#if overrides(id)}<span class="ov">override</span>{/if}
						<span class="t">{#if time.text}{time.live ? '▸ ' : ''}{time.text}{/if}</span>
					</span>
				</button>
			{/each}
		</div>
	</div>
</div>

<style>
	.stage-scroll { overflow: auto; min-height: 0; height: 100%; }
	.stage-scroll.fade { mask-image: linear-gradient(to right, #000 calc(100% - 40px), transparent); }
	.sizer { position: relative; min-width: 100%; }
	.field { position: relative; transform-origin: 0 0; min-width: 100%; }

	.rank { position: absolute; top: 12px; font: 400 10px var(--f-mono); color: var(--fnt); }
	.rule { position: absolute; left: 0; right: 0; height: 1px; background: var(--ln); }
	.lane-lbl {
		position: absolute; left: 24px; font: 500 11px var(--f-label); letter-spacing: 0.16em; color: var(--fnt);
		text-transform: uppercase; white-space: nowrap;
	}
	.lane-lbl b { display: block; font: 400 11px var(--f-mono); letter-spacing: 0; color: var(--dim); margin-top: 2px; }

	.cords { position: absolute; inset: 0; pointer-events: none; overflow: visible; }
	.cord { stroke: var(--ln2); stroke-width: 1; fill: none; }
	.cord.crit { stroke: var(--tx); stroke-opacity: 0.55; }
	.cord.suggested { stroke: var(--dim); stroke-dasharray: 3 3; }
	.cord.conf { stroke: var(--l656); stroke-dasharray: 1 3; }
	.cords.reading .cord:not(.incident):not(.crit):not(.conf) { stroke-opacity: 0.18; }
	.conf-tag {
		position: absolute; display: flex; align-items: center; gap: 6px; padding: 0 6px; background: var(--bg);
		font: 500 11px var(--f-label); letter-spacing: 0.14em; color: var(--l656); text-transform: uppercase; white-space: nowrap;
	}

	/* ---- the member: 214 × 56, an emission line and three rows ---- */
	.mem {
		position: absolute; width: 214px; height: 56px; padding: 1px 0 0 16px; text-align: left; cursor: pointer;
		background: var(--bg); border: 0; display: block; color: var(--tx);
	}
	.mem.sel { background: var(--p2); box-shadow: 0 0 0 7px var(--p2), 0 0 0 8px var(--ln2); z-index: 2; }
	.mem:hover, .mem:focus-visible { z-index: 5; }
	.mem:focus-visible { outline: 1px solid var(--tx); outline-offset: 8px; }

	.em {
		position: absolute; left: 0; top: 0; width: 2px; height: 56px; background: var(--h); transform-origin: 50% 100%;
		pointer-events: none;
	}
	.mem.just .em { animation: land var(--t-land) var(--land) both; }
	.mem.sel .em { animation: land var(--t-land) var(--land) both, emit 2.2s ease-in-out var(--t-land) infinite; }
	.mem[data-tone='needs'] .em { background: var(--l589); box-shadow: 4px 0 0 var(--l589); }
	.mem[data-tone='waiting'] .em, .mem[data-tone='ready'] .em {
		background: repeating-linear-gradient(var(--h) 0 5px, transparent 5px 9px);
	}
	.mem[data-tone='waiting'] .em { opacity: 0.6; }
	.mem[data-tone='ready'] .em::after {
		content: ''; position: absolute; left: -3px; top: -3px; width: 8px; height: 8px; border-radius: 50%; background: var(--h);
	}
	.mem[data-tone='failed'] .em { background: linear-gradient(var(--l656) 0 20px, transparent 20px 34px, var(--l656) 34px); }
	.mem[data-tone='settled'] .em { opacity: 0.5; }
	.mem[data-tone='paused'] .em { top: 28px; height: 28px; }
	.mem[data-tone='cancelled'] .em { background: var(--fnt); }
	.mem[data-tone='starting'] .em { opacity: 0.6; }

	.grow {
		position: absolute; left: 2px; top: 55px; height: 1px; background: var(--h); opacity: 0.7;
		transform-origin: 0 50%; transition: transform var(--t-land) var(--ease); pointer-events: none;
	}
	.r1 {
		display: flex; gap: 9px; align-items: baseline; font: 500 11px var(--f-label); letter-spacing: 0.14em;
		text-transform: uppercase; color: var(--dim); white-space: nowrap; overflow: hidden;
	}
	.r1 .id { font: 500 12px var(--f-mono); letter-spacing: 0; color: var(--tx); text-transform: none; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 0 1 auto; }
	.r1 .role { overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 0 100 auto; }
	.r1 .sw { color: var(--sc); margin-left: auto; padding-right: 6px; flex: none; }
	.nm {
		display: block; padding-right: 6px; font: 500 13.5px/1.2 var(--f-ui); white-space: nowrap; overflow: hidden;
		text-overflow: ellipsis; margin: 3px 0 5px; transition: color var(--t-fast); color: var(--tx);
	}
	.mem[data-tone='waiting'] .nm, .mem[data-tone='settled'] .nm { color: var(--tx2); }
	.mem[data-tone='cancelled'] .nm { color: var(--fnt); }
	.nm.struck { text-decoration: line-through; }
	.mem:hover .nm { color: var(--tx); }
	.r3 { display: flex; gap: 7px; align-items: center; font: 400 11px var(--f-mono); color: var(--dim); white-space: nowrap; padding-right: 6px; }
	.r3 .ov { font: 500 10px var(--f-label); letter-spacing: 0.12em; text-transform: uppercase; color: var(--l405); }
	.r3 .t { margin-left: auto; }

	/* the member tooltip: 250ms hover delay, name first, then harness › model · effort */
	.tip {
		position: absolute; left: 10px; bottom: calc(100% + 8px); z-index: 40; display: grid; gap: 5px; padding: 9px 12px;
		background: var(--tx); color: var(--on-pri); white-space: nowrap; pointer-events: none; opacity: 0;
		transform: translateY(3px); transition: opacity var(--t-fast), transform var(--t-fast); transition-delay: 0s;
	}
	.tip.below { bottom: auto; top: calc(100% + 8px); }
	.tip b { font: 500 13.5px var(--f-ui); }
	.tip .who { display: flex; align-items: center; gap: 6px; font: 400 11.5px var(--f-mono); opacity: 0.85; }
	.mem:hover .tip, .mem:focus-visible .tip { opacity: 1; transform: none; transition-delay: 250ms; }
	.mem.sel:not(:hover):not(:focus-visible) .tip { opacity: 0; }
</style>
