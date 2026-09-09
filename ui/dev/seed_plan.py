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
  drawer    six initiatives shaped for the detail drawer: a long multi-paragraph
            brief, a contract with checks and a command policy, subtasks in
            every state, a settled attempt with usage, a failed attempt that was
            never closed, an attempt with no pane, and two members that never ran
  gate      eight initiatives left unapproved, shaped for the approval gate: a
            write/write conflict the lanes permit, an articulation point three
            members hang off, two unordered write/read pairs, a member that
            declares no writes, a long multi-paragraph brief, an edge with no
            shared path to explain it, and recorded planner usage

checkpoint  six initiatives shaped for checkpoint review: three preserved
            versions of one contract-gated member (approved, then rejected
            after a consumer had already built on it, then a revision awaiting
            review), a member whose evidence violates its contract three ways,
            a tainted consumer, a consumer waiting on two producers, an
            automatic member nobody reviews, and a member with no evidence

Prints the plan id. Open it in the UI at /run?plan=<id>.
"""

import argparse
from datetime import UTC, datetime, timedelta
from typing import cast

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    CheckResult,
    Checkpoint,
    CheckpointApproved,
    CheckpointRecorded,
    CheckpointRejected,
    Contract,
    Event,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    SubtaskAdvanced,
    Usage,
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




# --- the drawer shape --------------------------------------------------------
#
# What R2 has to survive. Every field the drawer reads has a member here that
# exercises its awkward value: a brief long enough to scroll and carrying a path
# no word-break can help, a contract with a real command policy, subtasks in all
# four states, an attempt that settled with reported usage, an attempt that
# failed and was therefore never closed, an attempt herdr never gave a pane, a
# member with no subtasks at all, and a member with nothing to focus.

DRAWER_BRIEF = (
    "Prove the initiative detail drawer against every shape of real record: "
    "long briefs, contracts, part-done subtasks, and attempts that did not "
    "finish cleanly."
)

LONG_BRIEF = """Reconcile herdr pane references against surviving worktrees after a daemon restart.

The projection is rebuilt from the event log on every start, so an attempt that
was running when the daemon went down comes back with a pane reference that may
name a pane herdr no longer holds. Treat a missing pane as a fact to record, not
an error to swallow: the operator needs to know the difference between an agent
that is still working and one whose terminal went away underneath it.

Do not reach for the herdr socket from the fold. The fold is pure and stays
pure; reconciliation belongs to the runtime layer, behind the same adapter
boundary everything else crosses. Write the reconciliation result to
herdsman/runtime/reconcile/pane_reference_reconciliation_report.py and leave the
projection alone.

