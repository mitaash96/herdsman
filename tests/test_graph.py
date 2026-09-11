"""Graph calculations, contention detection, and the plan-gate risk report."""

from datetime import UTC, datetime
import pytest
from pydantic import ValidationError

from herdsman.checkpoint import CheckpointError
from herdsman.classes import (
    Assignment,
    Attempt,
    AttemptStarted,
    Checkpoint,
    CheckpointRecorded,
    Event,
    InitiativeSettled,
    InitiativeSpec,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    Usage,
)
from herdsman.graph import (
    ScopeTrie,
    ancestor_patches,
    conflicts_with,
    contention,
    critical_path,
    downstream_impact,
    max_concurrency,
    overhead,
    plan_graph,
    plan_revision,
    revision_impact,
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
    subtasks: list[str] | None = None,
    token_cap: int | None = None,
) -> InitiativeSpec:
    return InitiativeSpec(
        id=node_id,
        name=node_id,
        brief=brief,
        assignment=Assignment(harness="luna", model=model),
        routes=Routes(reads=reads or [], writes=writes or []),
        subtasks=subtasks or [],
        depends_on=depends_on or [],
        token_cap=token_cap,
    )


def stream(*specs: InitiativeSpec) -> list[Event]:
    """Plan `p` version 1 with `specs`, created and approved."""
    return [
        PlanCreated(plan_id="p", at=AT, brief="brief", planner=LUNA),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=list(specs)),
        PlanApproved(plan_id="p", at=AT, version=1),
    ]


def recalibration(*specs: InitiativeSpec, reason: str | None = None) -> PlanProposed:
    return PlanProposed(
        plan_id="p", at=AT, version=2, initiatives=list(specs), reason=reason
    )


def planned(*specs: InitiativeSpec, approve: bool = True) -> Plan:
    events: list[Event] = [
        PlanCreated(plan_id="p", at=AT, brief="brief", planner=LUNA),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=list(specs)),
    ]
    if approve:
        events.append(PlanApproved(plan_id="p", at=AT, version=1))
    return Plan.fold(events)


def settle(plan: Plan, node_id: str, patch_path: str | None) -> None:
    attempt_id = f"att_{node_id}"
    plan.initiatives[node_id].attempts.append(
        Attempt(
            id=attempt_id,
            initiative_id=node_id,
            assignment=LUNA,
            started_at=AT,
            checkpoint=Checkpoint(
                id=f"cp_{node_id}",
                attempt_id=attempt_id,
                patch_path=patch_path,
            ),
        )
    )
    plan.initiatives[node_id].state = "settled"


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


def test_settled_ancestor_without_patch_fails_with_its_initiative_name() -> None:
    plan = planned(spec("ancestor"), spec("consumer", depends_on=["ancestor"]))
    settle(plan, "ancestor", None)

    with pytest.raises(CheckpointError, match="ancestor"):
        _ = ancestor_patches(plan, "consumer")


def test_empty_ancestor_patch_is_included() -> None:
    plan = planned(spec("ancestor"), spec("consumer", depends_on=["ancestor"]))
    patch = ".herdsman/artifacts/empty.patch"
    settle(plan, "ancestor", patch)

    assert ancestor_patches(plan, "consumer") == [patch]


def test_ancestor_patches_remain_topologically_ordered() -> None:
    plan = planned(
        spec("a"),
        spec("b", depends_on=["a"]),
        spec("c", depends_on=["a"]),
        spec("d", depends_on=["b", "c"]),
    )
    for node in ("a", "b", "c"):
        settle(plan, node, f".herdsman/artifacts/{node}.patch")

    assert ancestor_patches(plan, "d") == [
        ".herdsman/artifacts/a.patch",
        ".herdsman/artifacts/b.patch",
        ".herdsman/artifacts/c.patch",
    ]


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


