# ui

Svelte driver UI over the herdsman daemon. The UI and the CLI are peers over the
same HTTP API — `herdsman run` is itself a client of the daemon
(`herdsman/cli.py`), so the UI submits actions to the same routes rather than
shelling out to anything.

Built by unit, in a fresh session per unit; see `notes/ui-views.md`. Unit **F1**
established the app bootstrap and the visual system; unit **R1** drew the Run
spine on it; **R2** added the initiative drawer and **R3** the approval gate,
which share the Run view's right-edge slot; **R4** put checkpoint review inside
that drawer and widens it to a reading surface. Per-unit design decisions live in `.impeccable/surfaces/`; the
system itself is in `DESIGN.md`.

## Stack

SvelteKit with `adapter-static` (`fallback: index.html`), `ssr = false`,
`prerender = false` — a single-page app that builds to a plain asset folder.
Confirmed with the owner in F1; see
`.impeccable/surfaces/ui-src-routes-layout-svelte.md` for the alternatives and
why they lost. Type checking is `svelte-check`; the fonts are self-hosted in
`static/fonts/` so a fresh-machine install needs no network.

## Dev launch

Two processes. The daemon owns all state; Vite serves the UI and proxies
`/plans` to the daemon so the browser stays on one origin.

```sh
# 1. the daemon, from the repository ROOT (see the warning below)
uv run herdsman serve                 # http://127.0.0.1:8000

# 2. the UI, from ui/
npm install                           # first time only
npm run dev                           # http://127.0.0.1:5173
```

> **Run the daemon from the repository root.** The event store path is
> `.herdsman/events.db`, resolved relative to the working directory
> (`herdsman/store.py`). Starting the daemon from `ui/` does not fail — it
> silently creates a second, empty store at `ui/.herdsman/`, and every plan you
> seeded then 404s. If plans vanish, check where the daemon was started from and
> delete the stray directory.

Point the proxy elsewhere with `HERDSMAN_DAEMON=http://host:port npm run dev`.

There is no `GET /plans` route on the daemon, so the UI cannot list plans and
offers no picker. A plan is addressed by id: `/run?plan=<id>`.

Navigation is served too: `GET /nav/codemap` (the full `NavIndex` JSON),
`GET /nav/tour`, `GET /nav/flow/{name}`, and `GET /nav/symbol/{name}` (each
a `{text}` envelope; unknown flows/symbols 404). The typed client is
`src/lib/daemon.ts` (`daemon.codemap/tour/flow/symbol`); no view consumes it
yet — it is the seam for future R13/R14 work. Scope is the nav's own: Python
source plus PEP 621 console-script discovery, structural (not type-inferred)
resolution with dynamic/unresolved edges labeled, structural generic tours and
guides, repository-curated named flows/semantic facets, and the optional
codegraph deep probe as a curated cross-check only.

## Real state to develop against

The event store starts empty and creating a plan through the daemon calls a
frontier planner. To get a real plan without spending a model call:

```sh
uv run python ui/dev/seed_plan.py                   # ui-f1-sprint2
uv run python ui/dev/seed_plan.py --shape proposed  # ui-r1-proposed
uv run python ui/dev/seed_plan.py --shape dense     # ui-r1-dense
uv run python ui/dev/seed_plan.py --shape drawer    # ui-r2-drawer
uv run python ui/dev/seed_plan.py --shape gate      # ui-r3-gate
uv run python ui/dev/seed_plan.py --shape checkpoint # ui-r4-checkpoint
```

These write **locally seeded** plans — no model, no harness — through the real
`EventStore`. Everything downstream is genuine: `herdsman serve` folds them with
the real `Plan.fold` and projects them through the real `plan_graph` and
`risk_report`. Never present a seeded plan as planner-authored.

