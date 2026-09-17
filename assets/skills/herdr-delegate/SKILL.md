---
name: herdr-delegate
description: >-
  Delegate a coding lane to a foreign-harness agent (Claude Code, Codex, ...)
  in a herdr-managed worktree. Reuse the worktree's root pane (never split for
  its first agent), submit with agent prompt --wait, and consume completion
  without polling. Use for real cross-harness delegation, not same-harness
  subagents or inspecting the orchestrator's own herdr pane.
---

# Herdr delegate — 0.9.1

**One worktree → its existing root pane → one agent → one blocking doorbell.**
Do not create a second pane. Do not monitor progress in a loop.

## Preconditions and boundaries

- Run `herdr --version`, `herdr status`, and `herdr integration status` once.
  This recipe targets **0.9.1**. Verify flags with `herdr <group> <command>
  --help`; do not try the obsolete 0.7.2 commands if it fails.
- Both client and server must support this recipe. Version skew is a blocker,
  not permission to restart the server. Never stop/kill the user's server.
- The target harness needs a working herdr lifecycle integration. Missing or
  outdated hooks need owner-approved setup; do not modify global harness
  configs. `unknown` is not completion. Resolve detection before dispatch.
- Confirm the harness executable, authentication, model and native flags before
  creating a lane. Do not bypass permission prompts or sandboxing without
  explicit authorization.
- One writer per worktree. The lane commits only on its own branch; it never
  pushes, merges, or touches `master`. The parent owns integration into `uat`.
- Parse IDs from JSON; never predict them. If running inside herdr, exclude
  `HERDR_PANE_ID` from every mutation. Only control resources you created.
- Bare `herdr` opens the TUI. Never use mutating commands without arguments for
  discovery: `workspace create` with no arguments actually creates a workspace.

## 1. Create the worktree; keep its root pane

```bash
created=$(herdr worktree create --cwd <absolute-repo> --branch <lane-branch> \
  --base <approved-ref> --path <absolute-worktree-path> \
  --label "<lane label>" --no-focus)
printf '%s\n' "$created"
```

Record `.result.root_pane.pane_id` as the lane pane. Inspect the returned
workspace object for its workspace ID and record the worktree path as well.
Creation responses are JSON; no `--json` flag is needed.

**Worktree creation already creates the workspace, tab, and shell pane.**
The pane is not a disposable placeholder: it is where the agent goes.
Do **not** run `pane split`, `tab create`, or `workspace create` after this.
Splitting is only for an explicitly requested *additional* terminal, not for
launching the first agent. Never close the root pane to compensate for an
unnecessary split.

If resuming an existing lane, use `worktree open --path <path>` and inspect its
returned panes/occupants before launching; do not create a duplicate agent.

## 2. Start in exactly that pane

```bash
herdr agent start <lane-name> --kind claude --pane <root-pane-id> \
  --timeout 120000 -- --model <model> --permission-mode acceptEdits
```

For Codex, use `--kind codex` and flags checked against `codex --help`.
`--kind` supplies the executable: arguments after `--` are **flags only**, not
another `claude`/`codex` command. Names match `[a-z][a-z0-9_-]{0,31}` and must be
unique among live agents.

`agent start` never creates layout. It requires an idle shell and returns only
when the expected agent is ready in that same terminal. Verify the returned
`.result.agent.pane_id` equals the saved root pane. No extra boot-status wait
or split is needed. If startup reports `agent_not_ready`, inspect the reserved
name once and surface any approval dialog; do not launch another instance.

## 3. Submit and let the doorbell ring

Write the brief to a temporary file. Include scope, cwd/ref, allowed files,
dependencies, non-goals, model/effort, validation ownership, stop rules, and a
final report containing verdict, commit SHA, changed files, checks and risks.
Then submit **once** from the newly ready agent:

```bash
herdr agent prompt <lane-name> "$(cat /tmp/<lane>-prompt.txt)" \
  --wait --timeout 1800000
```