def test_overhead_counts_only_harness_reported_productive_usage() -> None:
    plan = planned(spec("a"))
    initiative = plan.initiatives["a"]
    for index, source in enumerate(("harness", "provider", "estimate")):
        attempt_id = f"att_{index}"
        initiative.attempts.append(
            Attempt(
                id=attempt_id,
                initiative_id="a",
                assignment=LUNA,
                started_at=AT,
                checkpoint=Checkpoint(
                    id=f"cp_{index}",
                    attempt_id=attempt_id,
                    usage=Usage(
                        input_tokens=10,
                        output_tokens=5,
                        source=source,
                    ),
                ),
            )
        )
    plan.planner_usage = Usage(input_tokens=20, output_tokens=10, source="provider")

    measured = overhead(plan)

    assert measured.productive_tokens == 15

def test_downstream_impact_covers_transitive_descendants_in_build_order() -> None:
    plan = planned(
        spec("a"),
        spec("b", depends_on=["a"]),
        spec("c", depends_on=["a"]),
        spec("d", depends_on=["b", "c"]),
    )
    impact = downstream_impact(plan, "a")
    assert [node.initiative_id for node in impact.descendants] == ["b", "c", "d"]
    assert impact.started == []
    assert [node.state for node in impact.descendants] == ["pending"] * 3

    assert [
        node.initiative_id for node in downstream_impact(plan, "b").descendants
    ] == ["d"]
    assert downstream_impact(plan, "d").descendants == []

def test_downstream_impact_flags_descendants_that_already_ran() -> None:
    plan = planned(spec("a"), spec("b", depends_on=["a"]))
    settle(plan, "a", None)
    plan.initiatives["b"].attempts.append(
        Attempt(id="att_b", initiative_id="b", assignment=LUNA, started_at=AT)
    )

    impact = downstream_impact(plan, "a")
    assert impact.started == ["b"]
    by_id = {node.initiative_id: node for node in impact.descendants}
    assert by_id["b"].attempts == 1
    assert by_id["b"].state == "pending"

def test_downstream_impact_rejects_unknown_initiatives() -> None:
    plan = planned(spec("a"))
    with pytest.raises(ValueError, match="unknown initiative nope"):
        _ = downstream_impact(plan, "nope")


# --- recalibration diff ------------------------------------------------------


def test_plan_revision_distinguishes_renumbered_from_changed_nodes() -> None:
    events = stream(spec("a"), spec("b", brief="do b"))
    previous = Plan.fold(events)
    current = Plan.fold(
        [*events, recalibration(spec("a2"), spec("b", brief="do b", writes=["src/"])), ]
    )

    revision = plan_revision(previous, current)

    assert (revision.plan_id, revision.from_version, revision.to_version) == ("p", 1, 2)
    assert revision.ambiguous == []
    # Every category is present, so a reader never has to guess at a zero.
    assert revision.counts == {
        "unchanged": 1,
        "edited": 1,
        "split": 0,
        "merged": 0,
        "new": 0,
        "removed": 0,
    }
    renamed = next(record for record in revision.nodes if record.renamed)
    assert (renamed.change, renamed.old_ids, renamed.new_ids) == (
        "unchanged",
        ["a"],
        ["a2"],
    )
    assert renamed.old_digest == renamed.new_digest == previous.initiatives["a"].spec.digest
    assert renamed.edge_state == "same"
    edited = next(record for record in revision.nodes if record.change == "edited")
    assert (edited.old_ids, edited.new_ids, edited.renamed) == (["b"], ["b"], False)
    assert edited.old_digest != edited.new_digest


def test_plan_revision_names_a_split_and_discloses_its_allowance_resets() -> None:
    events = [
        *stream(spec("a", subtasks=["one", "two"])),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_a", initiative_id="a", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_a", attempt_id="att_a", exit_code=0),
        ),
    ]
    previous = Plan.fold(events)
    current = Plan.fold([
        *events,
        recalibration(spec("one", subtasks=["one"]), spec("two", subtasks=["two"])),
    ])

    revision = plan_revision(previous, current)
    split = next(record for record in revision.nodes if record.change == "split")

    assert (split.old_ids, split.new_ids) == (["a"], ["one", "two"])
    assert split.old_digest == previous.initiatives["a"].spec.digest
    assert split.new_digest is None  # a group of nodes has no single digest
    assert (split.old_attempts, split.new_attempts) == (1, 0)
    assert revision.counts["split"] == 1

    impact = revision_impact(previous, current, revision)

    assert [
        (reset.initiative_id, reset.source_ids, reset.consumed_attempts)
        for reset in impact.allowance_resets
    ] == [("one", ["a"], 1), ("two", ["a"], 1)]
    assert impact.dropped == ["a"]
    assert impact.stranded == []
    assert [item.spec.id for item in current.retired] == ["a"]


