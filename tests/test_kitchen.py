"""Kitchen configuration domain: loading, projection, resolution, readiness."""

import json
from pathlib import Path

import pytest

from herdsman.classes import Assignment
from herdsman.kitchen import (
    Adapter,
    Capabilities,
    Defaults,
    FallbackChain,
    HarnessFacts,
    Kitchen,
    KitchenConfigError,
    ModelEntry,
)


def write(root: Path, name: str, payload: object) -> Path:
    directory = root / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    return path


PI = {
    "name": "pi",
    "argv": ["/usr/bin/pi", "--print", "{prompt}"],
    "model_argv": ["--model"],
    "capabilities": {"structured_output": "supported", "usage": "supported"},
}
LUNA = {
    "name": "luna",
    "argv": ["/usr/bin/luna", "--print", "{prompt}"],
    "model_argv": ["--model"],
    "capabilities": {"pty": "supported", "memory": "B"},
}


def two_harness_doc() -> dict[str, object]:
    return {
        "version": 1,
        "adapters": [PI, LUNA],
        "models": [
            {"harness": "pi", "model": "opus-5", "usage": "supported"},
            {"harness": "luna", "model": "cheap-1"},
        ],
        "tiers": {"pi/opus-5": "frontier", "cheap-1": "cheap"},
        "defaults": {
            "planner": {"harness": "pi", "model": "opus-5"},
            "initiative": {"harness": "luna", "model": "cheap-1"},
        },
    }


# --- unconfigured and malformed ---------------------------------------------


def test_missing_configuration_projects_instead_of_raising(tmp_path: Path) -> None:
    projection = Kitchen.load(tmp_path).projection()
    assert projection.configured is False
    assert projection.ready is False
    assert projection.adapters == []
    assert any("no adapter is declared" in blocker for blocker in projection.blockers)
    # still serializable for an inspection surface
    assert json.loads(projection.model_dump_json())["version"] == 1


def test_malformed_document_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / ".herdsman" / "kitchen.json"
    path.parent.mkdir(parents=True)
    _ = path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KitchenConfigError, match="invalid JSON"):
        _ = Kitchen.load(tmp_path)


