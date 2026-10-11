# Recovery

Herdsman records the plan event history and attempt metadata in the project's
`.herdsman/events.db`. A daemon restart or machine interruption does not make a
completed attempt runnable again. Recovery reconciles what survived, then leaves
new work to an explicit run or retry command.

## Daemon stopped or crashed

Start the project daemon and inspect the plan:

```sh
herdsman up
herdsman recovery PLAN_ID
herdsman resume PLAN_ID
```

`resume` reattaches surviving panes and collects work completed while the daemon
was down. Missing panes become auditable failures. It does not re-drive pending
or failed initiatives; after reviewing the result, use `herdsman run
INITIATIVE_ID` for a pending initiative, `herdsman run-plan PLAN_ID` for ready
work, or `herdsman retry INITIATIVE_ID` for a failed attempt.

If the herdr server is unavailable, fix that service first and rerun `doctor`.
Use `herdsman resume PLAN_ID --assume-missing` only when you have deliberately
decided that stale panes are gone; it force-closes them without probing herdr.

## Preserve evidence before deciding

```sh
herdsman salvage PLAN_ID
herdsman checkpoints PLAN_ID
herdsman status PLAN_ID
```

The salvage report lists attempts, retained worktrees, failure reasons, evidence
paths, failed checks, and repeated failure signatures. `herdsman salvage PLAN_ID
--write` asks the daemon to author failure leaves from that evidence.

To intervene in a live run, use `pause`, `nudge`, `redirect`, `reassign`,
`restart INITIATIVE_ID`, or `cancel`. Disruptive actions show their impact and
may prompt for confirmation; pass `--yes` only when the action is understood.

## Final landing blocked

Settlement is final even when the target cannot be advanced. Inspect
`herdsman --format text status PLAN_ID` or fleet attention, resolve the
block, then run `herdsman land PLAN_ID`. The same retry runs at daemon startup,
not in an automatic loop.

| Reason | Recovery |
| --- | --- |
| `dirty_checkout` | Commit or stash tracked changes on the target yourself; remove any obstructing untracked files, then retry. |
| `checked_out_elsewhere` | Release the target from the other linked worktree, then retry. |
| `symbolic_target` | Restore the recorded target to a direct branch ref; Herdsman never follows symbolic targets. |
| `conflict` | Preserve the checkpoint evidence. Target merge conflicts need operator reconciliation; patch-replay/diamond overlaps need the deferred commit-based integration, not patch heuristics. |
| `git_error` | Fix the reported Git/ref/identity error, then retry. A repeated ref CAS miss stops after one recomposition. |

The base pin is `refs/herdsman/plans/<id>/base`. Before writing the target,
Herdsman pins its landing commit at `refs/herdsman/plans/<id>/landed`.
If the daemon dies after the target write but before its event append,
startup recognizes the target at that pin and records the landing without
writing again. Commit trailers retain the write intent for this repair.
Pins (including orphan base pins after failed planning) are not pruned here.
Recovery never resets, checks out, or stashes the root working tree.

## Local diagnostics and cleanup

```sh
herdsman doctor
herdsman doctor --fix
herdsman migrate
herdsman repair
herdsman prune
herdsman prune --apply
```

`doctor` is read-only unless `--fix` is supplied; fixes are limited to stale
runtime files and the local schema. `migrate` applies pending event-store schema
versions. `repair` checks SQLite and checkpoints disposable WAL state; it does
not rewrite damaged event history. `prune` previews disposable derived files and
never deletes event history, even with `--apply`.

If `repair` reports damaged event history, preserve the project directory and
restore `events.db` from your backup. Do not delete it while diagnosing a run.

## Finish a run cleanly

After reviewing and archiving a completed plan, stop the daemon with
`herdsman down`. Herdsman's generated state is project-local. Herdr worktrees
created for attempts are retained when evidence needs review and are removed
through the Herdsman discard/recovery path after that review.
