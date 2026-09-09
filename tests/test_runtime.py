import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from herdsman.classes import Assignment, InitiativeSpec, MemoryLeaf
from herdsman.runtime import (
    CompletionError,
    LunaConfigError,
    PlannerError,
    TaskPacket,
    compile_task_packet,
    completion_from_detail,
    executor_command,
    resolve_luna_binary,
)


def packet() -> TaskPacket:
    return compile_task_packet(
        InitiativeSpec(
            id="init_1",
            name="one node",
            brief="make one change",
            assignment=Assignment(harness="luna", model="cheap-1"),
        )
    )


def test_luna_does_not_use_environment_or_installed_pi_as_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERDSMAN_LUNA_BINARY", "/installed/pi")

    with pytest.raises(LunaConfigError, match=".herdsman/luna.json"):
        _ = executor_command(packet(), project_root=tmp_path)


def test_luna_mapping_requires_exact_shape_and_uses_configured_binary(
    tmp_path: Path,
) -> None:
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir()
    _ = mapping.write_text(json.dumps({"binary": "/opt/luna"}))

    assert resolve_luna_binary(tmp_path) == "/opt/luna"
    command = executor_command(packet(), project_root=tmp_path)
    assert command.startswith("/opt/luna ")

    _ = mapping.write_text(json.dumps({"binary": "/opt/luna", "extra": True}))
    with pytest.raises(LunaConfigError, match="exactly"):
        _ = resolve_luna_binary(tmp_path)


def test_executor_rejects_non_luna_harness() -> None:
    non_luna = TaskPacket(
        initiative_id="init_1",
        name="one node",
        brief="make one change",
        assignment=Assignment(harness="planner text", model="cheap-1"),
        routes=packet().routes,
        subtasks=(),
    )

    with pytest.raises(PlannerError, match="explicit luna"):
        _ = executor_command(non_luna)


def test_completion_ignores_marker_inside_executor_echo() -> None:
    detail = {
        "text": (
            '/opt/luna --print "... HERDSMAN_CHECKPOINT not-json"\n'
            '  HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'
            '{"input_tokens":1,"output_tokens":2,"source":"harness"}}'
        )
    }

    completion = completion_from_detail(detail)

    assert completion is not None
    assert completion.exit_code == 0


def test_completion_ignores_partial_marker_and_reads_later_complete_marker() -> None:
    detail = {
        "text": (
            'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":\n'
            'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'
            '{"input_tokens":1,"output_tokens":2,"source":"harness"}}'
        )
    }

    completion = completion_from_detail(detail)

    assert completion is not None
    assert completion.exit_code == 0
    assert completion.usage.input_tokens == 1


def test_completion_ignores_partial_marker_without_completion() -> None:
    detail = {"text": 'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'}

    assert completion_from_detail(detail) is None


def test_completion_requires_harness_usage() -> None:
    detail = {
        "text": (
            'HERDSMAN_CHECKPOINT {"exit_code":0,"usage":'
            '{"input_tokens":1,"output_tokens":2,"source":"provider"}}'
        )
    }

    with pytest.raises(CompletionError, match="source must be harness"):
        _ = completion_from_detail(detail)


def test_luna_mapping_rejects_non_string_binary(tmp_path: Path) -> None:
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir()
    _ = mapping.write_text(json.dumps({"binary": 7}))

    with pytest.raises(LunaConfigError, match="non-empty string"):
        _ = resolve_luna_binary(tmp_path)


def test_a_packet_compiles_the_current_brief_and_assignment_overrides() -> None:
    """A retry compiles the task's current brief version and assignment."""
    spec = InitiativeSpec(
        id="init_1",
        name="one node",
        brief="make one change",
        assignment=Assignment(harness="luna", model="cheap-1"),
    )
    redirected = compile_task_packet(
        spec,
        brief="redirected brief",
        assignment=Assignment(harness="luna", model="big-1"),
    )
    assert redirected.brief == "redirected brief"
    assert redirected.assignment == Assignment(harness="luna", model="big-1")
    unchanged = compile_task_packet(spec)
    assert unchanged.brief == "make one change"
    assert unchanged.assignment == spec.assignment


def test_a_packet_carries_run_scoped_leaves_as_deterministic_lines() -> None:
    """Ground-truth interventions ride along as one line per leaf."""
    at = datetime(2026, 9, 9, tzinfo=UTC)
    leaves = [
        MemoryLeaf(
            id="leaf_1",
            subject="init_1.brief",
            claim="brief redirected to version 2",
            origin="redirect",
            at=at,
        ),
        MemoryLeaf(
            id="leaf_2",
            subject="tabs-or-spaces",
            claim="tabs",
            origin="operator-answer",
            at=at,
        ),
    ]
    spec = InitiativeSpec(
        id="init_1",
        name="one node",
        brief="make one change",
        assignment=Assignment(harness="luna", model="cheap-1"),
    )
    compiled = compile_task_packet(spec, leaves=leaves)
    assert compiled.memory == (
        "[redirect] init_1.brief: brief redirected to version 2",
        "[operator-answer] tabs-or-spaces: tabs",
    )
    assert json.loads(compiled.json())["memory"] == list(compiled.memory)
    # A first run has no interventions, so the packet stays lean.
    assert compile_task_packet(spec).memory == ()