**This command is the doorbell.** It submits text plus Enter atomically, checks
for activity, then waits for `idle`, `done`, or `blocked`. It does not return
merely because submission succeeded. No separate Enter, `working` wait, output
sentinel, or periodic `agent get` is required. Choose the full lane budget up
front; the example allows 30 minutes. The wait consumes no model turns while
herdr is waiting.

### How the parent waits — no busywaiting

- **Blocking execution tool:** run the command once with a tool timeout longer
  than the CLI timeout. Let that tool call block. Do not wrap it in a sleep,
  status/read loop, or repeated short timeout.
- **Background tool with native completion notifications:** start that same
  command once, save its job ID, do independent work, then **return control**.
  The tool's completion notification wakes the parent. Do not poll the job or
  call another wait just to manufacture a wake.
- **Yielded/streaming execution:** an empty chunk marked “running” is not a
  timeout. Retain the existing job/session ID. Use the tool's supported
  blocking continuation or completion subscription for that job, never
  re-execute `agent prompt` or start a second herdr wait.
- A plain shell `&` is **not** a parent notification system. If no real
  background completion facility exists, use the blocking recipe. Do not
  invent a polling watcher or launch a model subagent merely to wait.

For parallel lanes, use the host's supported background completion facility
for each prompt/wait and consume their notifications at dependency barriers.
If the host cannot do that, wait sequentially; do not pretend detached shell
processes will wake the model.

A completion notification means **inspect the outcome**, not “PASS.” `blocked`
needs the user's answer; never auto-answer approvals. `idle`/`done` mean the
agent is ready for input, not that the task succeeded.

## 4. Read once, verify, integrate

After the doorbell returns:

```bash
herdr agent read <lane-name> --source recent-unwrapped --lines 120
git -C <worktree-path> status --short --branch
git -C <worktree-path> log -1 --stat
```

Verify the reported SHA exists, belongs to the lane, and contains the expected
work; check actual test evidence. The parent owns review and integration into
`uat`. Failed or blocked lanes retain their worktree and evidence.

If TUI alternate-screen output is unavailable, request a report written to a
temporary file using another `agent prompt ... --wait`, then read that file.
Do not replace missing evidence with a guessed verdict.

## 5. Teardown only after verified integration

Record the lane's workspace ID and path before closing its agent pane:

```bash
herdr pane close <agent-pane-id>
herdr worktree remove --workspace <lane-workspace-id>
```

If closing the last pane removed its workspace, reopen the recorded checkout
with `herdr worktree open --path <path> --no-focus`, capture its new workspace
ID, then remove that worktree. Do not use `--force` to discard unexplained
changes. Verify lane cleanup once; close only leftover resources you own.
Completed, integrated lanes should not leave idle agent panes behind.

## Errors are decisions, not polling triggers

| Result | Action |
| --- | --- |
| `agent_blocked` | Prompt was rejected before input; read once and ask the user. |
| `agent_prompt_stalled` or timeout | Delivery may have happened. Inspect once; never blindly resubmit. |
| Agent still working after a real timeout | Report the exceeded budget; get direction before extending it. If extended, use one `agent wait <name> --timeout <new-budget>`, not another prompt. |
| Interrupted connection or server handoff | Rediscover the agent once. Do not assume old pane IDs still identify it or resend the task. Re-arm one wait only after confirming ownership and activity. |
| `unknown` | Detection failure, not success. Inspect process/detection evidence and resolve the integration. |
| Wait returns immediately | Standalone `agent wait` matches current state; `agent prompt --wait` on an already-working agent can match the previous turn. Start from ready, submit once. |

If explicit output matching is genuinely needed, 0.9.1 uses `pane wait-output`
(not `wait output`). It searches existing output immediately: a sentinel echoed
in the prompt can produce false success. Lifecycle doorbell plus verified
report is the default; do not add a competing output waiter.

## Remote lanes

Use the same `herdr --machine <saved-label-or-id>` prefix for discovery and
every subsequent command. Discover IDs on that machine; local IDs and
`--current` are not remote targets. Do not combine `--machine` with `--session`
or `--remote`. Connection failure does not prove a mutation failed; inspect
remote state before retrying. The same root-pane and single-doorbell rules apply.
