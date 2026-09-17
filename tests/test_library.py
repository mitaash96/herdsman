"""Sprint 9 Library core: authoring, overrides, validation, and the snapshot.

The sprint exits on three claims, and each one has a test here by name:
a terminal edit reaches a newly planned initiative, already-approved
initiatives keep immutable snapshots, and the whole library never enters a
worker packet.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    AssetSnapshot,
    InitiativeSpec,
    LibrarySnapshot,
    MemoryLeaf,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
)
from herdsman.daemon import Daemon
from herdsman.library import (
    LIBRARY_DIR,
    MAX_CONTEXT_TOKENS,
    Asset,
    Library,
    LibraryError,
    parse_asset,
    parse_ref,
    serialize_asset,
)
from herdsman.memory import MemoryFileStore
from herdsman.runtime import compile_task_packet
from herdsman.store import EventStore
from tests.test_daemon import (
    CapturingRuntime,
    StubCollector,
    packet_from_command,
)

AT = datetime(2026, 9, 17, 12, tzinfo=UTC)
LUNA = Assignment(harness="luna", model="cheap-1")


def bundled(root: Path) -> Path:
    """A read-only shipped asset tree standing in for the packaged one."""
    roles = root / "roles"
    roles.mkdir(parents=True)
    _ = (roles / "implementer.md").write_text(
        "---\nkind: role\nname: implementer\ntitle: Implementer\n"
        + "references:\n  - contract/default\n---\nProduce changes in scope.\n",
        encoding="utf-8",
    )
    contracts = root / "contracts"
    contracts.mkdir(parents=True)
    _ = (contracts / "default.md").write_text(
        "---\nkind: contract\nname: default\ntitle: Default contract\n---\n"
        + "Show a clean checkpoint.\n",
        encoding="utf-8",
    )
    return root


def library(tmp_path: Path, context_budget: int = MAX_CONTEXT_TOKENS) -> Library:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Library(
        project,
        bundled_root=bundled(tmp_path / "bundled"),
        context_budget=context_budget,
    )


def plan_with(*specs: InitiativeSpec) -> Plan:
    return Plan.fold([
        PlanCreated(plan_id="plan_1", at=AT, brief="do it"),
        PlanProposed(plan_id="plan_1", at=AT, version=1, initiatives=list(specs)),
    ])


def spec(initiative_id: str, *assets: str) -> InitiativeSpec:
    return InitiativeSpec(
        id=initiative_id,
        name=initiative_id,
        brief=f"work on {initiative_id}",
        assignment=LUNA,
        routes=Routes(writes=[f"src/{initiative_id}/**"]),
        assets=list(assets),
    )


# --- identity and serialization ----------------------------------------------


def test_refs_name_a_kind_and_a_safe_slug() -> None:
    assert parse_ref("role/implementer") == ("role", "implementer")
    with pytest.raises(LibraryError, match="unknown asset kind"):
        _ = parse_ref("prompt/x")
    with pytest.raises(LibraryError, match="safe slug"):
        _ = parse_ref("role/Implementer")


def test_an_asset_round_trips_through_its_file_format() -> None:
    asset = Asset(
        kind="skill",
        name="navigate",
        title="Navigate the code",
        references=["role/implementer"],
        body="Run `herdsman nav codemap`.",
    )
    restored = parse_asset(
        serialize_asset(asset), kind="skill", name="navigate", origin="project"
    )
    assert restored == asset
    assert restored.digest == asset.digest


def test_an_unrelated_frontmatter_head_still_reads_as_an_asset() -> None:
    """A skill shipped for another harness loads without being rewritten."""
    asset = parse_asset(
        "---\nname: delegate\ndescription: >-\n  Hand a lane to another\n"
        + "  harness and supervise it.\nallowed-tools: Bash\n---\nBody text.\n",
        kind="skill",
        name="delegate",
        origin="bundled",
    )
    assert asset.title == "Hand a lane to another harness and supervise it."
    assert asset.body == "Body text."


def test_a_frontmatter_that_renames_its_own_file_is_refused() -> None:
    with pytest.raises(LibraryError, match="the path decides"):
        _ = parse_asset(
            "---\nkind: role\nname: other\n---\nx\n",
            kind="role",
            name="implementer",
            origin="project",
        )


# --- bundled assets and copy-on-edit -----------------------------------------


def test_bundled_assets_are_readable_and_never_written(tmp_path: Path) -> None:
    lib = library(tmp_path)
    implementer = lib.show("role/implementer")
    assert implementer.origin == "bundled"
    assert implementer.title == "Implementer"

    edited = lib.edit("role/implementer", body="Produce changes, carefully.")
    assert edited.origin == "project"
    assert edited.digest != implementer.digest
    # The shipped file is untouched; the project copy shadows it.
    assert "carefully" not in (tmp_path / "bundled/roles/implementer.md").read_text()
    assert lib.show("role/implementer").body == "Produce changes, carefully."
    assert lib.show("role/implementer").origin == "project"


def test_browse_reports_the_override_and_filters_the_shelf(tmp_path: Path) -> None:
    lib = library(tmp_path)
    _ = lib.edit("role/implementer", title="Implementer (local)")
    _ = lib.create("skill", "navigate", title="Navigate", body="nav")
    _ = lib.create("agent", "retired-one", title="Old", body="x")
    _ = lib.archive("agent/retired-one")

    refs = {row.ref: row for row in lib.browse()}
    assert set(refs) == {"role/implementer", "contract/default", "skill/navigate"}
    assert refs["role/implementer"].shadows_bundled is True
    assert refs["contract/default"].shadows_bundled is False
    assert [row.ref for row in lib.browse(kind="skill")] == ["skill/navigate"]
    assert [row.ref for row in lib.browse(status="retired")] == ["agent/retired-one"]
    assert [row.ref for row in lib.browse(query="navi")] == ["skill/navigate"]
    assert lib.unarchive("agent/retired-one").status == "active"


def test_copy_preserves_the_revision_and_rename_moves_a_project_file(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    source = lib.create("skill", "navigate", title="Navigate", body="nav")
    duplicate = lib.copy("skill/navigate", "navigate-deep")
    assert duplicate.ref == "skill/navigate-deep"
    assert duplicate.body == source.body

    moved = lib.rename("skill/navigate-deep", "navigate-2")
    assert moved.ref == "skill/navigate-2"
    assert lib.find("skill/navigate-deep") is None
    assert not (tmp_path / "project" / LIBRARY_DIR / "skills/navigate-deep.md").exists()


def test_a_bundled_asset_cannot_be_renamed(tmp_path: Path) -> None:
    lib = library(tmp_path)
    with pytest.raises(LibraryError, match="bundled and read-only"):
        _ = lib.rename("role/implementer", "builder")
    # Copying it under the new name is the supported move.
    assert lib.copy("role/implementer", "builder").ref == "role/builder"


def test_create_refuses_to_overwrite_and_edit_refuses_a_stale_revision(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    first = lib.create("skill", "navigate", body="one")
    with pytest.raises(LibraryError, match="already exists"):
        _ = lib.create("skill", "navigate", body="two")
    _ = lib.edit("skill/navigate", body="two")
    with pytest.raises(LibraryError, match="changed since it was read"):
        _ = lib.edit("skill/navigate", body="three", expect_digest=first.digest)


def test_checkout_materializes_the_editor_path_and_revision_follows_it(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    before = lib.revision
    path = lib.checkout("role/implementer")
    assert path == tmp_path / "project" / LIBRARY_DIR / "roles/implementer.md"
    assert path.is_file()
    # Checking out an unmodified bundled asset copies bytes, not identity.
    assert lib.revision == before

    _ = path.write_text(
        "---\nkind: role\nname: implementer\ntitle: Implementer\n---\nEdited in vim.\n",
        encoding="utf-8",
    )
    assert lib.revision != before
    assert lib.show("role/implementer").body == "Edited in vim."


# --- references, conflicts, effective context size ---------------------------


def test_resolution_follows_references_and_reports_a_missing_one(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    _ = lib.create("skill", "navigate", body="nav", references=["role/implementer"])
    closure = [asset.ref for asset in lib.resolve(["skill/navigate"])]
    assert closure == ["skill/navigate", "role/implementer", "contract/default"]

    issues = lib.validate(["skill/absent"])
    assert [(issue.code, issue.severity) for issue in issues] == [
        ("reference-missing", "error")
    ]


def test_a_reference_cycle_is_reported_rather_than_followed(tmp_path: Path) -> None:
    lib = library(tmp_path)
    _ = lib.create("skill", "a", body="a", references=["skill/b"])
    _ = lib.create("skill", "b", body="b", references=["skill/a"])
    issues = lib.validate(["skill/a"])
    assert [issue.code for issue in issues] == ["reference-cycle"]
    assert issues[0].detail == "skill/a -> skill/b -> skill/a"


def test_an_archived_reference_is_an_error_and_a_stale_one_is_a_warning(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    _ = lib.create("agent", "old", body="x")
    _ = lib.archive("agent/old")
    assert [issue.code for issue in lib.validate(["agent/old"])] == [
        "reference-retired"
    ]

    store = MemoryFileStore(tmp_path / "project")
    _ = store.write(
        MemoryLeaf(
            id="drifted",
            subject="pytest is slow",
            claim="the suite takes four minutes",
            origin="operator",
            at=AT,
            evidence=["decision:owner"],
            lifetime="project",
            status="stale",
        ),
        resolver=lambda _ref: True,
    )
    warnings = lib.validate(["memory-leaf/drifted"])
    assert [(issue.code, issue.severity) for issue in warnings] == [
        ("memory-stale", "warning")
    ]


def test_effective_context_size_over_budget_warns_without_refusing(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path, context_budget=10)
    _ = lib.create("skill", "long", body=" ".join(["word"] * 40))
    issues = lib.validate(["skill/long"], owner="init_a")
    assert [(issue.code, issue.severity, issue.ref) for issue in issues] == [
        ("context-size", "warning", "init_a")
    ]
    assert "over the 10-token budget" in issues[0].message
    assert issues[0].detail.startswith("skill/long=")


# --- the memory shelf, through MemoryFileStore -------------------------------


def test_the_memory_shelf_reads_and_writes_the_one_memory_store(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    created = lib.create(
        "memory-leaf",
        "flaky-test",
        title="test_login is flaky",
        body="test_login fails once in ten runs.\nSeen on CI and locally.",
        references=["decision:owner"],
        resolver=lambda _ref: True,
    )
    assert created.ref == "memory-leaf/flaky-test"

    store = MemoryFileStore(tmp_path / "project")
    leaf = store.get("flaky-test")
    assert leaf is not None
    assert leaf.subject == "test_login is flaky"
    assert leaf.claim == "test_login fails once in ten runs."
    assert leaf.lifetime == "project"

    _ = lib.edit("memory-leaf/flaky-test", body="Now fixed upstream.")
    edited = store.get("flaky-test")
    assert edited is not None and edited.body == "Now fixed upstream."
    assert edited.version == leaf.version + 1

    assert lib.archive("memory-leaf/flaky-test").status == "retired"
    retired = store.get("flaky-test")
    assert retired is not None and retired.status == "retired"
    # Retiring on the shelf is retiring in the store; there is no second copy.
    assert not (tmp_path / "project" / LIBRARY_DIR / "memory").exists()


def test_a_memory_leaf_still_needs_a_subject_a_claim_and_evidence(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    with pytest.raises(LibraryError, match="one-line claim"):
        _ = lib.create("memory-leaf", "empty", title="x", body="")
    with pytest.raises(ValueError, match="evidence"):
        _ = lib.create("memory-leaf", "bare", title="x", body="a claim")


# --- the approval snapshot ---------------------------------------------------


def test_the_snapshot_freezes_exact_contents_per_initiative(tmp_path: Path) -> None:
    lib = library(tmp_path)
    _ = lib.create("skill", "navigate", body="nav", references=["role/implementer"])
    _ = lib.create("agent", "reviewer", body="review")
    plan = plan_with(
        spec("init_a", "skill/navigate"),
        spec("init_b", "agent/reviewer"),
        spec("init_c"),
    )
    snapshot = lib.snapshot_for(plan)

    assert snapshot.by_initiative == {
        "init_a": ["skill/navigate", "role/implementer", "contract/default"],
        "init_b": ["agent/reviewer"],
    }
    assert "init_c" not in snapshot.by_initiative
    assert [asset.ref for asset in snapshot.for_initiative("init_b")] == [
        "agent/reviewer"
    ]
    assert snapshot.for_initiative("init_c") == []
    assert snapshot.for_initiative("init_a")[0].body == "nav"
    assert snapshot.total_tokens == sum(asset.tokens for asset in snapshot.assets)


def test_an_unresolvable_reference_refuses_the_snapshot(tmp_path: Path) -> None:
    lib = library(tmp_path)
    with pytest.raises(LibraryError, match="skill/absent"):
        _ = lib.snapshot_for(plan_with(spec("init_a", "skill/absent")))


def test_snapshot_warnings_are_recorded_rather_than_refused(tmp_path: Path) -> None:
    lib = library(tmp_path, context_budget=5)
    _ = lib.create("skill", "long", body=" ".join(["word"] * 40))
    snapshot = lib.snapshot_for(plan_with(spec("init_a", "skill/long")))
    assert [issue.code for issue in snapshot.issues] == ["context-size"]


def test_a_snapshot_cannot_name_an_initiative_the_plan_does_not_have() -> None:
    with pytest.raises(ValueError, match="unsnapshotted asset"):
        _ = LibrarySnapshot(by_initiative={"init_a": ["role/x"]})


def test_the_fold_records_one_snapshot_per_approved_version() -> None:
    frozen = LibrarySnapshot(
        assets=[
            AssetSnapshot(
                ref="role/implementer",
                kind="role",
                name="implementer",
                body="v1 text",
                digest="aaaa",
                tokens=2,
            )
        ],
        by_initiative={"init_a": ["role/implementer"]},
    )
    plan = Plan.fold([
        PlanCreated(plan_id="plan_1", at=AT, brief="do it"),
        PlanProposed(
            plan_id="plan_1", at=AT, version=1,
            initiatives=[spec("init_a", "role/implementer")],
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1, assets=frozen),
    ])
    assert plan.asset_snapshots[1] == frozen
    assert [asset.body for asset in plan.initiative_assets("init_a")] == ["v1 text"]
    assert plan.initiative_assets("init_b") == []


# --- the three sprint exits --------------------------------------------------


def test_a_terminal_edit_reaches_a_newly_planned_initiative(tmp_path: Path) -> None:
    """Exit: a terminal edit is used by a newly planned initiative."""
    project = tmp_path / "project"
    project.mkdir()
    store = EventStore(project / "events.db")
    daemon = Daemon(store, project_root=project)
    daemon.library().bundled_root = bundled(tmp_path / "bundled")

    def lib() -> Library:
        found = daemon.library()
        found.bundled_root = tmp_path / "bundled"
        return found

    path = lib().checkout("role/implementer")
    _ = path.write_text(
        "---\nkind: role\nname: implementer\ntitle: Implementer\n---\n"
        + "Edited at the terminal.\n",
        encoding="utf-8",
    )
    try:
        _ = daemon.append(PlanCreated(plan_id="plan_1", at=AT, brief="do it"))
        _ = daemon.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=1,
                initiatives=[spec("init_a", "role/implementer")],
            )
        )
        plan = daemon.approve_plan("plan_1")
        carried = plan.initiative_assets("init_a")
        assert [asset.body for asset in carried] == ["Edited at the terminal."]
        # Replay sees the same bytes: the snapshot is in the event, not on disk.
        assert Plan.fold(store.read("plan_1")).initiative_assets("init_a") == carried
    finally:
        store.close()


def test_an_approved_version_keeps_its_snapshot_across_later_edits(
    tmp_path: Path,
) -> None:
    """Exit: existing approved initiatives retain immutable asset snapshots."""
    project = tmp_path / "project"
    project.mkdir()
    store = EventStore(project / "events.db")
    daemon = Daemon(store, project_root=project)
    lib = Library(project, bundled_root=bundled(tmp_path / "bundled"))
    _ = lib.create("skill", "navigate", body="v1 instructions")
    try:
        _ = daemon.append(PlanCreated(plan_id="plan_1", at=AT, brief="do it"))
        _ = daemon.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=1,
                initiatives=[spec("init_a", "skill/navigate")],
            )
        )
        _ = daemon.approve_plan("plan_1")

        # The library moves on; a recalibration proposes and approves v2.
        _ = lib.edit("skill/navigate", body="v2 instructions")
        _ = daemon.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=2,
                initiatives=[
                    spec("init_a", "skill/navigate"),
                    spec("init_b", "skill/navigate"),
                ],
            )
        )
        plan = daemon.approve_plan("plan_1")

        assert plan.asset_snapshots[1].for_initiative("init_a")[0].body == (
            "v1 instructions"
        )
        assert plan.asset_snapshots[2].for_initiative("init_a")[0].body == (
            "v2 instructions"
        )
        assert (
            plan.asset_snapshots[1].for_initiative("init_a")[0].digest
            != plan.asset_snapshots[2].for_initiative("init_a")[0].digest
        )
        # And a cold replay rebuilds both, byte for byte.
        assert Plan.fold(store.read("plan_1")).asset_snapshots == plan.asset_snapshots
    finally:
        store.close()


def test_a_packet_carries_only_its_own_initiatives_assets(tmp_path: Path) -> None:
    """Exit: the full asset library is never injected into every worker."""
    lib = library(tmp_path)
    _ = lib.create("skill", "navigate", body="nav")
    _ = lib.create("agent", "reviewer", body="review")
    _ = lib.create("skill", "unused", body="never sent")
    plan = plan_with(spec("init_a", "skill/navigate"), spec("init_b"))
    snapshot = lib.snapshot_for(plan)

    packet = compile_task_packet(
        plan.initiatives["init_a"].spec, assets=snapshot.for_initiative("init_a")
    )
    body = packet.json()
    assert "nav" in body
    assert "never sent" not in body
    assert "review" not in body

    bare = compile_task_packet(
        plan.initiatives["init_b"].spec, assets=snapshot.for_initiative("init_b")
    )
    # A node that declared nothing carries no assets section at all.
    assert "assets" not in dict(bare.sections())
    assert bare.snapshot().total_tokens < packet.snapshot().total_tokens


def test_a_hand_edited_status_typo_is_named_rather_than_traced(
    tmp_path: Path,
) -> None:
    lib = library(tmp_path)
    path = lib.checkout("role/implementer")
    _ = path.write_text(
        "---\nkind: role\nname: implementer\nstatus: archived\n---\nx\n",
        encoding="utf-8",
    )
    with pytest.raises(LibraryError, match="declares status 'archived'"):
        _ = lib.show("role/implementer")


def test_a_launched_executor_receives_the_frozen_asset_and_no_other(
    tmp_path: Path,
) -> None:
    """The whole seam: shelf -> approval snapshot -> one executor's packet."""
    project = tmp_path / "project"
    project.mkdir()
    mapping = project / ".herdsman" / "luna.json"
    mapping.parent.mkdir(parents=True)
    _ = mapping.write_text(json.dumps({"binary": "luna-test"}), encoding="utf-8")
    lib = Library(project, bundled_root=bundled(tmp_path / "bundled"))
    _ = lib.create("skill", "navigate", body="run herdsman nav codemap")
    _ = lib.create("skill", "unused", body="never sent to anyone")

    store = EventStore(project / "events.db")
    daemon = Daemon(store, project_root=project)
    runner = CapturingRuntime()
    try:
        _ = daemon.append(PlanCreated(plan_id="plan_1", at=AT, brief="do it"))
        _ = daemon.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=1,
                initiatives=[spec("init_a", "skill/navigate"), spec("init_b")],
            )
        )
        _ = daemon.approve_plan("plan_1")

        async def scenario() -> None:
            _ = await daemon.run_initiative(
                "plan_1", "init_a", runtime=runner, collector=StubCollector()
            )

        asyncio.run(scenario())

        packet = packet_from_command(runner.commands[-1])
        carried = cast(list[dict[str, object]], packet["assets"])
        assert [asset["ref"] for asset in carried] == ["skill/navigate"]
        assert carried[0]["body"] == "run herdsman nav codemap"
        assert "never sent to anyone" not in runner.commands[-1]

        # And the attempt's own receipt records the section it was charged for.
        attempt = daemon.plan("plan_1").initiatives["init_a"].attempts[-1]
        assert attempt.packet_snapshot is not None
        assert "assets" in {
            section.name for section in attempt.packet_snapshot.sections
        }
    finally:
        store.close()


