<script lang="ts">
	import type { Field, Member, Touch } from './field';

	let {
		field,
		contention,
		contentionRead,
		selected,
		onselect
	}: {
		field: Field;
		contention: Map<string, Touch[]>;
		/** False when the risk report could not be read: cords are unknown, not absent. */
		contentionRead: boolean;
		selected: string | null;
		onselect: (id: string) => void;
	} = $props();

	/* Past this the id marks stop fitting between the rules, so the field keeps
	   the rings and the schedule below carries every name. */
	const dense = $derived(field.columns > 12);

	const x = (m: Member) => m.depth + 0.5;
	const y = (m: Member) => m.lane + 0.5;

	/** Horizontal out of `a`, then down into `b`'s column. */
	const elbow = (a: Member, b: Member) =>
		a.lane === b.lane
			? `M${x(a)} ${y(a)}H${x(b)}`
			: `M${x(a)} ${y(a)}H${x(b) - 0.42}V${y(b)}H${x(b)}`;

	/** A pair relation, not a flow: it drops out of one lane and into the other. */
	const bracket = (a: Member, b: Member) => {
		const mid = (x(a) + x(b)) / 2;
		return `M${x(a)} ${y(a)}H${mid}V${y(b)}H${x(b)}`;
	};

	interface Cord {
		key: string;
		d: string;
		kind: 'edge' | 'conflict' | 'suggested' | 'critical';
		ends: [string, string];
		title: string;
	}

	const cords = $derived.by(() => {
		const drawn: Cord[] = [];
		for (const { from, to } of field.crossings)
			drawn.push({
				key: `e:${from.node.initiative_id}>${to.node.initiative_id}`,
				d: elbow(from, to),
				kind: 'edge',
				ends: [from.node.initiative_id, to.node.initiative_id],
				title: `${to.node.initiative_id} depends on ${from.node.initiative_id}`
			});

		const seen = new Set<string>();
		for (const [id, touches] of contention) {
			const a = field.byId.get(id);
			if (!a) continue;
			for (const touch of touches) {
				const b = field.byId.get(touch.peer);
				if (!b || a.lane === b.lane) continue; // same lane: already ordered, no contention
				const pair = [id, touch.peer].sort().join('|');
				if (seen.has(`${pair}:${touch.kind}`)) continue;
				seen.add(`${pair}:${touch.kind}`);
				// A conflict is a hard limit on the concurrency the lanes promise, so
				// it is always drawn. A missing edge is advisory and there are many of
				// them -- one shared file can suggest a dozen -- so they draw for the
				// member you are reading. The schedule lists every one, always.
				if (touch.kind !== 'write_write' && !(selected === id || selected === touch.peer)) continue;
				drawn.push({
					key: `c:${pair}:${touch.kind}`,
					d: bracket(a, b),
					kind: touch.kind === 'write_write' ? 'conflict' : 'suggested',
					ends: [id, touch.peer],
					title:
						touch.kind === 'write_write'
							? `${id} and ${touch.peer} both write ${touch.paths.join(', ')}; they cannot run at the same time`
							: `${touch.paths.join(', ')} is written by one of ${id}, ${touch.peer} and read by the other, with no dependency between them`
				});
			}
		}
		return drawn;
	});

	const criticalRun = $derived(
		field.criticalPath.length < 2
			? ''
			: field.criticalPath
					.map((m, i) => (i === 0 ? `M${x(m)} ${y(m)}` : elbow(field.criticalPath[i - 1], m).slice(1)))
					.join('')
	);

	const incident = (cord: Cord) => selected !== null && cord.ends.includes(selected);

	/* The one tab stop into the drawing. It must never be the selected id alone:
	   a revision that drops that initiative would leave the field with no
	   tabbable seat at all, which is a keyboard trap. */
	const anchor = $derived(
		selected !== null && field.byId.has(selected)
			? selected
			: (field.members[0]?.node.initiative_id ?? null)
	);

	/* Selection follows focus: moving through the field is how you read it, and
	   nothing here is destructive. */
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
		const touches = contention.get(m.node.initiative_id) ?? [];
		const conflicts = touches.filter((t) => t.kind === 'write_write').length;
		return [
			`${m.node.initiative_id}, ${m.node.name}`,
			`${m.cancelled ? 'cancelled' : m.node.state}`,
			m.node.state === 'pending' ? (m.node.ready ? 'ready to run' : `waiting on ${m.blockedBy.join(', ')}`) : '',
			`lane ${m.lane + 1}, earliest rank ${m.depth}`,
			m.onCriticalPath ? 'on the critical path' : '',
			conflicts ? `${conflicts} write conflict${conflicts > 1 ? 's' : ''}` : ''
		]
			.filter(Boolean)
			.join('. ');
	}
