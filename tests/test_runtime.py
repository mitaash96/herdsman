import asyncio
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from herdsman.classes import (
    Assignment,
    AttemptStarted,
    Checkpoint,
    CheckpointRecorded,
    Event,
    FailureRecord,
    InitiativeFailed,
    InitiativeSettled,
    InitiativeSpec,
    MemoryLeaf,
    OperatorAnswered,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    SubtaskAdvanced,
)
from herdsman.runtime import (
    CompletionError,
    FailureDelta,
    LunaConfigError,
    PiFrontierPlanner,
    PlannerError,
    TaskPacket,
    _MAX_CONTEXT_BRIEF,  # pyright: ignore[reportPrivateUsage]
    _MAX_FAILURE_CHARS,  # pyright: ignore[reportPrivateUsage]
    _MAX_FAILURE_DELTAS,  # pyright: ignore[reportPrivateUsage]
    compile_task_packet,
    completion_from_detail,
    executor_command,
    proposal_from_result,
    recalibration_context,
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
        self._payload: bytes = payload

    async def read(self) -> bytes:
        return self._payload


class _StubProcess:
    """A completed planner subprocess: its argv is the evidence under test."""

    def __init__(self, payload: bytes) -> None:
        self.returncode: int = 0
        self.stdout: _StubStream = _StubStream(payload)
        self._payload: bytes = payload
        self.killed: bool = False

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
    # An explicit orchestration category overrides the harness's own claim,
    # which is attribution, not a trusted measurement, while the harness's
    # source, phase, and counts survive.
    claimed = usage_from_result(
        {"usage": {**payload["usage"], "category": "planning"}},
        category="recalibration_replay",
    )
    assert claimed is not None
    assert claimed.category == "recalibration_replay"
    assert (claimed.source, claimed.phase) == ("harness", "actual")
    assert (claimed.input_tokens, claimed.output_tokens) == (3, 4)
    # An ordinary call still preserves a harness-reported category.
    declared = usage_from_result(
        {"usage": {**payload["usage"], "category": "execution"}}
    )
    assert declared is not None
    assert declared.category == "execution"


def test_a_revision_proposal_carries_the_recalibration_usage_category() -> None:
    initiatives: list[dict[str, object]] = [
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
            "usage": {
                "input_tokens": 5,
                "output_tokens": 6,
                "source": "harness",
                "category": "planning",
            },
        },
        plan_id="plan_1",
        at=at,
        version=2,
        usage_category="recalibration_replay",
    )

    assert proposal.usage is not None
    assert proposal.usage.category == "recalibration_replay"
    assert proposal.usage.total_tokens == 11
    assert proposal.version == 2
    # A silent harness measures nothing; the plan gets no invented usage.
    silent = proposal_from_result(
        {"initiatives": initiatives}, plan_id="plan_1", at=at, version=2
    )
    assert silent.usage is None


def test_a_revision_may_depend_on_fixed_ids_but_may_not_redeclare_them() -> None:
    """Fixed anchors are server-side ids: dependencies may name them, the
    model may not return them."""
    at = datetime(2026, 9, 11, tzinfo=UTC)
    residual = {
        "id": "init_residual",
        "name": "residual",
        "brief": "finish the rest",
        "assignment": {"harness": "luna", "model": "cheap-1"},
        "depends_on": ["init_fixed"],
    }

    proposal = proposal_from_result(
        {"initiatives": [residual]},
        plan_id="plan_1",
        at=at,
        version=2,
        known_ids=["init_fixed"],
    )

    assert [spec.id for spec in proposal.initiatives] == ["init_residual"]
    with pytest.raises(PlannerError, match="re-declared fixed"):
        _ = proposal_from_result(
            {"initiatives": [{**residual, "id": "init_fixed"}]},
            plan_id="plan_1",
            at=at,
            version=2,
            known_ids=["init_fixed"],
        )