Out of scope: recovering the agent itself, replaying its transcript, or deciding
whether to retry. Those are Sprint 6 and unit R9."""

DRAWER_CONTRACT = Contract(
    id="implementer-strict",
    role="implementer",
    required_checks=["uv run pytest", "uv run basedpyright"],
    required_paths=["herdsman/runtime.py"],
    require_patch=True,
    allow_writes=True,
    allowed_commands=["uv run pytest", "uv run basedpyright", "git diff"],
)

DRAWER_SPECS = [
    InitiativeSpec(
        id="D1",
        name="Reconcile pane references after a restart",
        brief=LONG_BRIEF,
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/classes.py"], writes=["herdsman/runtime.py"]),
        subtasks=[
            "Read the surviving worktrees back from herdr",
            "Mark an attempt whose pane is gone, without failing it",
            "Leave the fold pure -- reconcile in the runtime layer",
            "Cover a restart with a live attempt end to end",
        ],
        contract=DRAWER_CONTRACT,
    ),
    InitiativeSpec(
        id="D2",
        name="Attribute usage to the attempt that spent it",
        brief="Record per-attempt usage from the harness, never from an estimate.",
        assignment=PI,
        routes=Routes(writes=["herdsman/checkpoint.py"]),
        subtasks=["Read the harness figure", "Refuse an estimate silently standing in"],
    ),
    InitiativeSpec(
        id="D3",
        name="Preserve failure evidence when an attempt is discarded",
        brief="A discarded attempt releases its worktree and keeps its evidence.",
        assignment=CLAUDE,
        routes=Routes(writes=["herdsman/daemon.py"]),
        subtasks=["Release the worktree", "Keep the checkpoint", "Prove both"],
    ),
    InitiativeSpec(
        id="D4",
        name="Gate a downstream consumer on an approved checkpoint",
        brief="Stay blocked until the producer's checkpoint is approved by a reviewer.",
        assignment=PI,
        routes=Routes(reads=["herdsman/checkpoint.py"], writes=["tests/test_dag_run.py"]),
        subtasks=["Gate the consumer", "Release on approval"],
        approval="required",
    ),
    InitiativeSpec(
        id="D5",
        name="Prove the whole thread on a fresh machine",
        brief="Install and run the thread on a machine that has never seen it.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/cli.py"], writes=["README.md"]),
        depends_on=["D1", "D3"],
    ),
    InitiativeSpec(
        id="D6",
        name="Publish the structural risk report",
        brief="Serve the report the plan gate is decided from.",
        assignment=PI,
        routes=Routes(reads=["herdsman/graph.py"], writes=["herdsman/graph.py"]),
        subtasks=["Project it", "Serve it"],
    ),
]


# --- the gate shape ----------------------------------------------------------
#
# What R3 has to survive, and the only fixture where the callouts are non-empty:
# the golden five collide nowhere. G2/G3 both write `daemon.py` with nothing
# ordering them, which is the hard limit the lanes cannot see. G4 is the
# articulation point every path crosses. G6 and G7 read what others write with
# no dependency between them -- advisory, and there are two so the ranking has
# something to rank. G8 declares no writes at all, and G5's edge onto G4 has no
# shared path, so the register has to say "declared" rather than invent a reason.

GATE_BRIEF = (
    "Land the token ledger: per-attempt accounting, a preflight estimate, and "
    "the overhead ratio recomputed from measurements rather than guesses."
)

GATE_LONG_BRIEF = """Meter every attempt against the harness's own reported usage, and refuse to fill a gap with an estimate.

The ratio Herdsman publishes is falsifiable, which only holds while the
denominator is measurement. A harness that reports nothing leaves the attempt
unmetered, and unmetered has to survive all the way to the readout as its own
value -- not as a zero, and not as a provider figure quietly promoted into the
harness column.

Out of scope: enforcing a ceiling. Deciding what happens when a plan runs past
its budget is a policy question and it is not this initiative's."""

GATE_SPECS = [
    InitiativeSpec(
        id="G1",
        name="Per-attempt usage ledger",
        brief=GATE_LONG_BRIEF,
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/classes.py"], writes=["herdsman/ledger.py"]),
        subtasks=["Record the harness figure", "Keep unmetered unmetered"],
    ),
    InitiativeSpec(
        id="G2",
        name="Serve the ledger projection",
        brief="Project the ledger over the plan's existing daemon routes.",
        assignment=PI,
        routes=Routes(reads=["herdsman/ledger.py"], writes=["herdsman/daemon.py"]),
        subtasks=["Projection", "Route"],
    ),
    InitiativeSpec(
        id="G3",
        name="Stream cost frames as they land",
        brief="Push each attempt's cost onto the plan event stream when recorded.",
        assignment=PI,
        # The collision: G2 and G3 both write the daemon, and nothing orders
        # them. The lane cover will happily put them side by side.
        routes=Routes(reads=["herdsman/classes.py"], writes=["herdsman/daemon.py"]),
        subtasks=["Frame shape", "Emit on record"],
    ),
    InitiativeSpec(
        id="G4",
        name="Preflight estimate before dispatch",
        brief="Size a packet before it is sent, and label the number an estimate.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/ledger.py"], writes=["herdsman/preflight.py"]),
        subtasks=["Size the packet", "Label the provenance", "Never call it a limit"],
        depends_on=["G1"],
    ),
    InitiativeSpec(
        id="G5",
        name="Recompute the overhead ratio from measurements",
        brief="Drop the crude Sprint 2 counters for the ledger's own totals.",
        assignment=PI,
        # No shared path with G4: the edge is declared and the register says so
        # rather than inventing a motive for it.
        routes=Routes(reads=["herdsman/graph.py"], writes=["herdsman/graph.py"]),
        subtasks=["Read the ledger", "Republish the ratio"],
        depends_on=["G4"],
    ),
    InitiativeSpec(
        id="G6",
        name="Show cost in the run readouts",
        brief="Put the measured figure and its provenance on the Run sheet.",
        assignment=CLAUDE,
        # Reads the ledger G1 writes, with no dependency ordering them.
        routes=Routes(reads=["herdsman/ledger.py"], writes=["ui/src/routes/run/+page.svelte"]),
        subtasks=["Readout cell", "Provenance gloss"],
    ),
    InitiativeSpec(
        id="G7",
        name="Cover the ledger end to end",
        brief="Prove an unmetered attempt survives to the readout as unmetered.",
        assignment=PI,
        # Reads the preflight G4 writes, again unordered.
        routes=Routes(reads=["herdsman/preflight.py"], writes=["tests/test_ledger.py"]),
        subtasks=["Unmetered fixture", "Assert it never becomes zero"],
        depends_on=["G5"],
    ),
    InitiativeSpec(
        id="G8",
        name="Review the published ratio claim",
        brief="Read the ratio the release will publish and say whether it holds.",
        assignment=CLAUDE,
        # Declares no writes at all -- the register has to say so.
        routes=Routes(reads=["herdsman/graph.py", "README.md"]),
        approval="required",
        depends_on=["G7"],
    ),
]