def test_revision_impact_discloses_a_same_id_extraction_allowance() -> None:
    events = [
        *stream(spec("a", subtasks=["kept", "extracted"])),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_a", initiative_id="a", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_a", attempt_id="att_a", exit_code=1),
        ),
    ]
    previous = Plan.fold(events)
    # The anchor keeps its id and one claim; the extracted residual arrives as
    # a new node, and a genuinely new claim belongs to nobody's dropped work.
    current = Plan.fold([
        *events,
        recalibration(
            spec("a", subtasks=["kept"]),
            spec("child", subtasks=["extracted"]),
            spec("fresh", subtasks=["brand new"]),
            spec("empty"),
        ),
    ])

    revision = plan_revision(previous, current)
    impact = revision_impact(previous, current, revision)

    assert (revision.counts["new"], revision.counts["edited"]) == (3, 1)
    assert [
        (
            reset.initiative_id,
            reset.source_status,
            reset.source_ids,
            reset.candidate_source_ids,
            reset.consumed_attempts,
        )
        for reset in impact.allowance_resets
    ] == [
        ("child", "proven", ["a"], [], 1),
        # A new node with no claims still gets its allocation row.
        ("empty", "new", [], [], None),
        # Genuinely new work still discloses its allocation, labelled as new.
        ("fresh", "new", [], [], None),
    ]
    assert impact.dropped == []
    assert impact.stranded == []


def test_revision_impact_labels_mixed_extraction_and_carries_renames() -> None:
    events = [
        *stream(
            spec("a", subtasks=["kept", "moved"]),
            spec("r", brief="rename me"),
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_a", initiative_id="a", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_a", attempt_id="att_a", exit_code=1),
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_r", initiative_id="r", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_r", attempt_id="att_r", exit_code=1),
        ),
    ]
    previous = Plan.fold(events)
    current = Plan.fold([
        *events,
        recalibration(
            spec("a", subtasks=["kept"]),
            # Mixed: one dropped claim plus one nobody ever declared.
            spec("child", subtasks=["moved", "brand new"]),
            spec("renamed", brief="rename me"),
        ),
    ])

    impact = revision_impact(previous, current)

    # A rename carries the same recorded budget: no fresh-allowance row.
    assert current.initiatives["renamed"].known_ids == ["r", "renamed"]
    assert len(current.initiatives["renamed"].attempts) == 1
    assert [
        (
            reset.initiative_id,
            reset.source_status,
            reset.candidate_source_ids,
            reset.source_ids,
            reset.consumed_attempts,
        )
        for reset in impact.allowance_resets
    ] == [("child", "unknown", ["a"], [], None)]


def test_revision_impact_reports_candidate_sources_instead_of_guessing() -> None:
    events = [
        *stream(
            spec("a", subtasks=["kept", "shared"]),
            spec("b", subtasks=["shared"]),
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_a", initiative_id="a", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_a", attempt_id="att_a", exit_code=1),
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_b", initiative_id="b", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_b", attempt_id="att_b", exit_code=1),
        ),
    ]
    previous = Plan.fold(events)
    # Two surviving nodes both dropped one identical claim: the payer cannot be
    # proven, so both are reported as candidates with no consumed count.
    current = Plan.fold([
        *events,
        recalibration(
            spec("a", subtasks=["kept"]),
            spec("b", subtasks=[]),
            spec("child", subtasks=["shared"]),
        ),
    ])

    impact = revision_impact(previous, current)

    assert [
        (
            reset.initiative_id,
            reset.source_status,
            reset.candidate_source_ids,
            reset.source_ids,
            reset.consumed_attempts,
        )
        for reset in impact.allowance_resets
    ] == [("child", "candidates", ["a", "b"], [], None)]
    assert impact.stranded == []