| Shape | What it exercises |
| --- | --- |
| `sprint2` | the golden five: three roots, a diamond, one gated consumer |
| `proposed` | the same plan unapproved — nothing has run, nothing may start |
| `dense` | 27 initiatives over twelve ranks, sixteen lanes, names too long for a cell, every member state at once, seven write conflicts and twenty-nine unordered write/read pairs |
| `gate` | eight initiatives left **unapproved**, shaped for R3: a write/write conflict the lanes permit, an articulation point three members hang off, two unordered write/read pairs, a member that declares no writes, a long multi-paragraph brief, a dependency with no shared path to explain it, a contract requiring review, and recorded planner usage — the only fixture whose callouts and planning cost are non-empty |
| `checkpoint` | six initiatives shaped for R4: three preserved versions of one contract-gated member (v1 approved, built on by a consumer, then **rejected**; v2 a revision awaiting review with a failed required check, a required check that never ran, and a command the contract does not permit), a member whose evidence violates its contract six ways at once, a tainted consumer, a consumer waiting on two producers, two members approved by the automatic policy rather than a reviewer, and a member with no evidence at all |
| `drawer` | six initiatives shaped for R2: a long multi-paragraph brief with an unbreakable path in it, a contract with required checks and a command policy, subtasks in all four states, a settled attempt with harness-reported usage, a failed attempt that was never closed, an attempt herdr never gave a pane, a member with no subtasks, and a member that never ran |

Then open <http://localhost:5173/run?plan=ui-f1-sprint2>.

A seeded plan is immutable once written. To reshape one, delete its events and
re-seed, then **restart the daemon** — it folds a plan once and holds it:

```sh
sqlite3 .herdsman/events.db "DELETE FROM events WHERE plan_id='ui-r1-dense'"
```

## Checks

Run all three before handing off. Each is fast and each has caught something.

```sh
npm run check      # svelte-check: types and a11y. Must be 0 errors, 0 warnings.
npm run build      # adapter-static; also proves the direction contracts survive
node dev/field-check.ts   # the field, gate and review models. Run from ui/ or the root.
../.claude/skills/impeccable/scripts/impeccable detect --json src/app.css src/routes/+layout.svelte src/routes/run/+page.svelte src/lib/ContentionField.svelte src/lib/field.ts src/lib/InitiativeDrawer.svelte src/lib/PlanGate.svelte src/lib/gate.ts src/lib/CheckpointReview.svelte src/lib/review.ts src/lib/daemon.ts
```

R2 added a daemon write (`POST /plans/{id}/initiatives/{iid}/focus`) and the
herdr adapter method behind it, so a change to the drawer's focus path is also a
Python change: run the repository's own gate (`uv run basedpyright`,
`uv run pytest`) and, because it crosses the herdr seam,
`HERDSMAN_TEST_REAL_HERDR=1 uv run pytest -k real_herdr` with the installed
`herdr` CLI.

`dev/field-check.ts` asserts the one claim the Run view rests on: that the lanes
it draws are a minimum chain cover, so the lane count really is the plan's
parallelism ceiling and no lane can overlap itself. Node strips the types; there
is no test framework and none is wanted for two files of pure functions.

It also carries R3's gate model, in the same runner because they are one view's
model: that the critical path is counted in R1's unit, that an unread risk
report yields *unread* callouts rather than none, that only the worst chokepoint
is called out (every node in a tail chain articulates), and that a dependency
with no shared path is reported as *declared* rather than given an invented
reason.

The direction contracts are HTML comments at the top of `<body>` in
`src/app.html` — one per unit that ran a concept round. They must survive into
the production build; a contract the build erases is a contract nobody can audit:

```sh
npm run build && grep -c 0571afe4 build/index.html   # F1, the visual world
npm run build && grep -c c5eeafdc build/index.html   # R1, the Run spine
```

## Checking the states by hand

The shell's async states are reachable without any fixture, which is the point:

