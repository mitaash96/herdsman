---
kind: skill
name: code-navigation
title: >-
  Answer "where does this live and how does execution move through it" from
  `herdsman nav`'s offline evidence instead of re-reading the repository.
references: []
---

Use this before grepping a repository you do not already hold in context, and
before asking anyone to explain its layout. Every command below is offline,
deterministic, and prints `file:line` citations you can open directly.

## Commands

- `herdsman nav codemap` — modules, entry points, and the edges the resolver
  could not settle. Add `--json` when you want to filter it with a tool.
- `herdsman nav tour` — a guided walk with citations and comprehension
  checkpoints; the starting point when the repository is new to you.
- `herdsman nav flow <name>` — trace one named end-to-end flow across modules.
- `herdsman nav symbol <name>` — one symbol's callers, callees, tests, and
  labeled edges. Run this before renaming or deleting anything.
- `herdsman nav guide` — report the generated guide's freshness; `--refresh`
  regenerates it.

## How to read the output

Structural, not type-inferred. The resolver walks the standard library's `ast`,
so a dynamic receiver stays **labeled unresolved** rather than being guessed at.
Treat an unresolved edge as "look here yourself", never as "no edge exists".

Generic tours and guides are structural; named flows and semantic facets are
curated per repository, so a flow name that does not exist means nobody has
curated it yet — not that the code has no such path.

## Scope

Python source plus PEP 621 console-script entry points. There is no
universal-language promise and no complete call graph. Anything outside that —
another language, a runtime-only dispatch, a generated file — needs your own
reading, and the codemap will say so rather than inventing an answer.
