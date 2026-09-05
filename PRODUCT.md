# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python daemon (orchestration, state, handoff documents) + Svelte driver UI + herdr
(terminal multiplexer supervising agent panes). Confirmed in `AGENTS.md` and
`pyproject.toml`; `ui/` is not scaffolded yet but Svelte is already the committed choice.

## Users

Primary: a solo developer on their own machine, running several AI agent CLIs
(Claude Code, pi, others) across their own initiatives. This user's situation wins
when a tradeoff comes up.

Secondary: small teams wanting a shared, repeatable orchestration layer over their
existing agent tooling. Real but not the deciding audience.

## Product Purpose

Herdsman is a meta-harness that sits between the developer and existing agent CLIs.
It decomposes a brief into a plan — a DAG of independent initiatives — and assigns
each initiative to a chosen agent via configuration, so efforts stop colliding and
the developer controls which agent does what.

Success: a reliable end-to-end run across multiple harnesses, and a full working
product installable on a fresh machine.

## Positioning

The pipeline is the product — roles, their contracts, and the documents they hand each
other. Which model or harness runs which role is configuration, not identity. A
neighboring tool that wraps a single CLI, or that treats agents as interchangeable
workers without contracted handoffs, cannot truthfully claim this.

## Operating Context

- The driver UI runs in a browser **beside** the terminal — a separate window, second
  screen or split, while herdr drives the agent panes in the terminal. The UI is not an
  overlay on the terminal and does not embed the panes.
- The user does all of the following with that UI, not a subset: composes the pipeline
  (roles, contracts, assignments), dispatches an initiative, supervises the run live,
  intervenes when work stalls or a handoff goes bad (redirect, retry, reassign), and
  comes back later to review results and approve handoffs. Authoring, live control, and
  review are equally first-class — no surface may assume one at the cost of another.
- Work moves between roles as handoff documents; those documents are what the user
  reads, judges, and acts on.
- v1 targets desktop and narrow-desktop operation first, with a readable,
  accessible small-screen fallback — not a separate phone-optimized supervision
  experience. The responsive UI grants no remote access; remote control stays out
  of scope.

## Capabilities and Constraints

- In scope: multi-harness orchestration, work decomposition and assignment, the driver UI.
- Out of scope (planned, far-off): cloud runtimes, remote control, a Rust rewrite.
- **Strictly additive**: Herdsman never tampers with global harness configurations.
- Token efficiency is a leading design principle, and applies to this repository's own
  code as well as to the orchestration it performs.
- Markdown assets ship with the package: `assets/agents/`, `assets/skills/`,
  `assets/roles/` (handoff document templates). All are currently empty placeholders.
- Implementation status: the substrate and core graph are implemented and landed on
  `uat` — Gate 0, Sprint 1 (golden thread), Sprint 2 (multi-agent DAG), and Effort A
  (offline `herdsman nav` code navigation). `herdsman/cli.py` implements the
  create/run/approve/review and `nav` commands over the daemon (`herdsman/daemon.py`,
  a FastAPI app exposing the plan lifecycle). `ui/` is scaffolded as of 2026-09-06:
  unit F1 landed the SvelteKit application shell — four-view routing, both themes,
  the type and status system, and the async-state patterns — against the real
  Sprint 2 projection. No view's feature content exists yet; each is its own unit
  in `notes/ui-views.md`. Everything later —
  contracts/checkpoint gates, token economics, interventions, recovery,
  recalibration, memory, Kitchen/Library/Home substrates, release — is unimplemented;
  the authoritative plan is `notes/working-note-herdsman.md`.
- Open decision: the architecture is subject to redesign; nothing in the current
  prototype fixes it.

## Brand Commitments

Name: **Herdsman**. No logo, wordmark, voice guide, or identity constraint has been
established. Do not invent one as if it were confirmed.

## Evidence on Hand

- `notes/product.md` — product brief (source of the problem statement, scope, constraints).
- `notes/public-repo-readme-guidance.md` — launch/README practices gathered for the
  public release, with sources.
- `ui/schedule-view.html` — a self-contained prototype of the initiative DAG and lane
  view, carrying a light/dark token set and a "Contention design" lineage.
  **Its visual identity was replaced by the owner in unit F1 on 2026-09-06** and is
  now anti-reference, not authority: read it for product evidence (the DAG and
  contention concepts, the state vocabulary) and never for look and feel. The
  driver UI's visual system is `DESIGN.md`. Note the file does not render as
  shipped — it is a template awaiting an injected `__DATA__` whose generator
  (`scripts/schedule_view.py`) is not in this repository.
- No customers, testimonials, benchmarks, pricing, press, or usage data exist. Future
  work must not fabricate any.

## Product Principles

1. **The pipeline is the product.** Roles, contracts, and handoff documents are the
   thing being offered; agent/model choice is configuration.
2. **Strictly additive.** Never modify what the user's other harnesses own.
3. **Token efficiency is a design constraint**, in the product and in this repo.
4. **One surface, three jobs.** Composing, supervising, and reviewing are equally
   first-class; the UI cannot optimize for one and degrade the others.
5. **Installable by a stranger.** This is headed for a public open-source release
   pre-launch, so a fresh-machine install and a credible first impression are product
   requirements, not polish.

## Accessibility & Inclusion

No product-specific requirement established. Standard baselines apply; nothing beyond
them has been confirmed.
