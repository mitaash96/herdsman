"""Graph calculations, contention detection, and the plan-gate risk report."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from herdsman.classes import (
    Assignment,
    Event,
    InitiativeSpec,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
)
from herdsman.graph import (
    ScopeTrie,
    conflicts_with,
    contention,
    critical_path,
    max_concurrency,
    overhead,
    plan_graph,
    risk_report,
)

AT = datetime(2026, 9, 2, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")


def spec(
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    brief: str = "do the thing",
    model: str = "cheap-1",
) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=brief,
        assignment=Assignment(harness="luna", model=model),
        routes=Routes(reads=reads or [], writes=writes or []),
        depends_on=depends_on or [],
    )


def planned(*specs: InitiativeSpec, approve: bool = True) -> Plan:
    events: list[Event] = [
        PlanCreated(plan_id="p", at=AT, brief="brief", planner=LUNA),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=list(specs)),
    ]
    if approve:
        events.append(PlanApproved(plan_id="p", at=AT, version=1))
    return Plan.fold(events)


def test_digest_tracks_content_not_identity() -> None:
    original = spec("a", writes=["src/a.py"])
    renamed = spec("b", writes=["src/a.py"])
    edited = spec("a", writes=["src/a.py"], brief="do a different thing")
    rescoped = spec("a", writes=["src/b.py"])

    # Same content under a new id is the same work.
    assert original.digest == renamed.digest
    # A changed brief or a changed scope is materially different work.
    assert original.digest != edited.digest
    assert original.digest != rescoped.digest
    # Route order is not content.
    assert (
        spec("a", writes=["x", "y"]).digest == spec("a", writes=["y", "x"]).digest
    )


def test_critical_path_and_max_concurrency() -> None:
    # a -> c, b -> c, c -> d; a and b are the only pair that can run together.
    plan = planned(
        spec("a"),
        spec("b"),
        spec("c", depends_on=["a", "b"]),
        spec("d", depends_on=["c"]),
    )
    path = critical_path(plan)
    assert path[-2:] == ["c", "d"]
    assert len(path) == 3
    assert max_concurrency(plan) == 2

    assert max_concurrency(planned(spec("a"), spec("b"), spec("c"))) == 3
    assert max_concurrency(planned(spec("a"), spec("b", depends_on=["a"]))) == 1


def test_scope_trie_matches_containing_directories() -> None:
    trie = ScopeTrie()
    trie.insert("herdsman/", "a")
    trie.insert("ui/src/app.svelte", "b")

    # A directory claim owns the files under it, in both directions.
    assert trie.touching("herdsman/daemon.py") == {"a"}
    assert trie.touching("ui/") == {"b"}
    assert trie.touching("ui/src/app.svelte") == {"b"}
    assert trie.touching("tests/test_daemon.py") == set()
    # A glob is the subtree it names, not a literal segment.
    assert trie.touching("herdsman/**") == {"a"}


def test_a_glob_inside_a_segment_over_approximates_rather_than_missing() -> None:
    # `src/*.py` and `src/a.py` really can collide, so treating the glob as a
    # literal segment would let two agents edit one file concurrently.
    plan = planned(spec("a", writes=["src/*.py"]), spec("b", writes=["src/a.py"]))
    conflicts = [item for item in contention(plan) if item.kind == "write_write"]
    assert len(conflicts) == 1
    assert conflicts_with(plan, "b", {"a"}) is True
    # Over-approximation costs concurrency, never correctness.
    assert contention(planned(spec("a", writes=["src/*.py"]), spec("b", writes=["ui/"]))) == []


def test_routes_reject_paths_that_escape_the_repository() -> None:
    for bad in ("../outside", "/etc/passwd", "~/secrets", "a/../../b"):
        with pytest.raises(ValidationError):
            _ = Routes(writes=[bad])
    # Ordinary paths, directory prefixes, and globs all remain valid.
    for good in ("src/a.py", "src/", "src/**", "src/*.py", "./src/a.py"):
        _ = Routes(writes=[good])


def test_overlapping_writes_are_a_conflict_and_shared_reads_are_not() -> None:
    plan = planned(
        spec("a", writes=["herdsman/"], reads=["notes/"]),
        spec("b", writes=["herdsman/daemon.py"], reads=["notes/"]),
    )
    found = contention(plan)
    conflicts = [item for item in found if item.kind == "write_write"]
    assert len(conflicts) == 1
    assert conflicts[0].initiatives == ("a", "b")
    assert conflicts[0].paths == ["herdsman/", "herdsman/daemon.py"]
    assert conflicts_with(plan, "b", {"a"}) is True
    assert conflicts_with(plan, "b", set()) is False

    # Shared reads alone are never contention.
    assert contention(planned(spec("a", reads=["x/"]), spec("b", reads=["x/"]))) == []


def test_write_read_overlap_suggests_a_missing_edge_with_its_direction() -> None:
    # Lexical order is the opposite of the roles, which is exactly how a
    # sorted pair silently reverses the suggested edge.
    plan = planned(
        spec("producer", writes=["herdsman/classes.py"]),
        spec("consumer", reads=["herdsman/classes.py"]),
    )
    suggested = [item for item in contention(plan) if item.kind == "write_read"]
    assert len(suggested) == 1
    assert suggested[0].writer == "producer"
    assert suggested[0].reader == "consumer"
    warning = risk_report(plan).warnings[0]
    assert warning.startswith("initiative consumer reads")
    assert "written by producer" in warning

    # A declared dependency already orders them, so it is a handoff, not a miss.
    ordered = planned(
        spec("producer", writes=["herdsman/classes.py"]),
        spec("consumer", reads=["herdsman/classes.py"], depends_on=["producer"]),
    )
    assert contention(ordered) == []


def test_risk_report_names_single_points_of_failure_and_blast_radius() -> None:
    plan = planned(
        spec("a"),
        spec("gate", depends_on=["a"]),
        spec("x", depends_on=["gate"]),
        spec("y", depends_on=["gate"]),
    )
    report = risk_report(plan)
    nodes = {node.initiative_id: node for node in report.nodes}
    assert nodes["gate"].articulation is True
    assert nodes["gate"].blast_radius == 2
    assert nodes["x"].blast_radius == 0
    assert nodes["gate"].on_critical_path is True
    assert report.max_concurrency == 2


def test_risk_report_warns_about_a_cheap_model_on_the_critical_path() -> None:
    plan = planned(
        spec("slow", model="cheap-1"),
        spec("fast", model="opus-5", depends_on=["slow"]),
    )
    tiers = {"cheap-1": "cheap", "opus-5": "frontier"}
    warnings = risk_report(plan, tiers=tiers).warnings
    assert any("slow" in warning and "cheap" in warning for warning in warnings)
    assert not any("fast" in warning for warning in warnings)
    # No tier map means no opinion, so no warning.
    assert risk_report(plan).warnings == []


def test_plan_graph_projects_status_and_a_zero_overhead_ratio() -> None:
    plan = planned(spec("a"), spec("b", depends_on=["a"]))
    projection = plan_graph(plan)
    assert projection.edges == [("a", "b")]
    assert projection.ready == ["a"]
    assert {node.initiative_id: node.ready for node in projection.nodes} == {
        "a": True,
        "b": False,
    }
    assert projection.nodes[0].digest == plan.initiatives["a"].spec.digest
    # Nothing has run, so there is nothing to divide by.
    assert overhead(plan).ratio is None
    assert overhead(plan).within_target is None