def drawer_events(plan_id: str, now: datetime) -> list[Event]:
    """Attempts and progress for the drawer shape, in the order the fold accepts.

    Timestamps are staggered so durations are real: every event on one clock
    reads as a zero-second attempt, which is a fixture artefact the drawer would
    otherwise be blamed for.
    """
    started = now - timedelta(minutes=95)
    later = now - timedelta(minutes=32)
    return [
        # D1: running, part-done subtasks, a contract, and a live pane.
        AttemptStarted(
            plan_id=plan_id, at=started, attempt_id="a-D1", initiative_id="D1",
            assignment=CLAUDE, worktree_ref=".herdsman/worktrees/D1",
            pane_ref="herdsman:1", packet_tokens=14200,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D1", subtask_id="D1.1", state="done"),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D1", subtask_id="D1.2", state="doing"),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D1", subtask_id="D1.4", state="skipped"),
        # D2: settled, with a checkpoint that reported real harness usage.
        AttemptStarted(
            plan_id=plan_id, at=started, attempt_id="a-D2", initiative_id="D2",
            assignment=PI, worktree_ref=".herdsman/worktrees/D2",
            pane_ref="herdsman:2", packet_tokens=6100,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D2", subtask_id="D2.1", state="done"),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D2", subtask_id="D2.2", state="done"),
        CheckpointRecorded(
            plan_id=plan_id, at=later,
            checkpoint=Checkpoint(
                id="c-D2", attempt_id="a-D2",
                changed_paths=["herdsman/checkpoint.py", "tests/test_checkpoint.py"],
                exit_code=0,
                usage=Usage(input_tokens=48120, output_tokens=9340, source="harness"),
            ),
        ),
        InitiativeSettled(plan_id=plan_id, at=later, initiative_id="D2", checkpoint_id="c-D2"),
        # D3: failed. Nothing closes the attempt, so it has no end time and the
        # reason exists only on this event -- which is exactly what R2 must say.
        AttemptStarted(
            plan_id=plan_id, at=started, attempt_id="a-D3", initiative_id="D3",
            assignment=CLAUDE, worktree_ref=".herdsman/worktrees/D3",
            pane_ref="herdsman:3", packet_tokens=8800,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=now, initiative_id="D3", subtask_id="D3.1", state="done"),
        InitiativeFailed(
            plan_id=plan_id, at=later, initiative_id="D3",
            reason="the required checks did not pass in the attempt worktree",
        ),
        # D4: review required, and herdr never answered with a pane.
        AttemptStarted(
            plan_id=plan_id, at=started, attempt_id="a-D4", initiative_id="D4",
            assignment=PI, worktree_ref=".herdsman/worktrees/D4",
            pane_ref=None, packet_tokens=5200,
        ),
        CheckpointRecorded(
            plan_id=plan_id, at=later,
            checkpoint=Checkpoint(
                id="c-D4", attempt_id="a-D4",
                changed_paths=["tests/test_dag_run.py"], exit_code=0,
                usage=Usage(input_tokens=21050, output_tokens=3110, source="estimate"),
            ),
        ),
        # D5 waits on D1 and D3; D6 is ready and has never run. Neither has an
        # attempt, which is the drawer's other empty state.
    ]



