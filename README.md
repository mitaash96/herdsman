# Herdsman

**Local orchestration for coordinated AI coding agents across CLI harnesses.**

> [!WARNING]
> Herdsman is in pre-alpha development. The orchestration workflow and browser UI described below are the product direction, not a usable release yet.

Herdsman is a meta-harness for developers who use multiple AI agent CLIs. It turns a brief into a plan of independent initiatives, assigns each initiative to a configured agent, and supervises the resulting work without taking ownership away from the underlying harnesses.

The primary audience is solo developers running several coding agents on one machine. Small teams that want repeatable, reviewable agent workflows are a secondary audience.

## Why Herdsman?

Running more agents does not automatically produce better results. Work can overlap, agents can receive too much context, and a failed handoff can be difficult to diagnose or recover.

Herdsman is designed around explicit coordination:

- **Plans and dependencies** describe what can run in parallel and what must wait.
- **Roles and contracts** define what each agent receives and must return.
- **Checkpoint handoffs** move evidence and artifacts between initiatives.
- **Supervised worktrees and panes** keep concurrent efforts isolated through `herdr`.
- **Deterministic operations** handle routine status, gates, recovery, and accounting without spending model tokens.

The pipeline is the product; the model and CLI harness used for each role are configuration.

## Intended workflow

1. Initialize Herdsman inside an existing project.
2. Create a plan from a brief and review its dependency graph, assignments, risks, and budget.
3. Approve the plan and run ready initiatives concurrently in isolated worktrees.
4. Review checkpoint evidence, intervene when needed, and approve gated handoffs.
5. Recover or recalibrate unfinished work without repeating completed work.

Herdsman is strictly additive: project-local files are allowed, but global agent-harness configuration is never modified.

### First project-local Kitchen setup (pre-alpha)

The first-run path uses one configured harness with two explicit model assignments:
use the frontier model for planning and a cheaper model for initiative execution.
For example, create `.herdsman/kitchen.json` in the project (the command below is
illustrative; use the executable and model names installed locally):

```json
{
  "version": 1,
  "adapters": [
    {
      "name": "pi",
      "argv": ["pi", "--print", "{prompt}"],
      "model_argv": ["--model"]
    }
  ],
  "models": [
    {"harness": "pi", "model": "frontier-model"},
    {"harness": "pi", "model": "cheap-model"}
  ],
  "tiers": {"pi/frontier-model": "frontier", "pi/cheap-model": "cheap"},
  "defaults": {
    "planner": {"harness": "pi", "model": "frontier-model"},
    "initiative": {"harness": "pi", "model": "cheap-model"}
  }
}
```

The daemon exposes `GET /kitchen` for the current project-local projection,
`POST /kitchen/discovery` for a read-only version/health refresh, and `PUT /kitchen`
for a validated declaration update. Updates include the `expect_revision` returned by
`GET /kitchen`; stale revisions are refused rather than overwritten. Discovery and
setup only read harnesses or write `.herdsman/kitchen.json`—they never modify global
harness configuration, credentials, installation, or integration setup. An optional
`context_warning_tokens` field (default 2000) sets the Library's per-initiative
effective-context warning threshold used at plan approval. This is a
pre-alpha development path, not a production-ready setup guide.

### Library authoring (Sprint 9)

Roles, contracts, skills, agents, checkpoint templates, and memory leaves are
authorable project-local assets. Bundled assets are read-only; editing one
creates a project-local override under `.herdsman/library/` (copy-on-edit),
and every read prefers that copy.

```sh
herdsman library browse --kind skill --query nav          # active shelf
herdsman library browse --status stale,conflicted --kind memory-leaf
herdsman library show skill/navigate
herdsman library create skill navigate --title "Navigate" --body "run herdsman nav codemap"
herdsman library edit skill/navigate      # opens $EDITOR, records the revision
herdsman library copy skill/navigate follow-navigate
herdsman library rename skill/follow-navigate nav
herdsman library archive skill/nav
herdsman library unarchive skill/nav
herdsman library validate skill/navigate role/implementer --owner init_a
herdsman library watch                     # follow the revision stream
```

The same surface lives on the daemon API: `GET/POST /library`,
`GET/PUT /library/{kind}/{name}`, and `POST
/library/{kind}/{name}/{checkout,copy,rename,archive,unarchive}`, plus
`GET /library/revision`, `GET /library/events` (SSE), and `POST
/library/validate`. Writes carry `expect_digest` (the revision you read); a
stale write is refused with `409` instead of overwritten. A terminal
`$EDITOR` session is the v1 authoring path: `herdsman library edit` checks
out the asset, opens the configured editor (argv, never a shell), and
records the resulting revision.