def test_plan_revision_detects_a_merge_from_the_new_node_side() -> None:
    events = [
        *stream(spec("one", subtasks=["alpha"]), spec("two", subtasks=["beta"])),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_one", initiative_id="one", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_one", attempt_id="att_one", exit_code=0),
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_two", initiative_id="two", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_two", attempt_id="att_two", exit_code=0),
        ),
    ]
    previous = Plan.fold(events)
    current = Plan.fold([
        *events,
        recalibration(spec("both", subtasks=["alpha", "beta"])),
    ])

    revision = plan_revision(previous, current)
    merged = next(record for record in revision.nodes if record.change == "merged")

    assert (merged.old_ids, merged.new_ids) == (["one", "two"], ["both"])
    assert merged.old_digest is None
    assert merged.new_digest == current.initiatives["both"].spec.digest
    assert (merged.old_attempts, merged.new_attempts) == (2, 0)
    # The merged node is one record, never also a duplicate `new` one.
    assert revision.counts == {
        "unchanged": 0,
        "edited": 0,
        "split": 0,
        "merged": 1,
        "new": 0,
        "removed": 0,
    }
    impact = revision_impact(previous, current, revision)
    assert [
        (
            reset.initiative_id,
            reset.source_status,
            reset.source_ids,
            reset.consumed_attempts,
        )
        for reset in impact.allowance_resets
    ] == [("both", "proven", ["one", "two"], 2)]
    assert impact.dropped == ["one", "two"]


def test_plan_revision_reports_an_overlapping_partition_as_ambiguous() -> None:
    events = stream(spec("a", subtasks=["one", "two"]))
    previous = Plan.fold(events)
    # Two new nodes whose claims overlap: naming either one the split would be
    # a guess, so the diff falls back to honest new plus removed and says which
    # digests it could not reconcile.
    current = Plan.fold([
        *events,
        recalibration(
            spec("one", subtasks=["one", "two"], brief="merged"),
            spec("two", subtasks=["one"]),
        ),
    ])

    revision = plan_revision(previous, current)

    assert (revision.counts["split"], revision.counts["merged"]) == (0, 0)
    assert (revision.counts["new"], revision.counts["removed"]) == (2, 1)
    assert len(revision.ambiguous) == 2
    assert previous.initiatives["a"].spec.digest in revision.ambiguous


def test_a_cap_only_revision_stays_content_unchanged_and_still_shows_budgets() -> None:
    """A moved allowance leaves no digest behind, so the diff has to carry it."""
    events = stream(
        spec("a", token_cap=1000),
        spec("b", brief="carry me", token_cap=1000),
        spec("c", token_cap=300),
    )
    previous = Plan.fold(events)
    current = Plan.fold([
        *events,
        PlanProposed(
            plan_id="p", at=AT, version=2, token_cap=5000,
            initiatives=[
                spec("a", token_cap=25),
                # Renumbered with a smaller budget: still the same work.
                spec("b2", brief="carry me", token_cap=None),
                spec("c", token_cap=300),
            ],
        ),
    ])

    revision = plan_revision(previous, current)
    assert revision.counts["unchanged"] == 3  # content never moved
    rows = {tuple(record.new_ids): record for record in revision.nodes}
    # The cap is deliberately not identity, so digest equality is not silence:
    # both sides of the budget are on the row the operator approves.
    assert (rows[("a",)].old_token_caps, rows[("a",)].new_token_caps) == (
        [1000],
        [25],
    )
    assert (rows[("b2",)].old_token_caps, rows[("b2",)].new_token_caps) == (
        [1000],
        [None],
    )
    assert rows[("b2",)].old_digest == rows[("b2",)].new_digest
    assert (rows[("c",)].old_token_caps, rows[("c",)].new_token_caps) == (
        [300],
        [300],
    )

    impact = revision_impact(previous, current, revision)
    assert (impact.plan_token_cap_from, impact.plan_token_cap_to) == (None, 5000)