# --- the checkpoint shape ----------------------------------------------------
#
# What R4 has to survive. The whole point of the shape is that a checkpoint is
# a *history*, not a state: C1 carries three preserved versions and the middle
# of the story is the awkward one -- v1 was approved, a consumer ran on it, and
# only then was it rejected. So the plan holds a settled producer whose
# evidence no longer stands, a consumer the fold has tainted, an approved
# version that is still the diff base because that is what was built on, and a
# revision awaiting review. C2 is the other hard case: evidence that violates
# its contract three separate ways, so approving it will be refused by the
# daemon before anything is appended.

CHECKPOINT_BRIEF = (
    "Land contracted checkpoint gates: a manifest per version, review that "
    "blocks downstream work, and taint when approved evidence is withdrawn."
)

STRICT = Contract(
    id="implementer-gated",
    role="implementer",
    required_checks=["uv run pytest", "uv run basedpyright", "verify-proposed"],
    required_paths=["herdsman/daemon.py"],
    require_patch=True,
    allow_writes=True,
    allowed_commands=["uv run pytest", "uv run basedpyright", "verify-proposed"],
)

# C2's contract asks for a check that never ran and a path never touched, so
# its one version violates it three ways at once (missing check, missing
# artifact, missing patch) on top of a non-zero exit.
DEMANDING = Contract(
    id="reviewer-strict",
    role="implementer",
    required_checks=["uv run pytest", "uv run basedpyright"],
    required_paths=["herdsman/graph.py", "docs/contracts.md"],
    require_patch=True,
)

CHECKPOINT_SPECS = [
    InitiativeSpec(
        id="C1",
        name="Project the checkpoint review surface",
        brief=(
            "Serve every preserved checkpoint version with its decision, the "
            "reviewer who made it and the reason they gave, so a refusal is "
            "auditable after the run that produced it is long over.\n\n"
            "Rejection never deletes. A superseded version stays addressable "
            "and stays readable, because a consumer that already ran on it "
            "built on evidence the plan has to keep pointing at."
        ),
        assignment=CLAUDE,
        # Declared file by file rather than as `herdsman/` and `tests/`: a
        # directory prefix is over-approximated into the whole subtree, which
        # would put this member in write/write contention with three siblings
        # it never touches. The field would be right and the fixture would be
        # about the wrong thing.
        routes=Routes(
            reads=["herdsman/classes.py", "herdsman/graph.py"],
            writes=[
                "herdsman/daemon.py",
                "herdsman/classes.py",
                "herdsman/store.py",
                "herdsman/verifier.py",
                "tests/test_daemon.py",
                "tests/test_classes.py",
                "tests/test_taint.py",
                "tests/test_store.py",
                "ui/src/lib/daemon.ts",
                "docs/review.md",
            ],
        ),
        subtasks=[
            "Fold the four review events",
            "Derive the decision per version",
            "Project taint across dependency edges",
        ],
        contract=STRICT,
        approval="required",
    ),
    InitiativeSpec(
        id="C2",
        name="Validate a contract before recording approval",
        brief=(
            "Refuse an approval whose evidence does not satisfy the contract, "
            "before any event is appended, so a refused decision stays pending "
            "rather than leaving a verdict nobody can act on."
        ),
        assignment=PI,
        routes=Routes(
            reads=["herdsman/classes.py"],
            writes=["herdsman/contracts.py", "tests/test_contracts.py"],
        ),
        subtasks=["Validate ahead of the append", "Type every violation"],
        contract=DEMANDING,
        approval="required",
    ),
    InitiativeSpec(
        id="C3",
        name="Release a consumer on approved evidence",
        brief="Start only once the producer's checkpoint stands approved.",
        assignment=PI,
        routes=Routes(reads=["herdsman/daemon.py"], writes=["tests/test_gates.py"]),
        depends_on=["C1"],
        subtasks=["Gate on the decision", "Prove the release"],
    ),
    InitiativeSpec(
        id="C4",
        name="Report taint across dependency edges",
        brief="List every consumer resting on evidence that has since been withdrawn.",
        assignment=CLAUDE,
        routes=Routes(reads=["herdsman/graph.py"], writes=["herdsman/graph.py"]),
        depends_on=["C1", "C2"],
        subtasks=["Walk the edges", "Clear on re-run"],
    ),
    InitiativeSpec(
        id="C5",
        name="Settle clean evidence without a reviewer",
        brief="An automatic member settles on a clean checkpoint and asks nobody.",
        assignment=PI,
        routes=Routes(writes=["herdsman/checkpoint.py"]),
        subtasks=["Settle on clean evidence"],
    ),
    InitiativeSpec(
        id="C6",
        name="Document the review vocabulary",
        brief="Write down what approved, rejected and changes-requested each mean.",
        assignment=CLAUDE,
        routes=Routes(writes=["docs/contracts.md"]),
        depends_on=["C5"],
    ),
]