| State | How to reach it |
| --- | --- |
| Loading | Open `/run?plan=<id>` with the daemon slow or starting |
| Ready | Daemon up, plan seeded |
| Empty | A plan whose proposal carried no initiatives |
| Error, unreachable | Stop the daemon, then load `/run?plan=<id>` |
| **Stale** | Load successfully, then stop the daemon and press **Read again** — values must stay on screen, marked stale, never blanked |
| Unavailable action | `/run` with no `?plan=` |
| Slack member | `/home`, `/library`, `/kitchen` |
| Proposed run | `/run?plan=ui-r1-proposed` — nothing has run; the gate opens on it |
| Dense field | `/run?plan=ui-r1-dense` — sixteen lanes, long names, every member state |
| Contention unread | Stop the daemon between the graph read and the risk read; the field draws without cords and says so |
| Live | Watch the **Stream** readout: `Connecting` → `Live`. Append an event to a seeded plan and the field re-reads without losing selection or focus |

### The initiative drawer (R2)

Select a member, then **Read this initiative**. All of it is on
`/run?plan=ui-r2-drawer`:

| State | Where |
| --- | --- |
| Long brief, contract, part-done subtasks, live pane | `D1` — running, four subtasks in four different states |
| Settled attempt with real usage and provenance | `D2` — harness-reported, `1h 3m`, exit 0 |
| Failed member, reason not projected | `D3` — the fold drops `InitiativeFailed.reason`, and the drawer says so instead of showing a blank. Reload after a live failure to see the difference: the sentence is there while the stream delivered it, gone after |
| Review required, and no pane to focus | `D4` — `AttemptStarted` recorded `pane_ref=None` |
| No subtasks, no attempts, blocked upstream | `D5` — three stated absences, no zeroes |
| Never ran, nothing in the way | `D6` — ready |
| Plan not approved | `/run?plan=ui-r1-proposed`, any member — the blocking reason is the gate, not the dependencies |
| Focus refused | Press **Focus this pane** on `D1`. The fixture's pane ids are not real, so herdr answers `pane not found` and the drawer reports the failure — a failed write is never shown as success |
| Focus unreachable | Stop `herdr`, then press it: the daemon's own message comes through |

### Checkpoint review (R4)

Second in the drawer's read, under the blocking statement. All of it is on
`/run?plan=ui-r4-checkpoint`:

| State | Where |
| --- | --- |
| Three preserved versions, one withdrawn | `C1` — v1 approved at 3h ago, `C3` ran on it, v1 **rejected** 50m ago with the reviewer's reason; v2 awaits review. Nothing was deleted |
| A required check that never ran | `C1` v2 — `verify-proposed` takes the slack ring and reads *did not run*, which is neither a pass nor a failure |
| A failed required check with its summary | `C1` v2 — `uv run basedpyright`, ring cut open, the two errors named |
| Executor caveats | `C1` v2 — two, and they are the manifest's only executor-written field. No generated handoff prose exists in this build |
| A symmetrical change list | `C1` v2 against v1 — 5 added, 4 carried, 1 no longer touched. The daemon projects only the added half; the dropped path is computed here |
| A tainted consumer | `C1` → downstream — `C3` carries `TAINTED` and the fold's own sentence; `C4` says it also waits on `C2` |
| Contract violations, six at once | `C2` — non-zero exit, missing patch, two missing artifacts, a failed required check, a required check that never ran |
| An approval the daemon refuses | `C2` → **Approve** → **Confirm**. `Daemon.approve_checkpoint` validates before appending, so the write fails, the decision stays pending, and the sheet says *Not approved.* over the daemon's own violation list |
| Reason required to block | `C2` → **Reject**: confirm stays disabled until a reason is written. **Approve** does not require one |
| A verdict that cannot be given | Reject `C2`, then look again — every further verdict is refused, stated as a sentence rather than a dead control |
| Approved by policy, not a person | `C5`, `C3` — "settled automatically on clean evidence — no reviewer was asked" |
| No evidence at all | `C6` — one sentence, and it says *why* there is none |
| The reader | **Expand to read in full**: the sheet widens 30rem → 74rem, every list goes whole, path lists flow into columns, and the section you were reading does not move. Escape collapses the reader before it closes the drawer |

The outcome of a verdict survives the live re-read that verdict triggers — key
the section's reset on `initiative@checkpointId` and **not** on the decision,
or the operator's own write wipes its own confirmation. Same shape as R3's bug,
found the same way: by driving the browser.

