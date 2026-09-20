# First run

This guide uses one configured harness for both planning and execution. The
configuration is project-local and is written under `.herdsman/`; Herdsman does
not edit a harness's global configuration.

## Prerequisites

Install Python 3.14 or newer, `uv`, the `herdr` 0.9.1 CLI, one authenticated
agent CLI, and Node.js 22.12+ with npm when you want the bundled browser UI.
Use a Git repository with at least one commit as the project root; herdr creates
attempt worktrees from that repository. Start the herdr local server and check
it before installing Herdsman:

```sh
herdr --version
herdr status
```

The adapter is pinned to herdr 0.9.1 and protocol 22. A drift is reported as a
warning so diagnostics can explain it; the operation still validates each
response.

## Install from a wheel

For a source checkout that should ship the browser UI, build the UI first and
then the wheel:

```sh
cd ui
npm ci
npm run build
cd ..
uv build --wheel
uv tool install dist/herdsman-0.0.1-py3-none-any.whl
herdsman --help
```

Use the actual filename emitted in `dist/` when the version differs. An API-only
wheel is valid when `ui/build` is absent; `herdsman up` reports that mode.

## Initialize and configure a project

Run these commands from the project you want Herdsman to coordinate:

```sh
herdsman init
herdsman config edit
herdsman config validate
herdsman doctor
```

In the Kitchen document, declare the installed harness, its command template,
the model(s) it accepts, and `defaults.planner` and `defaults.initiative`.
The [README's one-harness example](../README.md#running-real-agents) shows the
shape. `herdsman config show` prints the effective project-local document;
`herdsman config discover` performs read-only health/version discovery through
the daemon.

## Start the daemon and run a plan

`up` runs in the foreground. Keep it running in one terminal:

```sh
herdsman up
```

Use another terminal in the same project for the remaining commands:

```sh
herdsman open --no-browser       # prints the local URL in headless sessions
herdsman create "Add a focused change and verify it with the repository checks."
herdsman review PLAN_ID
herdsman approve PLAN_ID
herdsman run-plan PLAN_ID
```

`create` returns a plan document containing the plan ID. Use that ID in the
following commands. The browser UI and CLI read the same daemon projections;
`herdsman status PLAN_ID`, `herdsman checkpoints PLAN_ID`, and `herdsman wait
PLAN_ID` are useful from a second terminal. A checkpoint must be reviewed and
approved before a gated dependent is released.

When a run reaches a checkpoint, list its IDs, inspect the evidence, approve the
checkpoint, and resume the plan:

```sh
herdsman checkpoints PLAN_ID
herdsman checkpoint CHECKPOINT_ID approve --plan-id PLAN_ID
herdsman run-plan PLAN_ID
```

Repeat the approval and run commands for each checkpoint that `checkpoints` reports as
waiting for review. `checkpoint ... approve` is the explicit human handoff gate;
`run-plan` does not bypass it. `herdsman wait PLAN_ID` waits for settlement;
it does not approve outstanding checkpoints for you.

For the bundled graph, use `herdsman demo --dry-run` to inspect D1/D2 in
parallel and gated D3 without contacting herdr. `herdsman demo` runs it after
the project defaults are configured; the demo has no CLI assignment flags and
uses `defaults.initiative` (or `defaults.planner`) from Kitchen. The demo
requires the human checkpoint approval gates; no measured runtime or overhead
claim is implied by the demo.

After `demo` returns, use its `plan_id` to list the checkpoints. Review and
approve **both** D1 and D2 checkpoints before running the plan again to release
D3. Review D3's resulting checkpoint too if it requests approval. A failed check
requires investigation before approval; see [Recovery](recovery.md).

Stop the local daemon when finished:

```sh
herdsman down
```

`init`, `up`, and `down` are idempotent. `open` never starts a missing daemon;
`restart` with no ID means daemon restart, while `restart INITIATIVE_ID` means
restart that live task process.
