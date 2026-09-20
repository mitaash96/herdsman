# Architecture boundaries

Herdsman coordinates existing agent tooling on one machine. The boundaries below
explain where state lives and which component owns each operation.

| Component | Owns | Does not own |
| --- | --- | --- |
| Herdsman daemon and CLI | Plans, DAG readiness, approvals, checkpoint evidence, event history, budgets, interventions, recovery, and project-local configuration | Agent model execution, terminal panes, or global harness settings |
| Herdsman browser UI | Visual projections of daemon state: graph layout, drawers, charts, live status, and review presentation | A separate state store or exclusive state-changing workflow; it does not embed terminal panes |
| `herdr` | Local agent panes, PTYs, workspaces, worktrees, process I/O, and its own socket/protocol | Herdsman plans, approvals, contracts, or event history |
| Configured agent CLIs | Model/provider authentication and model execution inside the pane | Herdsman scheduling and checkpoint decisions |
| Project `.herdsman/` | SQLite events, daemon record, lock, Kitchen configuration, derived artifacts, and local recovery material | Any global configuration belonging to herdr or an agent CLI |

The daemon is the write boundary for Herdsman state. The CLI and browser call the
same daemon projections and actions, while deterministic reads such as status,
events, salvage, and navigation can inspect project-local state directly. The
browser is served by `herdsman up` when a built `ui/build` bundle is available;
otherwise the daemon remains usable as an API-only service.

## What crosses into an agent attempt

Each attempt receives a compiled task packet containing its own brief, assignment,
route claims, declared subtasks, dependency evidence, approved contract inputs,
and bounded memory/failure context. The packet does not include sibling briefs,
the whole plan, or a failed attempt's transcript. Checkpoint evidence returns to
the daemon, where it is validated against the initiative's contract before
settlement or downstream release.

## What Herdsman deliberately leaves alone

Herdsman does not modify global harness or agent configuration, provider
credentials, or a user's unrelated worktrees and panes. The herdr adapter lists
and operates on workspaces it created for the project. Cloud runtimes, remote
control, hosted accounts, broader agent protocols, and a Rust rewrite are outside
this local v1 boundary.

The architecture is intentionally replaceable: harness and model choice is
project configuration, while the product boundary is the pipeline of roles,
contracts, checkpoint evidence, and handoff documents.