def test_validation_failure_is_path_qualified(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", {"adapters": [{"name": "pi", "argv": ["x"]}]})
    with pytest.raises(KitchenConfigError, match=r"adapters\.0"):
        _ = Kitchen.load(tmp_path)


def test_argv_needs_exactly_one_prompt_placeholder() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _ = Adapter(name="pi", argv=["/usr/bin/pi", "--print", "{prompt}", "{prompt}"])
    with pytest.raises(ValueError, match="unknown placeholder"):
        _ = Adapter(name="pi", argv=["/usr/bin/pi", "{model}", "{prompt}"])


def test_unknown_references_are_refused() -> None:
    with pytest.raises(ValueError, match="undeclared adapter"):
        _ = Kitchen(models=[ModelEntry(harness="ghost", model="m")])
    with pytest.raises(ValueError, match="not in the model catalog"):
        _ = Kitchen(
            adapters=[Adapter.model_validate(PI)],
            models=[ModelEntry(harness="pi", model="opus-5")],
            defaults=Defaults(planner=Assignment(harness="pi", model="absent")),
        )


def test_declaring_a_tier_on_a_model_is_refused() -> None:
    with pytest.raises(ValueError, match="tiers map"):
        _ = Kitchen(
            adapters=[Adapter.model_validate(PI)],
            models=[ModelEntry(harness="pi", model="opus-5", tier="frontier")],
        )


# --- unknown stays unknown ---------------------------------------------------


def test_capabilities_and_prices_default_to_unknown_not_false() -> None:
    capabilities = Capabilities()
    assert capabilities.structured_output == "unknown"
    assert capabilities.resume == "unknown"
    assert capabilities.usage == "unknown"
    assert capabilities.pty == "unknown"
    assert capabilities.memory is None
    entry = ModelEntry(harness="pi", model="opus-5")
    assert (entry.usage, entry.counting, entry.price, entry.tier) == (
        "unknown", "unknown", None, None,
    )
    assert json.loads(entry.model_dump_json())["usage"] == "unknown"


def test_tier_comes_only_from_the_tier_map(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    assert kitchen.tier_of("pi", "opus-5") == "frontier"
    # a frontier-sounding name with no tier entry stays unknown
    assert kitchen.tier_of("pi", "opus-5-frontier-max") is None


def test_catalog_holds_only_declared_and_discovered_entries(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    assert [entry.key for entry in kitchen.catalog()] == [
        ("luna", "cheap-1"), ("pi", "opus-5"),
    ]
    discovered = ModelEntry(harness="pi", model="sonnet-5", source="discovered")
    catalog = kitchen.catalog([discovered])
    assert ("pi", "sonnet-5") in [entry.key for entry in catalog]
    assert [entry.tier for entry in catalog if entry.key == ("pi", "sonnet-5")] == [None]


def test_equal_model_labels_on_two_harnesses_do_not_collapse() -> None:
    kitchen = Kitchen(
        adapters=[Adapter.model_validate(PI), Adapter.model_validate(LUNA)],
        models=[
            ModelEntry(harness="pi", model="shared"),
            ModelEntry(harness="luna", model="shared"),
        ],
    )
    assert len(kitchen.catalog()) == 2


# --- resolution --------------------------------------------------------------


def test_two_harnesses_split_frontier_planning_from_execution(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    planner = kitchen.resolve_planner()
    executor = kitchen.resolve_assignment()
    assert planner.assignment == Assignment(harness="pi", model="opus-5")
    assert planner.tier == "frontier"
    assert planner.source == "defaults.planner"
    assert executor.assignment == Assignment(harness="luna", model="cheap-1")
    assert executor.source == "defaults.initiative"


def test_one_harness_carries_two_model_assignments(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", {
        "adapters": [PI],
        "models": [
            {"harness": "pi", "model": "opus-5"},
            {"harness": "pi", "model": "haiku-4"},
        ],
        "tiers": {"pi/opus-5": "frontier"},
        "defaults": {
            "planner": {"harness": "pi", "model": "opus-5"},
            "initiative": {"harness": "pi", "model": "haiku-4"},
        },
    })
    kitchen = Kitchen.load(tmp_path)
    assert kitchen.resolve_planner().assignment.model == "opus-5"
    assert kitchen.resolve_assignment().assignment.model == "haiku-4"


def test_role_assignment_wins_over_initiative_default_and_override_wins_over_both() -> None:
    kitchen = Kitchen(
        adapters=[Adapter.model_validate(PI)],
        models=[
            ModelEntry(harness="pi", model="a"),
            ModelEntry(harness="pi", model="b"),
            ModelEntry(harness="pi", model="c"),
        ],
        defaults=Defaults(
            initiative=Assignment(harness="pi", model="a"),
            roles={"reviewer": Assignment(harness="pi", model="b")},
        ),
    )
    assert kitchen.resolve_assignment(role="reviewer").assignment.model == "b"
    assert kitchen.resolve_assignment(role="implementer").assignment.model == "a"
    override = Assignment(harness="pi", model="c")
    resolved = kitchen.resolve_assignment(role="reviewer", override=override)
    assert (resolved.assignment.model, resolved.source) == ("c", "plan-override")


def test_unconfigured_defaults_refuse_with_a_useful_message() -> None:
    kitchen = Kitchen()
    with pytest.raises(KitchenConfigError, match="no frontier planner"):
        _ = kitchen.resolve_planner()
    with pytest.raises(KitchenConfigError, match="no executor assignment"):
        _ = kitchen.resolve_assignment()


# --- fallbacks ---------------------------------------------------------------


def base_for_fallbacks(chains: list[FallbackChain], tiers: dict[str, str]) -> Kitchen:
    return Kitchen(
        adapters=[Adapter.model_validate(PI)],
        models=[ModelEntry(harness="pi", model=name) for name in "abc"],
        tiers=tiers,
        fallbacks=chains,
    )


def test_only_declared_candidates_are_eligible() -> None:
    primary = Assignment(harness="pi", model="a")
    kitchen = base_for_fallbacks(
        [FallbackChain(primary=primary, candidates=[Assignment(harness="pi", model="b")])],
        {},
    )
    assert kitchen.fallback_candidates(primary) == [Assignment(harness="pi", model="b")]
    assert kitchen.fallback_candidates(Assignment(harness="pi", model="c")) == []


def test_fallback_may_not_escalate_to_a_frontier_tier() -> None:
    with pytest.raises(ValueError, match="may not escalate"):
        _ = base_for_fallbacks(
            [FallbackChain(
                primary=Assignment(harness="pi", model="a"),
                candidates=[Assignment(harness="pi", model="b")],
            )],
            {"pi/a": "cheap", "pi/b": "frontier"},
        )


def test_a_frontier_primary_may_fall_back_within_frontier() -> None:
    kitchen = base_for_fallbacks(
        [FallbackChain(
            primary=Assignment(harness="pi", model="a"),
            candidates=[Assignment(harness="pi", model="b")],
        )],
        {"pi/a": "frontier", "pi/b": "frontier"},
    )
    assert len(kitchen.fallback_candidates(Assignment(harness="pi", model="a"))) == 1


def test_cycles_duplicates_and_empty_chains_are_refused() -> None:
    a, b = Assignment(harness="pi", model="a"), Assignment(harness="pi", model="b")
    with pytest.raises(ValueError, match="fallback cycle"):
        _ = base_for_fallbacks(
            [
                FallbackChain(primary=a, candidates=[b]),
                FallbackChain(primary=b, candidates=[a]),
            ],
            {},
        )
    with pytest.raises(ValueError, match="duplicate"):
        _ = base_for_fallbacks([FallbackChain(primary=a, candidates=[b, b])], {})
    with pytest.raises(ValueError, match="own primary"):
        _ = base_for_fallbacks([FallbackChain(primary=a, candidates=[a])], {})
    with pytest.raises(ValueError, match="no candidates"):
        _ = base_for_fallbacks([FallbackChain(primary=a, candidates=[])], {})


# --- readiness ---------------------------------------------------------------


def test_readiness_combines_declarations_with_discovery(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    states = {
        item.harness: item
        for item in kitchen.readiness([
            HarnessFacts(harness="pi", executable="/usr/bin/pi", version="1.2", health="healthy"),
            HarnessFacts(harness="luna", executable=None, detail="not on PATH"),
            HarnessFacts(harness="stray", executable="/usr/bin/stray", health="healthy"),
        ])
    }
    assert states["pi"].state == "ready"
    assert states["pi"].version == "1.2"
    assert states["luna"].state == "unavailable"
    assert states["luna"].action
    assert states["stray"].state == "unconfigured"


def test_unknown_health_degrades_rather_than_passing(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    facts = [HarnessFacts(harness="pi", executable="/usr/bin/pi")]
    states = {item.harness: item.state for item in kitchen.readiness(facts)}
    assert states == {"pi": "degraded", "luna": "unknown"}


def test_projection_is_ready_only_when_both_defaults_resolve(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    kitchen = Kitchen.load(tmp_path)
    healthy = [
        HarnessFacts(harness=name, executable=f"/usr/bin/{name}", health="healthy")
        for name in ("pi", "luna")
    ]
    assert kitchen.projection(facts=healthy).ready is True
    partial = kitchen.projection(facts=healthy[:1])
    assert partial.ready is False
    assert any("luna" in blocker for blocker in partial.blockers)


# --- legacy compatibility and persistence ------------------------------------


def test_legacy_files_load_read_only(tmp_path: Path) -> None:
    _ = write(tmp_path, "luna.json", {"binary": "/opt/luna"})
    _ = write(tmp_path, "harnesses.json", {
        "pi": {"argv": ["/usr/bin/pi", "--print", "{prompt}"], "model_argv": ["--model"]}
    })
    _ = write(tmp_path, "models.json", {"cheap-1": "cheap"})
    _ = write(tmp_path, "memory.json", {"harnesses": {"luna": "B"}})
    before = {
        name: (tmp_path / ".herdsman" / name).read_bytes()
        for name in ("luna.json", "harnesses.json", "models.json", "memory.json")
    }
    kitchen = Kitchen.load(tmp_path)
    assert sorted(adapter.name for adapter in kitchen.adapters) == ["luna", "pi"]
    luna = kitchen.adapter("luna")
    assert luna is not None
    assert luna.argv[0] == "/opt/luna"
    assert luna.capabilities.memory == "B"
    assert luna.source == "legacy"
    assert kitchen.tiers == {"cheap-1": "cheap"}
    assert kitchen.notes
    for name, body in before.items():
        assert (tmp_path / ".herdsman" / name).read_bytes() == body
    assert not (tmp_path / ".herdsman" / "kitchen.json").exists()


def test_canonical_declarations_shadow_legacy_ones(tmp_path: Path) -> None:
    _ = write(tmp_path, "luna.json", {"binary": "/opt/luna"})
    _ = write(tmp_path, "kitchen.json", {"adapters": [LUNA]})
    kitchen = Kitchen.load(tmp_path)
    luna = kitchen.adapter("luna")
    assert luna is not None
    assert luna.argv[0] == "/usr/bin/luna"
    assert any("shadowed" in note for note in kitchen.notes)


def test_save_writes_project_local_and_rejects_a_stale_revision(tmp_path: Path) -> None:
    kitchen = Kitchen.model_validate(two_harness_doc())
    revision = kitchen.save(tmp_path)
    path = tmp_path / ".herdsman" / "kitchen.json"
    assert path.exists()
    assert Kitchen.load(tmp_path).revision == revision
    assert not list(tmp_path.glob("*.json"))

    edited = kitchen.model_copy(update={"tiers": {"pi/opus-5": "frontier"}})
    _ = edited.save(tmp_path, expect_revision=revision)
    with pytest.raises(KitchenConfigError, match="changed since it was read"):
        _ = edited.save(tmp_path, expect_revision=revision)


def test_revision_ignores_load_time_notes(tmp_path: Path) -> None:
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    _ = write(tmp_path, "models.json", {"cheap-1": "cheap"})
    loaded = Kitchen.load(tmp_path)
    assert loaded.notes
    assert loaded.revision == Kitchen.model_validate(two_harness_doc()).revision


def test_context_warning_defaults_to_the_historical_value(tmp_path: Path) -> None:
    assert Kitchen().context_warning_tokens == 2000
    _ = write(tmp_path, "kitchen.json", two_harness_doc())
    # An older document without the field keeps loading, unchanged.
    assert Kitchen.load(tmp_path).context_warning_tokens == 2000


def test_context_warning_is_project_local_and_persists(tmp_path: Path) -> None:
    doc = two_harness_doc()
    _ = write(tmp_path, "kitchen.json", {**doc, "context_warning_tokens": 5000})
    loaded = Kitchen.load(tmp_path)
    assert loaded.context_warning_tokens == 5000
    projection = loaded.projection()
    assert projection.context_warning_tokens == 5000
    # And a value that cannot mean anything is refused.
    with pytest.raises(KitchenConfigError):
        _ = Kitchen.load(write(tmp_path, "kitchen.json", {**doc, "context_warning_tokens": 0}))