def test_a_revision_discloses_the_caps_on_both_sides_of_a_group() -> None:
    """A split's children and a new node's cap are read off their own side."""
    events = stream(spec("a", subtasks=["one", "two"], token_cap=900))
    previous = Plan.fold(events)
    current = Plan.fold([
        *events,
        recalibration(
            spec("one", subtasks=["one"], token_cap=100),
            spec("two", subtasks=["two"], token_cap=200),
            spec("fresh", token_cap=7),
        ),
    ])

    rows = {
        tuple(record.new_ids): record
        for record in plan_revision(previous, current).nodes
    }
    split, fresh = rows[("one", "two")], rows[("fresh",)]
    assert (split.change, split.old_token_caps, split.new_token_caps) == (
        "split",
        [900],
        [100, 200],
    )
    # No old side at all is an empty list; no cap would have been one `None`.
    assert (fresh.change, fresh.old_token_caps, fresh.new_token_caps) == (
        "new",
        [],
        [7],
    )


def test_revision_impact_covers_downstream_and_never_strands_work() -> None:
    events = [
        PlanCreated(plan_id="p", at=AT, brief="brief", planner=LUNA),
        PlanProposed(
            plan_id="p", at=AT, version=1,
            initiatives=[
                spec("a"),
                spec("b", depends_on=["a"]),
                spec("c", depends_on=["b"]),
            ],
        ),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_a", initiative_id="a", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(
                id="cp_a", attempt_id="att_a", exit_code=0,
                usage=Usage(input_tokens=1, output_tokens=1, source="harness"),
            ),
        ),
        InitiativeSettled(
            plan_id="p", at=AT, initiative_id="a", checkpoint_id="cp_a"
        ),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att_b", initiative_id="b", assignment=LUNA
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp_b", attempt_id="att_b", exit_code=1),
        ),
    ]
    previous = Plan.fold(events)
    # `a` is settled, so it comes back identical; only the unfinished `b` is revised.
    current = Plan.fold([
        *events,
        recalibration(
            spec("a"),
            spec("b", brief="do b differently", depends_on=["a"]),
            spec("c", depends_on=["b"]),
            reason="b was obsolete",
        ),
    ])

    impact = revision_impact(previous, current)

    assert [node.initiative_id for node in impact.downstream] == ["c"]
    assert (impact.downstream[0].state, impact.downstream[0].attempts) == ("pending", 0)
    assert impact.stranded == []
    assert impact.dropped == []
    # Completed work is not reverted: the settled node and its checkpoint stand.
    assert current.initiatives["a"].state == "settled"
    assert [attempt.checkpoint.id for attempt in current.initiatives["a"].attempts if attempt.checkpoint] == ["cp_a"]


def test_plan_revision_needs_the_same_plan_and_a_later_version() -> None:
    plan = planned(spec("a"))
    other = Plan.fold([
        PlanCreated(plan_id="q", at=AT, brief="brief", planner=LUNA),
        PlanProposed(plan_id="q", at=AT, version=1, initiatives=[spec("a")]),
    ])

    with pytest.raises(ValueError, match="cannot be revised against"):
        _ = plan_revision(plan, other)
    with pytest.raises(ValueError, match="needs a later version"):
        _ = plan_revision(plan, plan)


def test_overhead_attributes_recalibration_from_its_own_ledger_rows() -> None:
    events = stream(spec("a"))
    assert overhead(Plan.fold(events)).recalibration_calls == 0
    assert overhead(Plan.fold(events)).recalibration_tokens == 0

    # An unlabelled planner row is planning, not recalibration, however many
    # versions it sits at in `planner_usage_history`.
    plain = Plan.fold([
        *events,
        PlanProposed(
            plan_id="p", at=AT, version=2, initiatives=[spec("a")],
            usage=Usage(input_tokens=50, output_tokens=50, source="harness"),
        ),
    ])
    assert plain.planner_usage_history
    assert overhead(plain).recalibration_tokens == 0

    labelled = Plan.fold([
        *events,
        PlanProposed(
            plan_id="p", at=AT, version=2, initiatives=[spec("a")],
            usage=Usage(
                input_tokens=90, output_tokens=10, source="harness",
                category="recalibration_replay",
            ),
        ),
    ])
    measured = overhead(labelled)

    assert measured.recalibration_tokens == 100
    assert measured.recalibration_calls == 1
    assert measured.orchestration_tokens == 100
    assert "recalibration_replay" in measured.recalibration_derivation
