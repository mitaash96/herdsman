---
name: herdsman-nav
description: >-
  Use the shipped `herdsman nav` CLI to understand and navigate this
  repository's Python source without reading files wholesale: module map,
  guided tour, end-to-end flow traces, per-symbol caller/callee reports, and a
  written guide with freshness checks. Use before tracing code "how does X
  work", before citing code in reports, or whenever the task mentions herdsman
  nav. Needs no daemon, model call, or network. NOT for herdr lane management
  (see herdr-delegate) and not for editing code.
---

# Herdsman nav: understand the source without reading it all

`herdsman nav` is an offline code-navigation CLI bundled with Herdsman. It
parses the working tree with the Python `ast` module on every invocation, so
reads always reflect live source — no stale index. Every output line carries a
`file.py:LINE` citation, suitable for quoting directly into reports.

Source of truth: `herdsman/nav.py` (extraction + renderers) and
`herdsman/cli.py` (`nav_app`, the typer wiring).

## Commands at a glance

Run from the repository root. No daemon required.

| Command | Gives you | Cost heuristic |
| --- | --- | --- |
| `herdsman nav codemap` | Every module with role note, LOC, top symbols | One screen; start here |
| `herdsman nav tour` | Five ordered steps through the core, with checkpoints | One screen |
| `herdsman nav flow create-approve-run-settle` | Cross-module trace of the main write path | One screen |
| `herdsman nav symbol <name>` | One symbol: signature, callers, callees, tests, labeled edges | Small; your drill-down |
| `herdsman nav guide` | Freshness status of the written guide (`.herdsman/nav/guide.md`) | Cheap |
| `herdsman nav guide --refresh [--deep]` | Regenerate the guide artifact | Write; `--deep` adds a read-only optional codegraph cross-check |

Exit code 1 from `herdsman nav guide` (no flag) means the guide is missing or
stale — regenerate or ignore (the guide is never required; the direct reads
above are always correct).

## Recommended workflow

1. **Orient** with `herdsman nav codemap`. It lists curated one-line module
   roles (`Fact:` = sourced claim, `Interpretation:` = inference) and the
   symbols per module, telling you which file to open before you grep anything.
2. **Learn the core path** with `herdsman nav tour` and
   `herdsman nav flow create-approve-run-settle`. The tour ends with
   comprehension checkpoints; the flow is the full create→approve→run→settle
   trace across `herdsman/cli.py`, `herdsman/daemon.py`, `herdsman/runtime.py`,
   `herdsman/classes.py`, and `herdsman/store.py`.
3. **Drill down** with `herdsman nav symbol <name>`. A bare simple name
   (e.g. `Plan.step`) works when unambiguous; a `module:qualname` path
   (e.g. `herdsman.daemon:Daemon.create_plan`) disambiguates. Output includes
   linked tests that cover the symbol.
4. **Persist orientation** with `herdsman nav guide --refresh` when you want a
   written artifact under `.herdsman/nav/guide.md` to re-read later instead of
   re-running commands. This is the only file the tool writes.

This is the whole tool. There is no configuration, no cache to invalidate, and
no state to manage beyond the optional guide file.

## Token-conscious habits

- Quote the `file.py:LINE` citations into your plan instead of pasting source —
  this is what downstream lanes use to jump straight to code.
- Reach for `herdsman nav symbol` before `codemap` when you already know the
  name: it is the smallest output that answers "who calls this".
- `codemap --json` and `guide --deep` outputs are for machine reading or
  completeness checks; skip them unless writing tooling or a report needs it.
- If you have a codegraph index already installed, `herdsman nav guide --deep`
  adds a caller/callee cross-check for a few curated symbols — it never
  downloads, indexes, or mutates anything (it runs `npx --no-install`).

## Edge labels

`herdsman nav symbol` labels every edge with how it was proven:

| Label | Meaning | Trust for citations |
| --- | --- | --- |
| `static` | Proven by AST: direct name, module import, `self`/`cls` method | Cite freely |
| `dynamic` | Unproven receiver resolved by name match | Cite with the label noted |
| `external` | Crosses the package boundary (subprocess, socket, path) | Real and expected — noted, not a defect |
| `unresolved` | Could not resolve; listed under the Unresolved heading | Name moved or truly dynamic — verify by reading |

## When nav cannot help

The index is `*.py` plus `pyproject.toml` only: no JavaScript templates in
`ui/`, no markdown. Unresolved names list nothing about linked tests. For
anything outside the Python tree, go straight to the file.
