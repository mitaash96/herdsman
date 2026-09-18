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
- The CLI never opens the browser. Commands exposing `--yes` (such as `retry`,
  `cancel`, `redirect`, and `reassign`) prompt for confirmation unless `--yes`
  is passed; pre-existing `settle` and `discard` do not prompt.

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
| `retry`, `restart`, `reassign`, `redirect`, `nudge`, `answer`, `focus` | Run UI intervention routes |
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

## Deliberate v1 limits

The full parity-matrix long tail is post-v1. `herdsman ask` is also deferred by
Sprint 11's own initial-delivery cut: the standalone CLI has no honest Serena or
codegraph service contract to invoke headlessly. Existing `herdsman nav`
commands provide deterministic, model-free code navigation in the meantime.
Visual DAG rendering and convenience aliases are intentionally absent.
