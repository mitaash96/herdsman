import asyncio
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path

import pytest

from herdsman.classes import Assignment, InitiativeSpec, MemoryLeaf
from herdsman.runtime import (
    CompletionError,
    FailureDelta,
    LunaConfigError,
    PiFrontierPlanner,
    TaskPacket,
    compile_task_packet,
    completion_from_detail,
    executor_command,
    proposal_from_result,
    recalibration_prompt,
    resolve_luna_binary,
    usage_from_result,
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


def second_harness_packet() -> TaskPacket:
    return compile_task_packet(
        InitiativeSpec(
            id="init_1",
            name="one node",
            brief="make one change",
            assignment=Assignment(harness="pi", model="frontier-9"),
        )
    )


def write_harness_registry(tmp_path: Path, mapping: object) -> Path:
    directory = tmp_path / ".herdsman"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "harnesses.json"
    _ = path.write_text(json.dumps(mapping))
    return path


def test_a_non_luna_harness_compiles_the_registered_argv_and_model(
    tmp_path: Path,
) -> None:
    """Selection is solely by Assignment.harness, from the project-local map."""
    _ = write_harness_registry(
        tmp_path,
        {
            "pi": {
                "argv": ["/opt/pi", "--no-session", "--print", "{prompt}"],
                "model_argv": ["--model"],
            }
        },
    )

    command = executor_command(second_harness_packet(), project_root=tmp_path)

    argv = shlex.split(command)
    assert argv[:5] == ["/opt/pi", "--no-session", "--print", "--model", "frontier-9"]
    assert "TASK_PACKET=" in argv[-1]

    unmodelled = compile_task_packet(
        InitiativeSpec(
            id="init_1",
            name="one node",
            brief="make one change",
            assignment=Assignment(harness="pi", model=""),
        )
    )
    argv = shlex.split(executor_command(unmodelled, project_root=tmp_path))
    assert argv[:3] == ["/opt/pi", "--no-session", "--print"]
    assert "--model" not in argv


def test_an_unconfigured_harness_fails_loudly_at_command_compilation(
    tmp_path: Path,
) -> None:
    """A reassignment onto an unmapped harness cannot strand a live attempt."""
    _ = write_harness_registry(
        tmp_path, {"pi": {"argv": ["/opt/pi", "--print", "{prompt}"]}}
    )
    unconfigured = compile_task_packet(
        InitiativeSpec(
            id="init_1",
            name="one node",
            brief="make one change",
            assignment=Assignment(harness="claude", model="default"),
        )
    )

    with pytest.raises(LunaConfigError, match="'claude' is not configured"):
        _ = executor_command(unconfigured, project_root=tmp_path)


def test_the_harness_registry_rejects_malformed_templates(tmp_path: Path) -> None:
    """Empty or malformed config and unknown placeholders are typed errors."""
    harnessless = second_harness_packet()

    with pytest.raises(LunaConfigError, match="missing at"):
        _ = executor_command(harnessless, project_root=tmp_path)

    _ = write_harness_registry(tmp_path, {})
    with pytest.raises(LunaConfigError, match="non-empty object"):
        _ = executor_command(harnessless, project_root=tmp_path)

    _ = write_harness_registry(
        tmp_path, {"pi": {"argv": ["/opt/pi", "--print", "{model}"]}}
    )
    with pytest.raises(LunaConfigError, match="unknown placeholder"):
        _ = executor_command(harnessless, project_root=tmp_path)

    _ = write_harness_registry(
        tmp_path, {"pi": {"argv": ["/opt/pi", "--print", "{prompt}", "{prompt}"]}}
    )
    with pytest.raises(LunaConfigError, match="exactly one"):
        _ = executor_command(harnessless, project_root=tmp_path)

    _ = write_harness_registry(tmp_path, {"pi": {"argv": "/opt/pi"}})
    with pytest.raises(LunaConfigError, match="argv array"):
        _ = executor_command(harnessless, project_root=tmp_path)

    _ = write_harness_registry(
        tmp_path,
        {"pi": {"argv": ["/opt/pi", "{prompt}"], "model_argv": ["--model", "{model}"]}},
    )
    with pytest.raises(LunaConfigError, match="never substituted"):
        _ = executor_command(harnessless, project_root=tmp_path)


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


def test_a_retry_packet_carries_bounded_failure_deltas_not_a_transcript() -> None:
    """Retry evidence: at most the last few failures, one bounded line each."""
    spec = InitiativeSpec(
        id="init_1",
        name="one node",
        brief="make one change",
        assignment=Assignment(harness="luna", model="cheap-1"),
    )
    transcript = "".join(f"pane output line {n:04d}\n" for n in range(200))
    failures = [
        FailureDelta(attempt_id="attempt_1", error="first failure", check="pytest"),
        FailureDelta(attempt_id="attempt_2", error=transcript, check="lint"),
    ] + [
        FailureDelta(attempt_id=f"attempt_{n}", error=f"failure {n}", check="pytest")
        for n in range(3, 9)
    ] + [FailureDelta(attempt_id="attempt_9", error="third\tfailure\nreason")]

    compiled = compile_task_packet(spec, failures=failures)

    # Bound: only the five most recent deltas survive.
    assert len(compiled.failures) == 5
    assert "[attempt_1]" not in compiled.json()
    assert "[attempt_2]" not in compiled.json()
    assert compiled.failures[0].startswith("[attempt_5]")
    assert compiled.failures[-1] == "[attempt_9] unknown-check: third failure reason"
    # The transcript is reduced to one bounded line; its tail is gone.
    assert "pane output line 0199" not in compiled.json()
    for line in compiled.failures:
        assert "\n" not in line and "\t" not in line and len(line) <= 400
    # A checkless failure stays attributable and one-line.
    assert "[attempt_5] pytest: failure 5" in compiled.failures
    # Structurally present in the packet JSON, deterministic across compiles.
    assert json.loads(compiled.json())["failures"] == list(compiled.failures)
    assert compile_task_packet(spec, failures=failures).json() == compiled.json()
    # Original contract unchanged by failure evidence; a first run is clean.
    assert compiled.brief == spec.brief
    assert compile_task_packet(spec).failures == ()
    assert json.loads(compile_task_packet(spec).json())["failures"] == []


class _StubStream:
    """The stdout surface a process fake exposes in place of a real pipe."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self) -> bytes:
        return self._payload


class _StubProcess:
    """A completed planner subprocess: its argv is the evidence under test."""

    def __init__(self, payload: bytes) -> None:
        self.returncode = 0
        self.stdout = _StubStream(payload)
        self._payload = payload
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._payload, b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def test_the_revision_call_keeps_propose_argv_and_carries_the_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegation through `_invoke` changes neither argv nor prompt shape."""
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*argv: str, **_kwargs: object) -> _StubProcess:
        calls.append(argv)
        return _StubProcess(b'{"initiatives":[]}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    planner = PiFrontierPlanner(binary="luna", model="frontier-9")

    async def scenario() -> tuple[object, object]:
        proposed = await planner.propose("build the thing")
        revised = await planner.recalibrate('{"plan_id":"plan_1"}')
        return proposed, revised

    proposed, revised = asyncio.run(scenario())

    assert proposed == {"initiatives": []}
    assert revised == {"initiatives": []}
    assert len(calls) == 2
    proposal_argv, revision_argv = (list(argv) for argv in calls)
    assert proposal_argv[:7] == [
        "luna",
        "--no-session",
        "--mode",
        "json",
        "--print",
        "--model",
        "frontier-9",
    ]
    assert proposal_argv[7].endswith("BRIEF=build the thing")
    assert "supervised frontier planner" in proposal_argv[7]
    # The revision call keeps the identical launch shape and sends the
    # remaining-work prompt over the compaction context, not a brief.
    assert revision_argv[:7] == proposal_argv[:7]
    assert revision_argv[7] == recalibration_prompt('{"plan_id":"plan_1"}')
    assert revision_argv[7].endswith('CONTEXT={"plan_id":"plan_1"}')
    assert "re-declare" in revision_argv[7]


def test_usage_stamping_keeps_defaults_and_the_recalibration_category() -> None:
    payload = {"usage": {"input_tokens": 3, "output_tokens": 4, "source": "harness"}}

    planning = usage_from_result(payload)
    recalibration = usage_from_result(payload, category="recalibration_replay")

    assert planning is not None
    assert planning.category == "planning"
    assert recalibration is not None
    assert recalibration.category == "recalibration_replay"
    # Unknown usage stays unknown: an absent block is never synthesized.
    assert usage_from_result({"initiatives": []}) is None
    assert usage_from_result({"usage": None}) is None
    # A harness-reported category wins over the caller's default.
    declared = usage_from_result(
        {"usage": {**payload["usage"], "category": "execution"}},
        category="recalibration_replay",
    )
    assert declared is not None
    assert declared.category == "execution"


def test_a_revision_proposal_carries_the_recalibration_usage_category() -> None:
    initiatives = [
        {
            "id": "init_1",
            "name": "one node",
            "brief": "make one change",
            "assignment": {"harness": "luna", "model": "cheap-1"},
            "depends_on": [],
        }
    ]
    at = datetime(2026, 9, 11, tzinfo=UTC)

    proposal = proposal_from_result(
        {
            "initiatives": initiatives,
            "usage": {"input_tokens": 5, "output_tokens": 6, "source": "harness"},
        },
        plan_id="plan_1",
        at=at,
        version=2,
        usage_category="recalibration_replay",
    )

    assert proposal.usage is not None
    assert proposal.usage.category == "recalibration_replay"
    assert proposal.version == 2
    # A silent harness measures nothing; the plan gets no invented usage.
    silent = proposal_from_result(
        {"initiatives": initiatives}, plan_id="plan_1", at=at, version=2
    )
    assert silent.usage is None