def test_recalibration_cannot_swap_the_assets_of_anchored_work() -> None:
    """Declared assets are part of an initiative's identity, so frozen work
    keeps the set it was approved with instead of silently acquiring another."""
    events = [
        PlanCreated(plan_id="plan_1", at=AT, brief="do it"),
        PlanProposed(
            plan_id="plan_1", at=AT, version=1,
            initiatives=[spec("init_a", "skill/navigate")],
        ),
        PlanApproved(plan_id="plan_1", at=AT, version=1),
        AttemptStarted(
            plan_id="plan_1", at=AT, attempt_id="att_1",
            initiative_id="init_a", assignment=LUNA,
        ),
    ]
    running = Plan.fold(events)
    assert running.initiatives["init_a"].state == "running"

    swapped = PlanProposed(
        plan_id="plan_1", at=AT, version=2,
        initiatives=[spec("init_a", "skill/other")],
    )
    with pytest.raises(ValueError, match="holds completed work"):
        _ = Plan.fold([*events, swapped])

    # Re-declaring it unchanged is accepted, as it is for every other field.
    unchanged = PlanProposed(
        plan_id="plan_1", at=AT, version=2,
        initiatives=[spec("init_a", "skill/navigate")],
    )
    assert Plan.fold([*events, unchanged]).version == 2