## Current status

The substrate is implemented and tested:

- event-sourced domain models with deterministic projection;
- project-local SQLite persistence;
- plan DAG validation;
- a narrow adapter for `herdr` runtime and worktree operations;
- a minimal FastAPI/SSE daemon surface; and
- basic Typer commands for serving and inspecting stored plans and events.
- offline `herdsman nav` code navigation plus daemon nav projections reusing the
  same evidence, with a typed UI client but no nav view yet.

There is no supported installation or end-user quick start yet. The next milestone is the first complete single-initiative flow: create, approve, run, checkpoint, and settle.

## Roadmap

- [x] **Substrate proof** — durable event log, projections, DAG validation, `herdr` adapter, and event streaming.
- [ ] **Golden thread** — project initialization and one initiative running end to end through planning, approval, execution, and checkpoint settlement.
- [ ] **Multi-agent plans** — concurrent dependency graphs, scoped worktrees, contention warnings, critical-path analysis, and artifact handoffs.
- [ ] **Contracts and control** — typed role contracts, checkpoint gates, approval policies, retry, reassignment, redirect, and live intervention.
- [ ] **Recovery and observability** — restart reconciliation, idempotent actions, bounded retry context, token provenance, budgets, and overhead reporting.
- [ ] **Product configuration** — harness discovery, model assignments, reusable role/skill assets, shared project memory, and fleet attention views.
- [ ] **Driver UI and automation** — Svelte views for planning, supervision, review, CLI parity for the golden path, replay, and policy-bounded unattended runs.
- [ ] **v1 release** — fresh-machine install, diagnostics, packaged assets and UI, a two-minute demo, and published evaluation receipts.

### Planned beyond v1

- In-browser asset editing
- Hosted and remote runtimes
- Multi-tenant accounts
- Broader agent protocol support
- Asset marketplace and remote registry
- Evidence-based assignment recommendations

Cloud execution and remote control are deliberately out of scope for v1.

## Design goals

- Never modify global harness configuration.
- Keep orchestration token overhead at or below 20% on the reference workflow, backed by a reproducible evaluation before release.
- Send each agent only the context required by its contract.
- Preserve plans, attempts, checkpoints, interventions, and failures as auditable events.
- Make a fresh-machine install and first run understandable to someone outside the project.

## Development

Herdsman currently requires Python 3.14+ and uses [`uv`](https://docs.astral.sh/uv/) for the development environment.

```console
uv sync
uv run pytest
uv run basedpyright
uv run herdsman --help
```

The live `herdr` integration test is skipped unless a compatible local `herdr` server is available.

### Navigate the code

From a clean checkout, one offline command (no daemon, no model call) generates the
architecture guide and makes `herdsman nav` usable:

```console
uv sync --locked --no-dev && .venv/bin/herdsman nav guide --refresh
```

The guide lands in `.herdsman/nav/guide.md` (gitignored); `herdsman nav tour`,
`herdsman nav codemap`, `herdsman nav flow create-approve-run-settle`, and
`herdsman nav symbol <name>` navigate it. Dev variant: `uv sync --locked --all-groups
&& uv run herdsman nav guide --refresh`.

Navigation scope: every repository Python file (hidden, cache, virtualenv, and
build trees excluded) plus PEP 621
`[project.scripts]` console-script discovery only. Resolution is structural
(stdlib `ast`), not type-inferred: dynamic receivers and unresolvable edges stay
visibly labeled, never dropped. Generic tours and guides stay structural;
named flows and semantic facets are repository-curated. The optional `guide
--deep` codegraph probe only cross-checks curated Herdsman symbols and never
merges — there is no universal-language or complete-call-graph promise.

The same evidence is served to the UI by the daemon (`GET /nav/codemap`,
`GET /nav/tour`, `GET /nav/flow/{name}`, `GET /nav/symbol/{name}`); the typed
client is `ui/src/lib/daemon.ts`, with no nav view yet. CLI access stays offline
and standalone — no daemon, no model call, no network — while the daemon
projections reuse that evidence for the UI and future contract checks.

### Repository layout

```text
herdsman/  Python package, daemon, domain model, and runtime adapter
assets/    Bundled agent, role, and skill assets
ui/        Browser UI prototypes; the Svelte app is not scaffolded yet
tests/     Python tests
```

## License

No license has been selected. The source is publicly viewable, but no permission is granted to use, modify, or distribute it.
