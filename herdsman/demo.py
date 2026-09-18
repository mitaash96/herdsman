"""`herdsman demo`: a bundled initiative a stranger can run with no authoring.

Three nodes, not two: showing parallel agents *and* a gated handoff needs two
independent producers plus one consumer gated on them. Two nodes can show one
property or the other, never both.
"""

from .classes import Assignment, InitiativeSpec, Routes

BRIEF = "Two agents work in parallel; a third waits on an approved checkpoint."


def demo_spec(harness: str = "claude-code", model: str = "claude-opus-5") -> list[InitiativeSpec]:
    """The bundled demo graph, parameterized only by what the operator has."""
    assignment = Assignment(harness=harness, model=model)
    return [
        InitiativeSpec(
            id="D1",
            name="Write the greeting module",
            brief="Create `demo/greeting.py` with a `greet(name)` returning a greeting string.",
            assignment=assignment,
            routes=Routes(writes=["demo/greeting.py"]),
        ),
        InitiativeSpec(
            id="D2",
            name="Write the farewell module",
            brief="Create `demo/farewell.py` with a `farewell(name)` returning a parting string.",
            assignment=assignment,
            routes=Routes(writes=["demo/farewell.py"]),
        ),
        InitiativeSpec(
            id="D3",
            name="Cover both modules with one test",
            brief="Add `demo/test_demo.py` asserting both functions include the given name.",
            assignment=assignment,
            routes=Routes(reads=["demo/greeting.py", "demo/farewell.py"], writes=["demo/test_demo.py"]),
            depends_on=["D1", "D2"],
        ),
    ]


__all__ = ["BRIEF", "demo_spec"]
