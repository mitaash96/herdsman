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

## Current status

The substrate is implemented and tested:

- event-sourced domain models with deterministic projection;
- project-local SQLite persistence;
- plan DAG validation;
- a narrow adapter for `herdr` runtime and worktree operations;
- a minimal FastAPI/SSE daemon surface; and
- basic Typer commands for serving and inspecting stored plans and events.

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

Herdsman currently requires Python 3.11+ and uses [`uv`](https://docs.astral.sh/uv/) for the development environment.

```console
uv sync
uv run pytest
uv run basedpyright
uv run herdsman --help
```

The live `herdr` integration test is skipped unless a compatible local `herdr` server is available.

### Repository layout

```text
herdsman/  Python package, daemon, domain model, and runtime adapter
assets/    Bundled agent, role, and skill assets
ui/        Browser UI prototypes; the Svelte app is not scaffolded yet
tests/     Python tests
```

## License

No license has been selected. The source is publicly viewable, but no permission is granted to use, modify, or distribute it.