def _recalibration_plan() -> Plan:
    """A settled anchor, a live anchor, a partial node, and an untouched node."""
    at = datetime(2026, 9, 11, tzinfo=UTC)
    api = InitiativeSpec(
        id="init_a",
        name="api",
        brief="FIXED_BRIEF_SENTINEL: add a health endpoint",
        assignment=Assignment(harness="luna", model="cheap-1"),
        routes=Routes(writes=["src/api/**"]),
        subtasks=["write the route"],
    )
    partial = InitiativeSpec(
        id="init_b",
        name="partial",
        brief="PARTIAL_BRIEF_SENTINEL: keep going on the migration",
        assignment=Assignment(harness="luna", model="cheap-1"),
        routes=Routes(writes=["src/db/**"]),
        subtasks=["first step", "second step"],
        token_cap=5000,
    )
    untouched = InitiativeSpec(
        id="init_c",
        name="later",
        brief="nothing started yet",
        assignment=Assignment(harness="luna", model="cheap-1"),
        depends_on=["init_a"],
    )
    live = InitiativeSpec(
        id="init_d",
        name="live",
        brief="still running",
        assignment=Assignment(harness="luna", model="cheap-1"),
        depends_on=["init_a"],
    )
    events: list[Event] = [
        PlanCreated(
            plan_id="plan_1", at=at, brief="PLAN_BRIEF_SENTINEL: revise the sprint"
        ),
        PlanProposed(
            plan_id="plan_1",
            at=at,
            version=1,
            initiatives=[api, partial, untouched, live],
        ),
        PlanApproved(plan_id="plan_1", at=at, version=1),
        AttemptStarted(
            plan_id="plan_1",
            at=at,
            attempt_id="att_a",
            initiative_id="init_a",
            assignment=api.assignment,
        ),
        CheckpointRecorded(
            plan_id="plan_1",
            at=at,
            checkpoint=Checkpoint(id="cp_a", attempt_id="att_a", exit_code=0),
        ),
        InitiativeSettled(
            plan_id="plan_1", at=at, initiative_id="init_a", checkpoint_id="cp_a"
        ),
        AttemptStarted(
            plan_id="plan_1",
            at=at,
            attempt_id="att_b",
            initiative_id="init_b",
            assignment=partial.assignment,
        ),
        SubtaskAdvanced(
            plan_id="plan_1",
            at=at,
            initiative_id="init_b",
            subtask_id="init_b.1",
            state="done",
        ),
        OperatorAnswered(
            plan_id="plan_1",
            at=at,
            attempt_id="att_b",
            subject="init_b.question",
            answer="MEMORY_CLAIM_SENTINEL: use postgres",
        ),
        # The attempt is closed by its recorded checkpoint — pending, never
        # approved — so the partial node stays revisable while its done claim
        # remains immutable.
        CheckpointRecorded(
            plan_id="plan_1",
            at=at,
            checkpoint=Checkpoint(id="cp_b", attempt_id="att_b", exit_code=0),
        ),
        InitiativeFailed(
            plan_id="plan_1",
            at=at,
            initiative_id="init_b",
            reason="boom reason",
            evidence=[".herdsman/artifacts/att_b.diag.patch"],
        ),
        AttemptStarted(
            plan_id="plan_1",
            at=at,
            attempt_id="att_d",
            initiative_id="init_d",
            assignment=live.assignment,
        ),
    ]
    return Plan.fold(events)


def test_the_revision_prompt_returns_remaining_work_only() -> None:
    prompt = recalibration_prompt('{"plan_id":"plan_1"}')

    assert prompt.endswith('CONTEXT={"plan_id":"plan_1"}')
    assert "covering only the revised remaining work" in prompt
    assert "do not re-declare any entry listed under fixed" in prompt
    assert "may name a fixed id or another returned id" in prompt
    assert "revises that node in place" in prompt
    assert "preserved verbatim under that node's original id" in prompt
    assert "never omitted, renamed, or moved to another node" in prompt
    assert "Use harness luna." in prompt