# C1 v2 touches nine paths against v1's five, so the change list has an added
# half, a carried half and a dropped half, and every list runs past the cap the
# collapsed sheet holds itself to.
C1_V1_PATHS = [
    "herdsman/daemon.py",
    "herdsman/classes.py",
    "tests/test_daemon.py",
    "tests/test_classes.py",
    "docs/review.md",
]
C1_V2_PATHS = [
    "herdsman/daemon.py",
    "herdsman/classes.py",
    "herdsman/store.py",
    "herdsman/verifier.py",
    "tests/test_daemon.py",
    "tests/test_classes.py",
    "tests/test_taint.py",
    "tests/test_store.py",
    "ui/src/lib/daemon.ts",
]

# Executor-written caveats: non-recoverable decisions and blockers, never a
# summary of the work. Named rather than inlined so each is one string literal.
TAINT_COST_CAVEAT = (
    "The taint walk is O(V+E) per read and is recomputed on every request; it "
    "is not cached, and a plan an order of magnitude larger will feel it."
)
ORDER_CHANGE_CAVEAT = (
    "The diamond is fixed by walking in topological order and unioning, which "
    "changes the order taints come out in. Any consumer that relied on the old "
    "order will need re-reading."
)
PRE_EXISTING_CAVEAT = (
    "basedpyright is failing on graph.py, which this revision did not touch; "
    "the errors are pre-existing and are not repaired here."
)

PASSING = [
    CheckResult(name="uv run pytest", passed=True, summary="214 passed, 4 skipped"),
    CheckResult(name="uv run basedpyright", passed=True, summary="0 errors, 0 warnings"),
    CheckResult(name="verify-proposed", passed=True, summary="PASS"),
]


