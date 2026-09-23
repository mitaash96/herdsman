"""`herdsman demo`: a bundled initiative a stranger can run with no authoring.

Three nodes, not two: showing parallel agents *and* a gated handoff needs two
independent producers plus one consumer gated on them. Two nodes can show one
property or the other, never both.
"""

from .classes import Assignment, InitiativeSpec, Routes

BRIEF = "Two agents work in parallel; a third waits on both approved checkpoints."


def demo_spec(harness: str = "claude-code", model: str = "claude-opus-5") -> list[InitiativeSpec]:
    """The bundled demo graph, parameterized only by what the operator has."""
    assignment = Assignment(harness=harness, model=model)
    return [
        InitiativeSpec(
            id="D1",
            name="Write a greeting",
            brief="Create `demo/greeting.txt` containing exactly `Hello from D1.` followed by a newline. Do not change any other file.",
            assignment=assignment,
            routes=Routes(writes=["demo/greeting.txt"]),
            approval="required",
        ),
        InitiativeSpec(
            id="D2",
            name="Write a farewell",
            brief="Create `demo/farewell.txt` containing exactly `Goodbye from D2.` followed by a newline. Do not change any other file.",
            assignment=assignment,
            routes=Routes(writes=["demo/farewell.txt"]),
            approval="required",
        ),
        InitiativeSpec(
            id="D3",
            name="Combine the approved handoffs",
            brief=(
                "Read `demo/greeting.txt` and `demo/farewell.txt`. Create "
                "`demo/result.txt` containing their two lines, in that order, "
                "followed by `Both approved handoffs received.` and a newline. "
                "Do not change any other file."
            ),
            assignment=assignment,
            routes=Routes(
                reads=["demo/greeting.txt", "demo/farewell.txt"],
                writes=["demo/result.txt"],
            ),
            depends_on=["D1", "D2"],
        ),
    ]


__all__ = ["BRIEF", "demo_spec"]
