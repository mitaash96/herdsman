"""Write one real plan into the project's event store, for UI validation.

The plan is *locally seeded*, not planner-authored: no model is called and no
harness runs. Everything downstream of it is real — the events go through the
real `EventStore`, and `herdsman serve` folds and projects them with the real
`Plan.fold`, `plan_graph` and `risk_report`. That is the point: the UI is
validated against genuine Sprint 2 projections rather than a mock store.

    uv run python ui/dev/seed_plan.py [--shape SHAPE] [--plan-id ID]

Shapes, each a different thing the Run view has to survive:

  sprint2   the golden five: three roots, a diamond, one gated consumer (default)
  proposed  the same plan left unapproved, so nothing has run
  dense     28 initiatives over eleven ranks, long names, mixed live states,
            a write/write conflict and an unordered write/read pair

Prints the plan id. Open it in the UI at /run?plan=<id>.
"""

import argparse
from datetime import UTC, datetime

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    Checkpoint,
    CheckpointRecorded,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
)
from herdsman.store import EventStore

BRIEF = "Prove concurrent independent initiatives with a checkpoint-gated consumer."

CLAUDE = Assignment(harness="claude-code", model="claude-opus-5")
PI = Assignment(harness="pi", model="pi-default")

SPECS = [
    InitiativeSpec(
        id="I1",
        name="Readiness computed from the folded plan",
        brief="Derive readiness per node in `plan_graph`; never store a blocked state.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/classes.py"], writes=["herdsman/graph.py"]),
        subtasks=["Fold events", "Derive ready set", "Assert no stored readiness"],
    ),
    InitiativeSpec(
        id="I2",
        name="SSE projection per plan",
        brief="Stream domain events to subscribers without polling the event store.",
        assignment=PI,
        routes=Routes(reads=["herdsman/classes.py"], writes=["herdsman/daemon.py"]),
        subtasks=["Subscriber registry", "Encode SSE frames"],
    ),
    InitiativeSpec(
        id="I3",
        name="Append-only event store with WAL",
        brief="Fold before writing so a rejected event never reaches disk.",
        assignment=PI,
        routes=Routes(writes=["herdsman/store.py"]),
        subtasks=["DDL", "Fold-then-append", "Restart projection"],
    ),
    InitiativeSpec(
        id="I4",
        name="Concurrent dispatch under contention",
        brief="Two independent initiatives run at once; a third waits on a claim.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/graph.py"], writes=["tests/test_dag_run.py"]),
        subtasks=["Contention fixture", "Concurrency assertion"],
        depends_on=["I1", "I2"],
    ),
    InitiativeSpec(
        id="I5",
        name="Checkpoint-gated downstream consumer",
        brief="Stay blocked until the producer checkpoint settles.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/checkpoint.py"], writes=["tests/test_dag_run.py"]),
        subtasks=["Gate the consumer", "Settle and unblock"],
        depends_on=["I3", "I4"],
    ),
]


# --- the dense shape ---------------------------------------------------------
#
# What R1 has to survive: more ranks than the field can label comfortably, names
# too long for a cell, every member state at once, and both kinds of contention.

DENSE_BRIEF = (
    "Reconcile the runtime layer end to end: recovery, contention, and the "
    "projections the driver UI reads."
)

SPINE = [
    "Fold the append-only event log into the canonical plan projection",
    "Reconcile herdr pane references against surviving worktrees after a restart",
    "Derive readiness from the folded plan rather than storing a blocked state",
    "Serialize concurrent writers over one declared path prefix in the scheduler",
    "Compile the task packet from role contract, brief and settled ancestor patches",
    "Apply ancestor patches in topological order before the executor is launched",
    "Collect the checkpoint evidence manifest without trusting model-authored prose",
    "Gate the downstream consumer on its producer's approved checkpoint version",
    "Attribute orchestration and productive tokens to the initiative that spent them",
    "Project the running graph and per-node status for the driver UI and the CLI",
    "Prove the whole thread end to end on a fresh machine with two harnesses",
]

