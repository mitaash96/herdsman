# ui

Svelte driver UI over the herdsman daemon. The UI and the CLI are peers over the
same HTTP API — `herdsman run` is itself a client of the daemon
(`herdsman/cli.py`), so the UI submits actions to the same routes rather than
shelling out to anything.

Built by unit, in a fresh session per unit; see `notes/ui-views.md`. Unit **F1**
established the app bootstrap and the visual system. Per-unit design decisions
live in `.impeccable/surfaces/`; the system itself is in `DESIGN.md`.

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

## Real state to develop against

The event store starts empty and creating a plan through the daemon calls a
frontier planner. To get a real plan without spending a model call:

```sh
uv run python ui/dev/seed_plan.py     # prints the plan id
```

This writes a **locally seeded** plan — no model, no harness — through the real
`EventStore`. Everything downstream is genuine: `herdsman serve` folds it with
the real `Plan.fold` and projects it through the real `plan_graph` and
`risk_report`. Never present a seeded plan as planner-authored.

Then open <http://127.0.0.1:5173/run?plan=ui-f1-sprint2>.

## Checks

Run all three before handing off. Each is fast and each has caught something.

```sh
npm run check      # svelte-check: types and a11y. Must be 0 errors, 0 warnings.
npm run build      # adapter-static; also proves the direction contract survives
node ../.claude/skills/impeccable/scripts/detect.mjs --json src/app.css src/routes/+layout.svelte
```

The direction contract is an HTML comment at the top of `<body>` in
`src/app.html`. It must survive into the production build — a contract the build
erases is a contract nobody can audit:

```sh
npm run build && grep -c 0571afe4 build/index.html   # expect 1
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

Themes: the control sits in the title block and cycles system → light → dark.
Both themes are real materials, not inversions of one another; check both.

## Screenshots

Headless capture, if you need evidence for a review:

```sh
brave --headless --disable-gpu --hide-scrollbars --virtual-time-budget=3500 \
  --window-size=1440,900 --screenshot=desktop.png \
  "http://127.0.0.1:5173/run?plan=ui-f1-sprint2"
# add --blink-settings=preferredColorScheme=1 for the light theme
```

Review evidence belongs in `.impeccable/review/`.