</script>

<div class="wrap">
	<div
		class="frame"
		style="--cols: {field.columns}; --lanes: {field.lanes.length}; --lane: {field.lanes
			.length > 10
			? '2.25rem'
			: field.lanes.length > 6
				? '2.75rem'
				: '3.25rem'}"
		role="group"
		aria-label="Contention field: {field.members.length} initiatives in {field.lanes
			.length} lanes. Arrow keys move between members."
	>
		<div class="corner" style="grid-row: 1">
			<span class="label">Rank</span>
		</div>
		{#each Array(field.columns) as _, rank (rank)}
			<div class="rank" style="grid-row: 1; grid-column: {rank + 2}">{rank}</div>
		{/each}

		{#each field.lanes as lane, index (index)}
			<div class="lane-cell" style="grid-row: {index + 2}">
				<span class="label">Lane {String(index + 1).padStart(2, '0')}</span>
				{#if lane.some((m) => m.onCriticalPath)}
					<span class="lane-note critical-note">{lane.length} · critical</span>
				{:else if lane.length > 1}
					<span class="lane-note">{lane.length} in sequence</span>
				{/if}
			</div>
		{/each}

		<div class="plot" aria-hidden="true">
			<svg
				viewBox="0 0 {field.columns} {field.lanes.length}"
				preserveAspectRatio="none"
				class:reading={selected !== null}
			>
				{#each field.lanes as lane, index (index)}
					<line class="ruling" x1="0" y1={index + 0.5} x2={field.columns} y2={index + 0.5} />
					<line
						class="run"
						x1={x(lane[0]) - 0.2}
						y1={index + 0.5}
						x2={x(lane[lane.length - 1]) + 0.2}
						y2={index + 0.5}
					/>
				{/each}
				{#each cords as cord (cord.key)}
					<path class="cord {cord.kind}" class:incident={incident(cord)} d={cord.d}>
						<title>{cord.title}</title>
					</path>
				{/each}
				{#if criticalRun}
					<path class="cord critical" d={criticalRun} />
				{/if}
			</svg>
		</div>

		{#each field.members as m (m.node.initiative_id)}
			<button
				id="seat-{m.node.initiative_id}"
				class="seat member"
				data-state={m.state}
				class:cancelled={m.cancelled}
						class:conflicted={(contention.get(m.node.initiative_id) ?? []).some(
					(t) => t.kind === 'write_write'
				)}
				style="grid-row: {m.lane + 2}; grid-column: {m.depth + 2}"
				type="button"
				aria-current={selected === m.node.initiative_id ? 'true' : undefined}
				aria-label={describe(m)}
				tabindex={m.node.initiative_id === anchor ? 0 : -1}
				onclick={() => onselect(m.node.initiative_id)}
				onkeydown={(event) => onkeydown(event, m)}
			>
				<span class="ring" aria-hidden="true"></span>
				{#if !dense}<span class="seat-mark" aria-hidden="true">{m.node.initiative_id}</span>{/if}
			</button>
		{/each}
	</div>

	<p class="narrow-note">
		At this width the field draws shape only — the rings carry state and position,
		not names. Selecting one marks it here and names it below; the schedule names
		every member.
	</p>

	<ul class="states" aria-label="Member states">
		{#each [['seated', 'Settled'], ['loaded', 'Running'], ['balanced', 'Ready'], ['slack', 'Blocked'], ['failed', 'Failed']] as [state, word] (state)}
			<li class="member" data-state={state}>
				<span class="ring" aria-hidden="true"></span><span class="state-word">{word}</span>
			</li>
		{/each}
	</ul>

	<dl class="key">
		<div><svg viewBox="0 0 24 6"><path class="cord-key run" d="M0 3H24" /></svg><dt>Lane run</dt>
			<dd>a chain: these cannot overlap each other</dd></div>
		<div><svg viewBox="0 0 24 6"><path class="cord-key critical" d="M0 3H24" /></svg><dt>Critical path</dt>
			<dd>the plan's floor on wall-clock time</dd></div>
		<div><svg viewBox="0 0 24 6"><path class="cord-key edge" d="M0 3H24" /></svg><dt>Dependency</dt>
			<dd>crosses lanes; within a lane the run carries it</dd></div>
		<div><svg viewBox="0 0 24 6"><path class="cord-key conflict" d="M0 3H24" /></svg><dt>Write conflict</dt>
			<dd>{contentionRead ? 'both write one path and may not run together' : 'unread — the risk report did not answer'}</dd></div>
		<div><svg viewBox="0 0 24 6"><path class="cord-key suggested" d="M0 3H24" /></svg><dt>Missing edge</dt>
			<dd>{contentionRead ? 'one writes what the other reads, unordered — drawn for the member you select' : 'unread — the risk report did not answer'}</dd></div>
	</dl>
</div>

<style>
	.wrap {
		margin: 1.5rem 0 0;
	}

	/* One grid rules the whole drawing: the rail, the rank marks, the lanes and
	   every seat land on the same lines, and the cords are painted over it. */
	.frame {
		--head: 1.5rem;
		--lane: 3.25rem;
		display: grid;
		grid-template-columns: 9rem repeat(var(--cols), minmax(0, 11rem)) minmax(0, 1fr);
		grid-template-rows: var(--head) repeat(var(--lanes), var(--lane));
		position: relative;
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
	}

	.corner,
	.rank {
		grid-row: 1;
		align-self: end;
		padding-bottom: 0.3rem;
		border-bottom: 1px solid var(--rule);
	}
	.corner {
		grid-column: 1;
		padding-right: 0.75rem;
	}
	.rank {
		font-size: 0.625rem;
		letter-spacing: 0.1em;
		color: var(--ink-2);
		text-align: center;
	}

	/* The rail: each lane's label rides a leader that runs into the field. */
	.lane-cell {
		grid-column: 1;
		display: flex;
		flex-direction: column;
		justify-content: center;
		gap: 0.1rem;
		padding-right: 0.75rem;
		border-right: 1px solid var(--rule);
	}
	.lane-cell .label {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.lane-cell .label::after {
		content: '';
		flex: 1;
		min-width: 0.5rem;
		height: 1px;
		background: var(--rule);
	}
	.lane-note {
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-2);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.critical-note {
		color: var(--ink);
	}

	.plot {
		grid-column: 2 / span var(--cols);
		grid-row: 2 / -1;
		position: relative;
		pointer-events: none;
	}
	.plot svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		overflow: visible;
	}

	/* Stroke widths and dash patterns stay in real pixels under the non-uniform
	   viewBox, so a wide plan does not draw fatter members than a narrow one. */
	.plot :is(line, path) {
		fill: none;
		vector-effect: non-scaling-stroke;
		stroke-linecap: butt;
		stroke-linejoin: miter;
	}
	.ruling {
		stroke: var(--rule);
		stroke-width: 1;
		stroke-dasharray: 1 5;
	}
	.run {
		stroke: var(--member-line);
		stroke-width: 1.25;
	}
	.cord.edge {
		stroke: var(--rule-strong);
		stroke-width: 1.25;
	}
	.cord.critical {
		stroke: var(--ink);
		stroke-width: 2.5;
	}
	.cord.conflict {
		stroke: var(--red);
		stroke-width: 1.5;
		stroke-dasharray: 1 4;
	}
	.cord.suggested {
		stroke: var(--ash);
		stroke-width: 1.25;
		stroke-dasharray: 3 3;
	}
	/* Reading one member: its own cords stay drawn, the rest fall back to paper. */
	svg.reading .cord:not(.incident):not(.critical) {
		opacity: 0.25;
	}

	.seat {
		position: relative;
		z-index: 1;
		display: flex;
		align-items: center;
		gap: 0.4rem;
		justify-content: center;
		min-width: 0;
		padding: 0 0.25rem;
		font: inherit;
		background: none;
		border: 0;
		color: var(--member-ink);
		cursor: pointer;
	}
	/* The field is drawn on the sheet plate, so a member knocks out of the run in
	   plate -- knocking out in ground would leave a halo a shade too dark. */
	.ring {
		flex: none;
		width: 11px;
		height: 11px;
		border: 1.5px solid currentColor;
		border-radius: 50%;
		background: var(--plate);
	}
	.seat[data-state='slack'] .ring {
		border-style: dashed;
	}
	.seat[data-state='loaded'] .ring {
		background: var(--red);
	}
	.seat[data-state='seated'] .ring {
		background: var(--seat);
	}
	/* Ready is the one state an operator acts on, and a dashed border against a
	   solid one is invisible at 11px. A charged member carries a pip: seated in
	   the structure, not yet carrying load. */
	.member[data-state='balanced'] .ring::before {
		content: '';
		position: absolute;
		inset: 2px;
		border-radius: 50%;
		background: currentColor;
	}
	.member[data-state='balanced'] .ring {
		position: relative;
	}
	/* A discontinuous load path: the ring is cut open on two sides. */
	.seat[data-state='failed'] .ring {
		border-left-color: transparent;
		border-right-color: transparent;
	}
	/* Struck out of the structure. Not a colour: a line through the member. */
	.cancelled .ring {
		background: linear-gradient(
			to bottom right,
			transparent calc(50% - 1px),
			currentColor calc(50% - 1px),
			currentColor calc(50% + 1px),
			transparent calc(50% + 1px)
		);
	}
	/* Location, in carbon, exactly as the strut marks the view you stand in. */
	.seat[aria-current='true'] .ring {
		box-shadow:
			0 0 0 3px var(--plate),
			0 0 0 4px var(--member-line);
	}
	.conflicted .ring::after {
		content: '';
		position: absolute;
		margin: 0.75rem 0 0 2px;
		width: 4px;
		height: 1.5px;
		background: var(--red);
	}
	.conflicted .ring {
		position: relative;
	}
	.seat-mark {
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--ink);
		background: var(--plate);
		padding: 0 0.25rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.seat[data-state='slack'] .seat-mark {
		color: var(--ink-2);
	}
	.seat:hover .seat-mark {
		color: var(--red);
	}

	.narrow-note {
		display: none;
		margin: 1.25rem 0 0;
		max-width: 68ch;
		color: var(--ink-2);
	}

	/* The key. A drawing that needs decoding without one is a puzzle, and a key
	   that documents the cords but not the members is half a key. */
	.states {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem 1.75rem;
		margin: 1.25rem 0 0;
		padding: 0;
		list-style: none;
	}
	.states li {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		color: var(--member-ink);
	}
	.states .ring {
		width: 11px;
		height: 11px;
		border: 1.5px solid currentColor;
		border-radius: 50%;
		background: var(--plate);
	}
	.states [data-state='loaded'] .ring {
		background: var(--red);
	}
	.states [data-state='seated'] .ring {
		background: var(--seat);
	}
	.states [data-state='slack'] .ring {
		border-style: dashed;
	}
	.states [data-state='failed'] .ring {
		border-left-color: transparent;
		border-right-color: transparent;
	}
	.state-word {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink);
	}
	/* Ash draws slack and never sets text; the dashed ring carries the state. */
	.states [data-state='slack'] .state-word {
		color: var(--ink-2);
	}

	.key {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
		gap: 0.5rem 1.75rem;
		margin: 1rem 0 0;
		padding-top: 1rem;
		border-top: 1px solid var(--rule);
	}
	.key > div {
		display: grid;
		grid-template-columns: 1.75rem minmax(0, 1fr);
		align-items: baseline;
		column-gap: 0.6rem;
	}
	.key svg {
		grid-row: 1 / 3;
		align-self: center;
		width: 1.75rem;
		height: 0.5rem;
		overflow: visible;
	}
	.cord-key {
		fill: none;
		vector-effect: non-scaling-stroke;
	}
	.cord-key.run {
		stroke: var(--member-line);
		stroke-width: 1.25;
	}
	.cord-key.critical {
		stroke: var(--ink);
		stroke-width: 2.5;
	}
	.cord-key.edge {
		stroke: var(--rule-strong);
		stroke-width: 1.25;
	}
	.cord-key.conflict {
		stroke: var(--red);
		stroke-width: 1.5;
		stroke-dasharray: 1 4;
	}
	.cord-key.suggested {
		stroke: var(--ash);
		stroke-width: 1.25;
		stroke-dasharray: 3 3;
	}
	.key dt {
		font-size: 0.625rem;
		font-weight: 500;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink);
	}
	.key dd {
		margin: 0;
		color: var(--ink-2);
	}

	@media (max-width: 60rem) {
		.frame {
			--lane: 2.75rem;
			/* Every rem the rail gives up widens the seats, which are the tap targets. */
		grid-template-columns: 4rem repeat(var(--cols), minmax(0, 11rem)) minmax(0, 1fr);
		}
		.lane-note {
			display: none;
		}
		.seat-mark {
			display: none;
		}
		.seat {
			padding: 0;
		}
		.narrow-note {
			display: block;
		}
	}
</style>