BRANCHES: list[tuple[str, str, int, Assignment, list[str], list[str]]] = [
    # (id, name, rank it joins the spine at, harness, reads, writes)
    ("B1", "Bootstrap the SQLite write-ahead log and its restart projection", 0, PI,
     [], ["herdsman/store.py"]),
    ("B2", "Stream domain events per plan over server-sent events without polling", 0, PI,
     ["herdsman/classes.py"], ["herdsman/daemon.py"]),
    ("B3", "Declare read and write route scopes on every initiative specification", 0, CLAUDE,
     [], ["herdsman/classes.py"]),
    ("B4", "Index declared paths in a prefix trie so a subtree overlap cannot be missed", 2, CLAUDE,
     ["herdsman/classes.py"], ["herdsman/graph.py"]),
    ("B5", "Compute the minimum chain cover that bounds a plan's real concurrency", 2, PI,
     [], ["herdsman/graph.py"]),
    ("B6", "Preserve failure evidence when an attempt is discarded rather than settled", 3, CLAUDE,
     [], ["herdsman/runtime.py"]),
    ("B7", "Open and release one git worktree per attempt, and never leak one", 3, PI,
     [], ["herdsman/herdr.py"]),
    ("B8", "Answer a blocking question from the operator without an operator model turn", 4, CLAUDE,
     ["herdsman/daemon.py"], ["herdsman/runtime.py"]),
    ("B9", "Record per-attempt usage from the harness, never from an estimate", 5, PI,
     [], ["herdsman/checkpoint.py"]),
    ("B10", "Publish the structural risk report the plan gate is decided from", 6, CLAUDE,
     ["herdsman/graph.py"], ["herdsman/daemon.py"]),
    ("B11", "Refuse a cyclic proposal at fold time so it can never reach disk", 7, PI,
     [], ["herdsman/classes.py"]),
    ("B12", "Cover concurrent dispatch under contention with an end-to-end test", 8, CLAUDE,
     ["herdsman/graph.py"], ["tests/test_dag_run.py"]),
    ("B13", "Cover checkpoint-gated consumers with an end-to-end test", 8, CLAUDE,
     ["herdsman/checkpoint.py"], ["tests/test_dag_run.py"]),
    ("B14", "Install and run the whole thread on a machine that has never seen it", 9, PI,
     ["herdsman/cli.py"], ["README.md"]),
    ("B15", "Retire the crude overhead ratio in favour of the attributed ledger", 9, CLAUDE,
     [], ["herdsman/graph.py"]),
    ("B16", "Document the daemon's HTTP surface as the CLI and UI both consume it", 10, PI,
     ["herdsman/daemon.py"], ["docs/daemon.md"]),
]


def dense_specs() -> list[InitiativeSpec]:
    specs: list[InitiativeSpec] = []
    for index, name in enumerate(SPINE):
        specs.append(
            InitiativeSpec(
                id=f"S{index + 1}",
                name=name,
                brief=name + ".",
                assignment=CLAUDE if index % 2 == 0 else PI,
                routes=Routes(
                    reads=["herdsman/classes.py"],
                    writes=[f"herdsman/spine/step_{index + 1:02d}.py"],
                ),
                subtasks=["Design", "Implement", "Prove"],
                depends_on=[f"S{index}"] if index else [],
            )
        )
    for identifier, name, joins, who, reads, writes in BRANCHES:
        specs.append(
            InitiativeSpec(
                id=identifier,
                name=name,
                brief=name + ".",
                assignment=who,
                routes=Routes(reads=reads, writes=writes),
                subtasks=["Implement", "Prove"],
                depends_on=[f"S{joins + 1}"] if joins else [],
            )
        )
    return specs


# Live states, in the order the fold accepts them. B12 and B13 both write
# tests/test_dag_run.py with no edge between them, which is the write/write
# conflict the field draws in red; B4 writes herdsman/graph.py that B5 also
# writes, and B10 reads it.
SETTLED = ["S1", "S2", "S3", "B1", "B2", "B3"]
RUNNING = ["S4", "B4", "B7"]
FAILED = ["B6"]


def live_events(plan_id: str, now: datetime) -> list[Event]:
    events: list[Event] = []
    for index, initiative_id in enumerate([*SETTLED, *RUNNING, *FAILED]):
        attempt = f"a-{initiative_id}"
        events.append(
            AttemptStarted(
                plan_id=plan_id,
                at=now,
                attempt_id=attempt,
                initiative_id=initiative_id,
                assignment=CLAUDE if index % 2 == 0 else PI,
                worktree_ref=f".herdsman/worktrees/{initiative_id}",
                pane_ref=f"herdsman:{index}",
                packet_tokens=1800 + index * 120,
            )
        )
    for initiative_id in SETTLED:
        checkpoint = f"c-{initiative_id}"
        events.append(
            CheckpointRecorded(
                plan_id=plan_id,
                at=now,
                checkpoint=Checkpoint(id=checkpoint, attempt_id=f"a-{initiative_id}", exit_code=0),
            )
        )
        events.append(
            InitiativeSettled(plan_id=plan_id, at=now, initiative_id=initiative_id, checkpoint_id=checkpoint)
        )
    for initiative_id in FAILED:
        events.append(
            InitiativeFailed(
                plan_id=plan_id,
                at=now,
                initiative_id=initiative_id,
                reason="the required checks did not pass in the attempt worktree",
            )
        )
    return events


SHAPES = ("sprint2", "proposed", "dense")
DEFAULT_IDS = {"sprint2": "ui-f1-sprint2", "proposed": "ui-r1-proposed", "dense": "ui-r1-dense"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--shape", choices=SHAPES, default="sprint2")
    _ = parser.add_argument("--plan-id", default=None)
    args = parser.parse_args()
    shape: str = args.shape
    plan_id: str = args.plan_id or DEFAULT_IDS[shape]

    store = EventStore()
    try:
        if plan_id in store.plans():
            print(f"{plan_id} already exists; nothing written.")
            return 0
        now = datetime.now(UTC)
        specs = dense_specs() if shape == "dense" else SPECS
        brief = DENSE_BRIEF if shape == "dense" else BRIEF
        events: list[Event] = [
            PlanCreated(plan_id=plan_id, at=now, brief=brief, planner=CLAUDE),
            PlanProposed(plan_id=plan_id, at=now, version=1, initiatives=specs),
        ]
        if shape != "proposed":
            events.append(PlanApproved(plan_id=plan_id, at=now, version=1))
        if shape == "dense":
            events.extend(live_events(plan_id, now))
        for event in events:
            _ = store.append(event)
    finally:
        store.close()

    print(plan_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