def test_recalibration_context_anchors_fixed_work_and_keeps_remaining_compact() -> None:
    plan = _recalibration_plan()
    limit = 32

    context = recalibration_context(plan, max_brief_chars=limit)
    payload = cast(dict[str, object], json.loads(context))

    assert payload["plan_id"] == "plan_1"
    assert payload["version"] == 1
    assert payload["approval"] == "approved"
    assert payload["brief"] == plan.brief[:limit]
    assert len(plan.brief) > limit

    fixed = cast(list[dict[str, object]], payload["fixed"])
    assert [item["id"] for item in fixed] == ["init_a", "init_d"]
    anchor = fixed[0]
    # Fixed anchors are compact identities: no brief, spec, or history.
    assert set(anchor) == {"id", "name", "digest", "state", "attempts", "checkpoint_id"}
    assert anchor["digest"] == plan.initiatives["init_a"].spec.digest
    assert anchor["state"] == "settled"
    assert anchor["attempts"] == 1
    assert anchor["checkpoint_id"] == "cp_a"
    running = fixed[1]
    assert running["state"] == "running"
    assert running["attempts"] == 1
    assert running["checkpoint_id"] is None

    remaining = cast(list[dict[str, object]], payload["remaining"])
    assert [item["id"] for item in remaining] == ["init_b", "init_c"]
    partial = remaining[0]
    assert partial["name"] == "partial"
    assert partial["brief"] == plan.initiatives["init_b"].spec.brief[:limit]
    assert partial["assignment"] == {"harness": "luna", "model": "cheap-1"}
    assert partial["routes"] == {"reads": [], "writes": ["src/db/**"]}
    assert partial["subtasks"] == ["first step", "second step"]
    assert partial["depends_on"] == []
    assert partial["state"] == "failed"
    assert partial["attempts"] == 1
    assert partial["failures"] == ["[att_b] error: boom reason"]
    assert partial["evidence"] == [".herdsman/artifacts/att_b.diag.patch"]
    # Only an approved checkpoint freezes a node: this pending one does not,
    # and the done claim stays immutable evidence against the residual the
    # model may still revise.
    assert partial["completed_claims"] == [
        {"id": "init_b.1", "claim": "first step", "state": "done"}
    ]
    assert partial["approved_checkpoint_ids"] == []
    assert partial["token_cap"] == 5000
    assert "contract" not in partial and "policy" not in partial
    untouched = remaining[1]
    assert untouched["state"] == "pending"
    assert untouched["attempts"] == 0
    assert untouched["depends_on"] == ["init_a"]
    assert untouched["failures"] == [] and untouched["evidence"] == []
    assert untouched["completed_claims"] == []
    assert untouched["approved_checkpoint_ids"] == []

    # Unrelated bodies stay out of the planner context by construction.
    assert "FIXED_BRIEF_SENTINEL" not in context
    assert "MEMORY_CLAIM_SENTINEL" not in context
    # Folding the same events again produces the identical context, and the
    # default brief bound leaves a short brief whole.
    assert context == recalibration_context(_recalibration_plan(), max_brief_chars=limit)
    full = cast(dict[str, object], json.loads(recalibration_context(plan)))
    assert full["brief"] == plan.brief
    assert len(plan.brief) <= _MAX_CONTEXT_BRIEF


def test_recalibration_context_bounds_failure_lines_and_evidence() -> None:
    plan = _recalibration_plan()
    partial = plan.initiatives["init_b"]
    attempt_id = partial.attempts[-1].id
    transcript = "".join(f"pane line {n:04d}\n" for n in range(300))
    for index in range(8):
        plan.failure_signatures[("init_b", f"check_{index}", transcript)] = FailureRecord(
            count=1, attempts=[attempt_id]
        )
    # Signatures of another node or another attempt are not this one's evidence.
    plan.failure_signatures[("init_b", "old_check", "old")] = FailureRecord(
        count=1, attempts=["att_old"]
    )
    plan.failure_signatures[("init_c", "other_node", "other")] = FailureRecord(
        count=1, attempts=[attempt_id]
    )
    partial.failures[-1].evidence = [
        f".herdsman/artifacts/{index}.patch" for index in range(8)
    ]

    payload = cast(dict[str, object], json.loads(recalibration_context(plan)))
    remaining = {
        cast(str, item["id"]): item
        for item in cast(list[dict[str, object]], payload["remaining"])
    }
    failures = cast(list[str], remaining["init_b"]["failures"])
    evidence = cast(list[str], remaining["init_b"]["evidence"])

    assert len(failures) == _MAX_FAILURE_DELTAS
    assert all(line.startswith("[att_b] ") for line in failures)
    # Oldest bounded lines are dropped; the freshest four checks plus the
    # stand-alone error survive, one line each.
    assert sum("check_" in line for line in failures) == 4
    assert any(line.endswith("error: boom reason") for line in failures)
    assert all(len(line) <= _MAX_FAILURE_CHARS for line in failures)
    assert all("\n" not in line and "\t" not in line for line in failures)
    assert "pane line 0299" not in "".join(failures)
    assert "old_check" not in "".join(failures)
    assert "other_node" not in "".join(failures)
    assert evidence == [f".herdsman/artifacts/{index}.patch" for index in range(3, 8)]
    assert all(len(path) <= _MAX_FAILURE_CHARS for path in evidence)
