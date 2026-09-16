---
name: herdr-delegate
description: >-
  Delegate a coding lane to a foreign-harness agent (Claude Code, Codex, ...)
  inside a herdr-managed git worktree and supervise it to completion from
  outside herdr: create the lane worktree, launch the agent pane, submit a
  prompt, wait for completion, and read its machine-readable report. Use when
  an orchestrating agent must hand real implementation work to another harness
  CLI rather than doing it itself or spawning same-harness subagents. NOT for
  inspecting or controlling herdr from inside a herdr pane (that is the
  upstream `herdr` skill), and not for same-harness delegation.
---

# Herdr delegate: run a foreign-harness lane

Drive a second agent CLI through herdr: isolated worktree, supervised pane,
prompt submitted atomically, completion detected without a human relay.

The five steps below are the whole recipe. Everything after them is reference
material you consult when a step misbehaves.

| Step | Goal | Primary command |
| --- | --- | --- |
| [1](#1-reserve-the-lane-worktree) | Reserve the lane | `herdr worktree create` |
| [2](#2-launch-the-foreign-agent) | Launch the agent | `herdr agent start` |
| [3](#3-wait-for-boot-then-submit) | Submit the prompt | `herdr agent send` + `herdr pane send-keys enter` |
| [4](#4-wait-for-completion) | Detect completion | `herdr wait output --match "<LANE>_DONE:"` |
| [5](#5-verify-and-integrate) | Verify and merge | `git log` in the worktree |

## Preconditions

Check all four before step 1. Each failure has a different fix, so do not
collapse them into one "is herdr working" glance.

1. **The binary is the authority.** Run `herdr --version` and confirm
   subcommand syntax with `herdr <group>` (the group name with no subcommand
   prints its usage). This recipe is verified against **herdr 0.7.2**; see
   [Version differences](#version-differences) for 0.9.x.
2. **The server is up.** `herdr status` reports client and server. Client and
   server versions can differ after an update — a missing method is a version
   skew, not permission to stop or restart a server.
3. **The target harness has an integration hook.** Lifecycle states (`idle`,
   `working`, `blocked`, `done`) come from herdr's per-harness integration, not
   from guessing at terminal output. `herdr integration` lists the installable
   hooks (`claude`, `codex`, `pi`, `opencode`, ...); install the one for the
   harness you are delegating to, or every wait in steps 3 and 4 degrades to
   `unknown`.
4. **You are outside herdr, or you accept that you are not.** This skill drives
   panes you create. If `HERDR_ENV=1` is set you are running *inside* a herdr
   pane, and the upstream `herdr` skill governs inspecting the session you are
   part of. Delegation still works from inside — just never target the pane you
   are running in.

## Standing rules

These hold for every step; violating one corrupts a lane or the user's session.

- **Parse every id from JSON.** Never guess pane ids like `w15:p2` — closed ids
  are not reused, and examples lie.
- **One writer per lane worktree.** Two agents in one worktree is data loss.
- **Never push or merge to `master`.** The lane commits on its own branch; the
  orchestrator owns integration.
- **Never auto-answer a permission or approval dialog.** A `blocked` agent needs
  the user, not you.
- **Do not close workspaces, tabs, panes, or agents you did not create.** Never
  run `herdr server stop` or kill the main herdr process.
- **Do not probe a mutating command by omitting its arguments.** `herdr agent`
  prints usage; `herdr workspace create` is valid with defaults and *executes*.
  Bare `herdr` launches or attaches the TUI — never run it for discovery.
- **CLI errors are structured.** Server errors are JSON on stderr with exit
  status 1; syntax errors exit 2.

## Identifiers and lifecycle states

Public ids are opaque, stable handles: workspace `w1`, tab `w1:t1`, pane
`w1:p1`. A pane moved to another workspace gets a *new* workspace-qualified id.

Agent targets accept a unique live agent name or the pane id currently hosting
that agent — not terminal ids and not bare harness labels. Names must match
`[a-z][a-z0-9_-]{0,31}`, must be unique among live agents, follow the current
pane occupant, and are cleared when that agent exits or is replaced.

| State | Meaning | What it does *not* mean |
| --- | --- | --- |
| `idle` | Ready for input | Not that the work succeeded |
| `working` | A turn is in progress | — |
| `blocked` | herdr recognized an approval or question UI | — |
| `done` | Ready for input, completion not yet seen by the server | Not that the work succeeded |
| `unknown` | An agent is present but herdr cannot classify it | **Not** proof of completion |

`idle` and `done` both mean "ready for input"; they differ only in the server's
seen-state, and explicit focus commands mark a target seen while reads do not.
Neither carries a verdict — that is what the step 4 sentinel is for.

## 1. Reserve the lane worktree

```bash
herdr worktree create --cwd <repo> --branch <branch> --base <ref> \
  --path <worktree-path> --label "<lane label>" --no-focus --json
```

Parse ids from the JSON response: `.result.root_pane.pane_id` and
`.result.workspace.id`. If the worktree already exists, use
`herdr worktree open --path <path>` (or `--branch <name>`) rather than creating
a second one.

`--no-focus` is not optional for background lanes: never steal the user's focus.

## 2. Launch the foreign agent

Confirm the harness binary and its flags first — the flags below are a starting
point, not a contract:

```bash
<harness> --help | grep -E 'model|permission|dangerously'
```

Then launch into a split off the lane's root pane:

```bash
herdr agent start <name> --cwd <worktree-path> --workspace <id> \
  --split right --no-focus -- <harness> <flags...>
```

- `<name>` encodes lane and harness, e.g. `claude-s8-core`, and must satisfy the
  naming rule above.
- Native harness arguments go **after** `--`. Everything before `--` is herdr's.
- Split a wide pane `right` and a narrow or tall pane `down`; check with
  `herdr pane layout --pane <id>`. Repeated same-direction splits produce
  unusably narrow columns.

| Harness | Status | Invocation |
| --- | --- | --- |
| Claude Code | Fully proven | `claude --model <model> --permission-mode acceptEdits` (add `--effort <level>` when wanted) |
| Codex | Shape proven, flags unverified | `codex` + flags confirmed against `codex --help` |
| Others (gemini-cli, opencode, ...) | Unverified | Same shape; verify against `--help` on first use and record what worked |

Never pass `--dangerously-skip-permissions` (or a harness equivalent) without
explicit user instruction.

A successful start returns the agent record with the `pane_id` used everywhere
below; `herdr agent get <name>` returns the same. That record also carries
`agent_session` — the herdr-detected session id, which proves the expected agent
occupies the pane rather than a shell that happens to be there.

Startup has its own timeout. If the agent is blocked during boot, the command
returns `agent_not_ready` immediately but **keeps the name reserved**, so
`herdr agent read` and `herdr pane send-keys` still work for diagnosis.

## 3. Wait for boot, then submit

The agent must reach `idle` before it can accept a prompt:

```bash
herdr wait agent-status <pane> --status idle --timeout 120000
```

Write the full prompt to a temp file — this avoids shell quoting problems on
long lane briefs — and submit it.

> **`herdr agent send` writes literal text only. It does not press Enter.**
> For TUI agents like Claude Code the text sits in the input box until
> submitted. Always chain the keypress in the same command.

```bash
herdr agent send <pane> "$(cat /tmp/<lane>-prompt.txt)" \
  && herdr pane send-keys <pane> enter
```

Then confirm the turn actually started:

```bash
herdr wait agent-status <pane> --status working --timeout 120000
```

If it returns `blocked` instead, the agent is showing an approval or question
dialog. Read it and surface it:

```bash
herdr agent read <pane> --source recent-unwrapped --lines 120
```

A timeout or a stalled submission **does not prove the prompt was never
delivered.** Read the pane before resending; duplicate submits corrupt lanes.

## 4. Wait for completion

Lifecycle state alone is not a verdict — `idle` only proves the agent stopped
talking. Require the lane prompt to end with a sentinel line carrying the
verdict and commit ref:

```
<LANE>_DONE: PASS|FAIL commit=<sha> summary=<one line>
```

Wait for that sentinel directly, then read the surrounding report:

```bash
herdr wait output <pane> --match "<LANE>_DONE:" --timeout 600000
herdr agent read <pane> --source recent-unwrapped --lines 120
```

`--match` is a literal substring; `--regex` takes a Rust regular expression.

> **The wait searches the current snapshot immediately**, so output that already
> exists can match. Choose a sentinel that cannot appear in the prompt you sent,
> or you will match your own instructions instead of the agent's report.

When no sentinel was agreed, fall back to two states: wait for `--status idle`
(or `done`), then read the final report — and treat the verdict as unverified
until step 5. For long lanes, poll `herdr agent get <pane>` for transitions
before deciding; a `blocked` status mid-run means the agent needs an answer, and
only the user provides it.

## 5. Verify and integrate

Trust nothing the agent claims without checking the worktree:

```bash
cd <worktree-path> && git status --short --branch && git log -1 --stat
```

Confirm the sentinel's commit sha exists and the branch is where you left it.
Then the orchestrating side owns the merge — cherry-pick lane commits, ff-merge
the branch, or hand off to review. **The delegate lane never merges, pushes, or
touches branches outside its own worktree.**

## Reading pane output

| Source | Contents | Use for |
| --- | --- | --- |
| `visible` | The currently rendered viewport | Seeing what the user sees |
| `recent` | Recent rendered output, including soft wraps | Quick glances |
| `recent-unwrapped` | Recent output with soft wraps joined | **Default** — logs and transcripts |
| `detection` | Plain-text bottom-buffer snapshot used for agent detection (0.9.x) | Debugging misdetection |

Add `--format ansi` only when colors or styling are the evidence; otherwise text.

`--lines` asks herdr for more rows from the pane's screen and host scrollback.
**Alternate-screen rows never enter ordinary scrollback**, which is why a
full-screen TUI agent can produce an empty read. If a larger read still does not
reveal the response, ask the agent to write its report as Markdown to a temp
path and reply with only that path, then read the file. Use this as a fallback
only — do not bake file output into the initial prompt.

## Remote lanes (herdr 0.9.x)

To run a lane on a saved SSH machine, use the same `--machine` prefix for
discovery *and* every later command:

```bash
herdr --machine <label-or-id> agent list
herdr --machine <label-or-id> agent prompt <name> "<text>" --wait --timeout 120000
```

The selector is an enabled saved profile id or a unique, case-sensitive label —
not an arbitrary SSH hostname. Inherited local ids and `--current` do not
identify remote panes, so discover ids on that machine. Do not combine
`--machine` with `--session` or `--remote`. Remote worktree paths must be
absolute, `~`, or start with `~/`. A connection failure does not prove a
mutation was not applied: inspect remote state before retrying.

## Version differences

This recipe targets the installed **0.7.2** CLI. On **0.9.x** the surface moves:

| Concern | 0.7.2 (this recipe) | 0.9.x |
| --- | --- | --- |
| Submit a prompt | `agent send` + `pane send-keys enter` | `agent prompt <target> "<text>" --wait [--timeout MS]` — one ordered write, honors bracketed paste |
| Launch | `agent start <name> --split right --workspace <id> -- <argv>` creates the pane | `agent start <name> --kind <kind> --pane <id>` requires an existing idle shell pane and never creates layout — split first with `pane split` |
| Lifecycle wait | `wait agent-status <pane> --status <s>` | `agent wait <target> --until <s>`; bare `agent wait` uses settled-state defaults |
| Output wait | `wait output <pane> --match <text>` | `pane wait-output <pane> --match <text>` |
| Keys | `pane send-keys <pane> <key>` | also `agent send-keys <target> <key>` |
| Remote | not available | `--machine <label-or-id>` |
| Git trust | not available | `--trust-repository`, for a user-verified repo only — not a routine retry |

Prefer `agent prompt --wait` wherever it exists: it submits text and Enter as one
ordered write, rejects an already-`blocked` agent with `agent_blocked` before
sending anything, and returns `agent_prompt_stalled` if no `working` or `blocked`
activity follows submission. It replaces step 3's two commands; the rest of this
recipe is unchanged. Successful submission still does not prove a turn started —
step 4 remains the verdict.

## Failure table

| Symptom | Cause | Fix |
| --- | --- | --- |
| Prompt sits in the TUI input box | `agent send` does not submit | `herdr pane send-keys <pane> enter` (step 3 chains it) |
| `agent_not_ready` at start | Agent blocked during boot | Name stays reserved; `agent read` + `agent get` before any retry |
| `agent_blocked` on prompt (0.9.x) | Approval or question dialog already open | Read the pane, surface to the user, never auto-answer |
| `blocked` during a wait | Approval or question dialog | Same as above |
| `agent_prompt_stalled` (0.9.x) | No `working`/`blocked` activity after submit | Read the pane; do not blindly resubmit |
| `wait output` matches instantly | The snapshot already contained the sentinel — usually your own prompt text | Pick a sentinel that cannot appear in the prompt |
| `wait output` times out | Sentinel missing, or the agent is on the alternate screen | `agent read --source recent-unwrapped`; if empty, ask the lane to write its report to a temp file and read that |
| `unknown` status | herdr cannot classify the occupant — often a missing integration hook | Not proof of completion; `herdr integration install <harness>`, then verify with `pane process-info` and a read |
| Pane id stopped resolving | The pane was moved and re-keyed | Use `.result.move_result.pane.pane_id` or the live agent name |
