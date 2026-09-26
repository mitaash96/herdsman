---
kind: contract
name: implementer
title: Implementer gate: every change inside the declared writes
role: implementer
---
Settles only with every changed path inside the initiative's declared write
routes, a zero exit, and every check that ran passing. Add the
project's own checks under `required_checks` in a project
copy (for example `uv run pytest -q`); the bundled gate cannot know them.