def checkpoint_events(plan_id: str, now: datetime) -> list[Event]:
    """Three versions of one gated checkpoint, and the taint that follows.

    The order is the order the fold accepts and the order it actually happened
    in: approve, release a consumer onto it, then withdraw the approval. That
    sequence is the only way to produce a settled producer whose evidence no
    longer stands, which is the state the whole review surface exists for.
    """
    ran = now - timedelta(hours=4)
    approved_at = now - timedelta(hours=3)
    consumer_ran = now - timedelta(hours=2, minutes=30)
    rejected_at = now - timedelta(minutes=50)
    revised_at = now - timedelta(minutes=35)
    return [
        # --- C1, version 1: clean evidence, approved, and settled on. --------
        AttemptStarted(
            plan_id=plan_id, at=ran, attempt_id="a-C1", initiative_id="C1",
            assignment=CLAUDE, worktree_ref=".herdsman/worktrees/C1",
            pane_ref="herdsman:1", packet_tokens=18400,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=ran, initiative_id="C1", subtask_id="C1.1", state="done"),
        SubtaskAdvanced(plan_id=plan_id, at=ran, initiative_id="C1", subtask_id="C1.2", state="done"),
        CheckpointRecorded(
            plan_id=plan_id, at=approved_at - timedelta(minutes=5),
            checkpoint=Checkpoint(
                id="c-C1-1", attempt_id="a-C1",
                changed_paths=C1_V1_PATHS,
                base_sha="4f1c9ab30d5e7c2188aa41d0",
                head_sha="a7d2e5b19c40f8317bb0c6ea",
                checks=PASSING,
                exit_code=0,
                usage=Usage(input_tokens=61240, output_tokens=11880, source="harness"),
                patch_path=".herdsman/artifacts/c-C1-1.patch",
                caveats=[TAINT_COST_CAVEAT],
            ),
        ),
        CheckpointApproved(
            plan_id=plan_id, at=approved_at, checkpoint_id="c-C1-1", by="operator",
            reason="Evidence is clean and the fold is right. Ship it.",
        ),
        InitiativeSettled(plan_id=plan_id, at=approved_at, initiative_id="C1", checkpoint_id="c-C1-1"),
        # --- C3 runs on that approval, and settles. --------------------------
        AttemptStarted(
            plan_id=plan_id, at=consumer_ran, attempt_id="a-C3", initiative_id="C3",
            assignment=PI, worktree_ref=".herdsman/worktrees/C3",
            pane_ref="herdsman:3", packet_tokens=7300,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=consumer_ran, initiative_id="C3", subtask_id="C3.1", state="done"),
        SubtaskAdvanced(plan_id=plan_id, at=consumer_ran, initiative_id="C3", subtask_id="C3.2", state="done"),
        CheckpointRecorded(
            plan_id=plan_id, at=consumer_ran + timedelta(minutes=20),
            checkpoint=Checkpoint(
                id="c-C3", attempt_id="a-C3",
                changed_paths=["tests/test_gates.py"],
                base_sha="a7d2e5b19c40f8317bb0c6ea",
                head_sha="0c93f7ad61e28b445d1af0c7",
                checks=[CheckResult(name="uv run pytest", passed=True, summary="9 passed")],
                exit_code=0,
                usage=Usage(input_tokens=15900, output_tokens=2410, source="harness"),
                patch_path=".herdsman/artifacts/c-C3.patch",
            ),
        ),
        InitiativeSettled(
            plan_id=plan_id, at=consumer_ran + timedelta(minutes=20),
            initiative_id="C3", checkpoint_id="c-C3",
        ),
        # --- and only then is v1 withdrawn. C3's recorded work now rests on
        #     evidence the plan has taken back, which is what taint is for.
        CheckpointRejected(
            plan_id=plan_id, at=rejected_at, checkpoint_id="c-C1-1", by="operator",
            reason=(
                "The taint walk misses a diamond: a consumer reached through two "
                "paths is listed once and cleared by whichever path recovers "
                "first. Found it while reviewing C3."
            ),
        ),
        # --- C1, version 2: the revision, awaiting review and not clean. -----
        CheckpointRecorded(
            plan_id=plan_id, at=revised_at,
            checkpoint=Checkpoint(
                id="c-C1-2", attempt_id="a-C1",
                changed_paths=C1_V2_PATHS,
                base_sha="a7d2e5b19c40f8317bb0c6ea",
                head_sha="e18b40c7a2df95361c07be44",
                checks=[
                    CheckResult(name="uv run pytest", passed=True, summary="231 passed, 4 skipped"),
                    CheckResult(
                        name="uv run basedpyright", passed=False,
                        summary=(
                            "2 errors: herdsman/graph.py:118 reportUnknownMemberType, "
                            "herdsman/graph.py:204 reportMissingParameterType"
                        ),
                    ),
                    CheckResult(name="ruff check", passed=True, summary="All checks passed"),
                ],
                exit_code=0,
                usage=Usage(input_tokens=88300, output_tokens=19420, source="harness"),
                patch_path=".herdsman/artifacts/c-C1-2.patch",
                caveats=[ORDER_CHANGE_CAVEAT, PRE_EXISTING_CAVEAT],
            ),
        ),
        # --- C2: one version, violating its contract three ways. -------------
        AttemptStarted(
            plan_id=plan_id, at=ran, attempt_id="a-C2", initiative_id="C2",
            assignment=PI, worktree_ref=".herdsman/worktrees/C2",
            pane_ref="herdsman:2", packet_tokens=9100,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=ran, initiative_id="C2", subtask_id="C2.1", state="doing"),
        CheckpointRecorded(
            plan_id=plan_id, at=now - timedelta(minutes=18),
            checkpoint=Checkpoint(
                id="c-C2", attempt_id="a-C2",
                changed_paths=["herdsman/contracts.py", "tests/test_contracts.py"],
                base_sha="4f1c9ab30d5e7c2188aa41d0",
                head_sha=None,
                checks=[
                    CheckResult(
                        name="uv run pytest", passed=False,
                        summary=(
                            "3 failed: test_refuses_before_append, "
                            "test_violation_is_typed, test_pending_survives_refusal"
                        ),
                    ),
                ],
                exit_code=1,
                usage=Usage(input_tokens=24700, output_tokens=6100, source="estimate"),
                caveats=["Left the validator half-written; the ordering rule is still open."],
            ),
        ),
        # --- C5: automatic policy, clean, settled, nobody reviewed it. -------
        AttemptStarted(
            plan_id=plan_id, at=ran, attempt_id="a-C5", initiative_id="C5",
            assignment=PI, worktree_ref=".herdsman/worktrees/C5",
            pane_ref="herdsman:5", packet_tokens=4200,
        ),
        SubtaskAdvanced(plan_id=plan_id, at=ran, initiative_id="C5", subtask_id="C5.1", state="done"),
        CheckpointRecorded(
            plan_id=plan_id, at=ran + timedelta(minutes=12),
            checkpoint=Checkpoint(
                id="c-C5", attempt_id="a-C5",
                changed_paths=["herdsman/checkpoint.py"],
                base_sha="4f1c9ab30d5e7c2188aa41d0",
                head_sha="b502ce7148ad39f06e1c88b2",
                checks=[CheckResult(name="uv run pytest", passed=True, summary="18 passed")],
                exit_code=0,
                usage=Usage(input_tokens=9800, output_tokens=1450, source="provider"),
                patch_path=".herdsman/artifacts/c-C5.patch",
            ),
        ),
        InitiativeSettled(
            plan_id=plan_id, at=ran + timedelta(minutes=12),
            initiative_id="C5", checkpoint_id="c-C5",
        ),
        # C4 waits on C1 and C2 and has never run. C6 waits on C5, which has
        # settled, so it is ready and has no evidence of its own -- the empty
        # state the review section has to say something honest about.
    ]

