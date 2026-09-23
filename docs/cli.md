# CLI automation contract

Herdsman's CLI is the headless control surface for the same daemon API used by
the browser UI. Routine reads, waits, configuration changes, and operator
actions do not call a model.

## Global behavior

```sh
herdsman [-C PROJECT] [--format text|json|ndjson] COMMAND ...
```

- `-C/--project` selects a project explicitly. Otherwise Herdsman walks upward
  to the nearest `.herdsman/` directory, then the nearest Git root.
- `json` is the default. Structured query and action results are key-sorted and
  contain one JSON value. `ndjson` emits each item in a top-level list on its
  own line; a single object remains one line. `text` is deterministic plain
  text or indented JSON. Commands such as `init`, `up`, and `library` unchanged
  text are not promised to be JSON.
- `events` remains NDJSON and `watch` remains the daemon's SSE stream.
- `create -` and `redirect ... --brief -` read the brief from stdin.
- `library edit` and `config edit` use `$EDITOR` without a shell. Config edits
  validate before atomically replacing `.herdsman/kitchen.json`.
- `open` opens the recorded daemon in a browser; `--no-browser` and headless
  sessions print the URL. Commands exposing `--yes` (such as `retry`, `cancel`,
  `redirect`, and `reassign`) prompt unless `--yes` is passed; pre-existing
  `settle` and `discard` do not prompt.

## Project lifecycle

```sh
herdsman init
herdsman up [--host HOST] [--port PORT]   # --port 0 chooses a free port
herdsman open [PLAN_OR_INITIATIVE_OR_CHECKPOINT] [--no-browser]
herdsman down
herdsman restart                          # daemon: down, then up
herdsman restart INITIATIVE_ID            # task: re-issue its live process
```

`init`, `up`, and `down` are idempotent. `up` holds the project writer lock,
serves the packaged SPA when present, and otherwise serves the API alone.
Herdr version drift is a warning, not a startup failure. `open` never starts a
missing daemon.

`restart` is arity-dispatched: no positional ID restarts the daemon; one ID
keeps the task-process restart behavior. It is not a task retry.

Maintenance is project-local:

```sh
herdsman doctor [--fix]
herdsman migrate
herdsman repair
herdsman prune [--apply]
herdsman demo [--dry-run]
```

`doctor` is read-only unless `--fix` is passed; fixes are limited to stale
runtime files and the local schema. `repair` checkpoints SQLite but never
rewrites damaged event history. `prune` previews disposable derived files by
default and never deletes events. `demo --dry-run` prints the bundled
D1/D2-parallel, D3-gated graph without contacting herdr. With no Kitchen
assignment, demo uses `claude-code/claude-opus-5`.

Shell completion is provided by Typer:

```sh
herdsman --show-completion
herdsman --install-completion
```

IDs accepted by the Sprint 11 query and action commands may be unique prefixes.
Exact matches win; ambiguous prefixes fail instead of choosing arbitrarily.

## Stable exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Command succeeded; a wait target settled. |
| `1` | Validation failed or a stale navigation result was detected. |
| `2` | Invalid CLI usage, lookup error, invalid configuration, or daemon connection/API error. |
| `3` | Attention exists (`attention` or `wait --attention`). |
| `4` | A waited target failed or was cancelled. |
| `5` | `wait` timed out. |

JSON is written to stdout. Confirmation previews and errors are written to
stderr, so scripts can parse stdout safely.

## Golden-path commands and API parity

| CLI | Daemon API / local source |
| --- | --- |
| `create`, `review`, `approve`, `run`, `run-plan` | Plan lifecycle (`/plans/...`) |
| `show ID` | Local event fold; plan, initiative/task, attempt, or checkpoint |
| `fleet [--archived|--all]` | `GET /fleet`, `/fleet/archived` |
| `status PLAN`, `tokens PLAN`, `events PLAN`, `watch PLAN` | Plan observability routes |
| `attention [--blocking-only]` | `GET /fleet/attention` or `/fleet/notifications` |
| `while-away [--since ISO8601]` | `POST /while-away` |
| `wait ID`, `wait --attention` | Polls deterministic status/attention projections |
| `deep-link ID` | Canonical Run link from `herdsman.fleet.deep_link` |
| `archive-plan`, `unarchive-plan` | `POST /plans/{id}/archive`, `/unarchive` |
| `checkpoints PLAN` | `GET /plans/{id}/checkpoints` |
| `checkpoint ID approve|reject|changes` | Checkpoint review routes |
| `retry`, `restart ID`, `reassign`, `redirect`, `nudge`, `answer`, `focus` | Run UI intervention routes |
| `init`, `up`, `open`, `down`, `restart` | Project-local daemon lifecycle |
| `doctor`, `migrate`, `repair`, `prune` | Project-local diagnostics and maintenance |
| `demo` | Bundled three-initiative first run |
| `recovery`, `resume`, `salvage`, `discard` | Recovery routes |
| `packet`, `packet-diff` | Persisted packet inspector routes |
| `config show|get|set|edit|validate` | Project-local Kitchen document |
| `config discover` | `POST /kitchen/discovery` |
| `library ...` | Library browse/authoring routes |
| `nav ...` | Offline Effort A structural navigation evidence |

The browser remains visual-only for graph layout, drawers, charts, and side-by-side
review presentation. It has no exclusive frequent state-changing action.
Terminal pane content also remains in herdr rather than being duplicated in the
CLI or browser.

Kitchen responses changed with the smoke probe. Every route that returns
Kitchen state — `GET /kitchen`, `POST /kitchen/discovery` (what
`config discover` prints), and `PUT /kitchen` — serves adapters without their
`argv`/`model_argv` launch templates and carries `smoke` (`results` per
harness/model pair plus an explicit `absence` sentence when none has run).
Launch templates never reach the wire; `PUT /kitchen` keeps one by omission:
an adapter sent without a template keeps the stored one, an explicit template
replaces it, `expect_revision` is always required (`428` when absent, `409`
when stale), and a credential-shaped replacement is refused without echoing
the value. The file-level commands — `config show|get|set|edit|validate` — go
through no route: they read and write `.herdsman/kitchen.json` directly, so
their output still includes launch templates.

## Deliberate v1 limits

The full parity-matrix long tail is post-v1. `herdsman ask` is also deferred by
Sprint 11's own initial-delivery cut: the standalone CLI has no honest Serena or
codegraph service contract to invoke headlessly. Existing `herdsman nav`
commands provide deterministic, model-free code navigation in the meantime.
`POST /kitchen/smoke`, the bounded model-consuming adapter probe behind the
Kitchen view, likewise has no `herdsman` command in v1: every call spends a
model turn, and its prompt is fixed by the daemon, so a command that spends
tokens needs its own design pass before the parity long tail.
Visual DAG rendering and convenience aliases are intentionally absent.
