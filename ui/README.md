# ui

Svelte driver UI over the herdsman daemon. The UI and the CLI are peers over the
same HTTP API — `herdsman run` is itself a client of the daemon
(`herdsman/cli.py`), so the UI submits actions to the same routes rather than
shelling out to anything.

Built by unit, in a fresh session per unit; see `notes/ui-views.md`. Unit **F1**
established the app bootstrap and the visual system; unit **R1** drew the Run
spine on it. Per-unit design decisions live in `.impeccable/surfaces/`; the
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
node dev/field-check.ts   # the Contention Field's model. Run from ui/ or the root.
node ../.claude/skills/impeccable/scripts/detect.mjs --json src/app.css src/routes/+layout.svelte src/routes/run/+page.svelte src/lib/ContentionField.svelte src/lib/field.ts
```

`dev/field-check.ts` asserts the one claim the Run view rests on: that the lanes
it draws are a minimum chain cover, so the lane count really is the plan's
parallelism ceiling and no lane can overlap itself. Node strips the types; there
is no test framework and none is wanted for one file of pure functions.

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
| Proposed run | `/run?plan=ui-r1-proposed` — nothing has run; approval is R3's, not built |
| Dense field | `/run?plan=ui-r1-dense` — sixteen lanes, long names, every member state |
| Contention unread | Stop the daemon between the graph read and the risk read; the field draws without cords and says so |
| Live | Watch the **Stream** readout: `Connecting` → `Live`. Append an event to a seeded plan and the field re-reads without losing selection or focus |

Themes: the control sits in the title block and cycles system → light → dark.
Both themes are real materials, not inversions of one another; check both.

## Screenshots

```sh
node dev/shot.mjs "http://localhost:5173/run?plan=ui-r1-dense" shot.png \
  --width 1440 --full --wait 4000 [--scheme light] [--click "#seat-B12"]
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