SHAPES = ("sprint2", "proposed", "dense", "drawer", "gate", "checkpoint")
DEFAULT_IDS = {
    "sprint2": "ui-f1-sprint2",
    "proposed": "ui-r1-proposed",
    "dense": "ui-r1-dense",
    "drawer": "ui-r2-drawer",
    "gate": "ui-r3-gate",
    "checkpoint": "ui-r4-checkpoint",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--shape", choices=SHAPES, default="sprint2")
    _ = parser.add_argument("--plan-id", default=None)
    args = parser.parse_args()
    shape = cast(str, args.shape)
    plan_id = cast(str, args.plan_id or DEFAULT_IDS[shape])

    store = EventStore()
    try:
        if plan_id in store.plans():
            print(f"{plan_id} already exists; nothing written.")
            return 0
        now = datetime.now(UTC)
        if shape == "dense":
            specs, brief = dense_specs(), DENSE_BRIEF
        elif shape == "drawer":
            specs, brief = DRAWER_SPECS, DRAWER_BRIEF
        elif shape == "gate":
            specs, brief = GATE_SPECS, GATE_BRIEF
        elif shape == "checkpoint":
            specs, brief = CHECKPOINT_SPECS, CHECKPOINT_BRIEF
        else:
            specs, brief = SPECS, BRIEF
        # The gate reads planning cost, which is the only token figure a proposed
        # plan has. Recording it here is what lets the readout show a real
        # provenance instead of the unknown every other fixture carries.
        planned = (
            Usage(input_tokens=18_402, output_tokens=3_117, source="harness")
            if shape == "gate"
            else None
        )
        events: list[Event] = [
            PlanCreated(plan_id=plan_id, at=now, brief=brief, planner=CLAUDE),
            PlanProposed(
                plan_id=plan_id, at=now, version=1, initiatives=specs, usage=planned
            ),
        ]
        if shape not in ("proposed", "gate"):
            events.append(PlanApproved(plan_id=plan_id, at=now, version=1))
        if shape == "dense":
            events.extend(live_events(plan_id, now))
        if shape == "drawer":
            events.extend(drawer_events(plan_id, now))
        if shape == "checkpoint":
            events.extend(checkpoint_events(plan_id, now))
        for event in events:
            _ = store.append(event)
    finally:
        store.close()

    print(plan_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