### The approval gate (R3)

A proposed revision opens the gate in the same right-edge slot as the drawer.
It opens itself once per plan and revision — a proposed plan's only available
action is the decision — and **Review and approve** on the proposed note reopens
it after you close it. All of it is on `/run?plan=ui-r3-gate`:

| State | Where |
| --- | --- |
| Ranked callouts | `SCOPE` — `G2`/`G3` both write `daemon.py`, the one thing the daemon will refuse; `CHOKEPOINT` — `G4` gates 3 of 8; then two `OVERLAP` entries under `ADVISORY` |
| No callouts at all | `/run?plan=ui-r1-proposed` — a sentence, never a zero |
| Callouts unread | Stop the daemon between the graph read and the risk read: *unread*, and the sheet says approving now approves a decomposition whose contention nobody has seen |
| Planning cost, with provenance | `ui-r3-gate` — 21,519 tokens, harness-reported |
| Planning cost unknown | Any other fixture — `—` and "unknown rather than zero", never `0` |
| A brief with more than shown | `G1` in the register — the lead paragraph, then `…`; the member opens for the rest |
| A member that declares no writes | `G8` |
| A dependency with no shared path | `G5`, `G7`, `G8` — "declared, with no shared path to explain it" |
| Arm | **Approve revision 1** swaps into **Confirm revision 1** / **Cancel**, with the consequence stated. `Escape` cancels the arm before it closes the sheet |
| Approved | Confirm. The store, the title block, the seats and the footer all move together; the outcome survives the live re-read it triggered |
| Refused | Stop the daemon while armed, then confirm: "Not approved." in red over the daemon's own sentence in graphite. The plan stays pending |
| Approved elsewhere | Approve over HTTP while the sheet is open — the control is withdrawn rather than left to fire a doomed write |
| Register into a member | Click any register entry (or a callout's id): R2's drawer opens **over** the gate on the same selection; closing it returns |
| Narrow | Below 60rem the gate takes the whole viewport, as the drawer does. The pinned decision stays on screen |

There is no revise action, and the sheet says so: no daemon route re-proposes a
plan (`POST /plans` mints a different plan id), so a decomposition you do not
want is refused by not approving it. Approval is append-once — `Plan._apply`
refuses a second `PlanApproved` and refuses a stale version, both as 409 — which
is why the confirm sends the version that was actually on screen.

The drawer expands on selection: pick a member in the field or the schedule and
it opens on that initiative. `Escape` and **Close** collapse it without clearing
the selection; selecting the same member again expands it. It is a non-modal
`<aside>`, so the field stays both readable and usable beside it.

Themes: the control sits in the title block and cycles system → light → dark.
Both themes are real materials, not inversions of one another; check both.

## Screenshots

```sh
node dev/shot.mjs "http://localhost:5173/run?plan=ui-r1-dense" shot.png \
  --width 1440 --full --wait 4000 [--scheme light] [--click "#seat-B12"]

# --click repeats, in order; selecting a member expands the drawer
node dev/shot.mjs "http://localhost:5173/run?plan=ui-r2-drawer" drawer.png \
  --height 1500 --click "#row-D1"
```

Do **not** use `brave --headless --screenshot --virtual-time-budget`. The Run
view holds an open server-sent-events stream, and virtual time never elapses
while a network task is pending: the browser hangs until something kills it and
writes no file. `dev/shot.mjs` drives the browser over the DevTools protocol
instead, so the capture happens on a clock we control with the stream still
connected — which is the state the view is actually in. `--click` captures
selection, which is half of what this view does.

Two traps that produce false passes, both seen for real:

- **A stale file is not a failed capture.** Check the timestamp and size of
  every file you write, and open it. A screenshot loop that dies partway leaves
  the previous run's images in place, looking like a result.
- **Vite may bind IPv6 only** (`[::1]`), so `127.0.0.1` refuses the connection
  while `localhost` works, and it silently moves to 5174/5175 when a stale dev
  server still holds 5173. Read the port it prints.

Review evidence belongs in `.impeccable/review/`.
