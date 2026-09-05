"""Write one real plan into the project's event store, for UI validation.

The plan is *locally seeded*, not planner-authored: no model is called and no
harness runs. Everything downstream of it is real — the events go through the
real `EventStore`, and `herdsman serve` folds and projects them with the real
`Plan.fold`, `plan_graph` and `risk_report`. That is the point: the UI is
validated against genuine Sprint 2 projections rather than a mock store.

    uv run python ui/dev/seed_plan.py [--plan-id ID]

Prints the plan id. Open it in the UI at /run?plan=<id>.
"""

import argparse
import sys
from datetime import UTC, datetime

from herdsman.classes import (
    Assignment,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--plan-id", default="ui-f1-sprint2")
    args = parser.parse_args()
    plan_id: str = args.plan_id

    store = EventStore()
    try:
        if plan_id in store.plans():
            print(f"{plan_id} already exists; nothing written.")
            return 0
        now = datetime.now(UTC)
        for event in (
            PlanCreated(plan_id=plan_id, at=now, brief=BRIEF, planner=CLAUDE),
            PlanProposed(plan_id=plan_id, at=now, version=1, initiatives=SPECS),
            PlanApproved(plan_id=plan_id, at=now, version=1),
        ):
            _ = store.append(event)
    finally:
        store.close()

    print(plan_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
