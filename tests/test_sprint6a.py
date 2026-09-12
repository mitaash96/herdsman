from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Self, final
from urllib.request import Request

import pytest
from typer.testing import CliRunner

from herdsman import cli
from herdsman.classes import (
    Assignment,
    AttemptStarted,
    TokenMeasurement,
    Checkpoint,
    CheckpointRecorded,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Usage,
    InitiativeSpec,
    Routes,
)
from herdsman.eval import (
    EvalMetrics,
    RealVariantRunner,
    Variant,
    deterministic_fixture_runner,
    evaluate_variants,
)
from herdsman.observability import burn_down, makespan_eta, select_measurements, token_ledger
from herdsman.repomap import RepoMapAdapter, RepoMapRequest
from herdsman.runtime import TaskPacket, packet_diff, preflight_packet


AT = datetime(2026, 9, 10, tzinfo=UTC)


def test_observability_cli_reads_daemon_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float | None]] = []

    @final
    class Response:
        def __init__(self) -> None:
            self.data = BytesIO(b'{"source":"daemon"}')

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.data.read()

    def fake_urlopen(request: Request, *, timeout: float | None) -> Response:
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(cli, "urlopen", fake_urlopen)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["status", "p"]).exit_code == 0
    assert runner.invoke(cli.app, ["tokens", "p"]).exit_code == 0
    assert runner.invoke(cli.app, ["watch", "p"]).exit_code == 0
    assert all("/plans/p/" in url for url, _timeout in calls)
    assert calls[0][0].endswith("/status")
    assert calls[1][0].endswith("/tokens")
    assert calls[2][0].endswith("/status")


def test_packet_receipt_and_ledger_are_provenance_preserving() -> None:
    spec = InitiativeSpec(
        id="a",
        name="A",
        brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
    )
    packet = TaskPacket("a", "A", "do it", spec.assignment, spec.routes, ())
    receipt = packet.snapshot()
    events = [
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(
            plan_id="p",
            at=AT,
            attempt_id="att",
            initiative_id="a",
            assignment=spec.assignment,
            packet_tokens=receipt.total_tokens,
            packet_snapshot=receipt,
        ),
    ]
    plan = Plan.fold(events)
    ledger = token_ledger(plan)
    assert ledger.entries
    assert all(entry.provenance for entry in ledger.entries)
    assert ledger.provenance
    assert ledger.totals.provenance["preflight"]
    assert ledger.totals.derivation["preflight"]
    assert ledger.totals.preflight == receipt.total_tokens
    counted = preflight_packet(packet, counter=len)
    assert counted.total_tokens == len(packet.json())
    assert sum(section.total_tokens for section in counted.sections) == counted.total_tokens
    diff = packet_diff(receipt, counted)
    assert diff.provenance
    assert diff.derivation


def test_packet_burn_is_attributed_to_initiative_budget():
    spec = InitiativeSpec(
        id="a",
        name="A",
        brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
        token_cap=100,
    )
    packet = TaskPacket("a", "A", "do it", spec.assignment, spec.routes, ())
    receipt = packet.snapshot()
    events = [
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att", initiative_id="a",
            assignment=spec.assignment, packet_tokens=receipt.total_tokens,
            packet_snapshot=receipt,
        ),
    ]
    plan = Plan.fold(events)
    ledger = token_ledger(plan)
    assert all(entry.initiative_id == "a" for entry in ledger.entries)
    assert burn_down(plan, ledger).remaining_initiative_caps["a"] == 100 - receipt.total_tokens
    completed = Plan.fold(events + [
        CheckpointRecorded(
            plan_id="p", at=AT, checkpoint=Checkpoint(
                id="cp", attempt_id="att", exit_code=0,
                usage=Usage(input_tokens=3, output_tokens=2, source="harness"),
            )
        )
    ])
    assert burn_down(completed).accounted_tokens == 5
    assert token_ledger(completed).accounted_tokens == 5


def test_usage_precedence_selects_one_semantic_measurement():
    rows = [
        TokenMeasurement(entry_id="estimate", semantic_work_id="work", plan_id="p", phase="estimate", source="estimate", category="execution", input_tokens=1),
        TokenMeasurement(entry_id="tokens", semantic_work_id="work", plan_id="p", phase="preflight", source="tokenizer", category="execution", input_tokens=2),
        TokenMeasurement(entry_id="gateway", semantic_work_id="work", plan_id="p", phase="preflight", source="gateway", gateway_used=True, category="execution", input_tokens=3),
        TokenMeasurement(entry_id="native", semantic_work_id="work", plan_id="p", phase="preflight", source="provider", category="execution", input_tokens=4),
        TokenMeasurement(entry_id="actual", semantic_work_id="work", plan_id="p", phase="actual", source="harness", category="execution", input_tokens=5),
    ]
    selected = select_measurements(rows)
    assert len(selected) == 1
    assert selected[0].input_tokens == 5


def test_provider_preflight_outranks_packet_estimate_for_budget_burn():
    spec = InitiativeSpec(
        id="a", name="A", brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
    )
    packet = TaskPacket("a", "A", "do it", spec.assignment, spec.routes, ())
    receipt = packet.snapshot()
    usage = Usage(
        input_tokens=receipt.total_tokens + 7,
        source="provider",
        phase="preflight",
        provenance="provider native count",
    )
    plan = Plan.fold([
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att", initiative_id="a",
            assignment=spec.assignment, packet_tokens=receipt.total_tokens,
            packet_snapshot=receipt,
        ),
        CheckpointRecorded(
            plan_id="p", at=AT,
            checkpoint=Checkpoint(id="cp", attempt_id="att", usage=usage),
        ),
    ])
    assert plan.accounted_token_burn() == usage.total_tokens
    assert burn_down(plan).accounted_tokens == usage.total_tokens


def test_provider_actual_usage_is_productive():
    spec = InitiativeSpec(
        id="a", name="A", brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
    )
    plan = Plan.fold([
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(plan_id="p", at=AT, attempt_id="att", initiative_id="a", assignment=spec.assignment),
        CheckpointRecorded(
            plan_id="p", at=AT, checkpoint=Checkpoint(
                id="cp", attempt_id="att", exit_code=0,
                usage=Usage(input_tokens=4, output_tokens=2, source="provider", phase="actual"),
            )
        ),
    ])
    assert token_ledger(plan).productive_tokens == 6


def test_eval_variants_keep_the_overhead_receipt():
    result = evaluate_variants("same brief", runner=deterministic_fixture_runner)
    assert {case.variant for case in result.cases} == {"single-agent", "dag", "assignment"}
    assert all((case.overhead_ratio or 0) <= 0.20 for case in result.cases)
    assert all(case.receipt == "test-double" for case in result.cases)


def test_eval_executes_public_runner_and_requires_authoritative_provenance() -> None:
    calls: list[tuple[Variant, str]] = []

    def runner(variant: Variant, brief: str) -> EvalMetrics:
        calls.append((variant, brief))
        return EvalMetrics(
            variant=variant,
            pass_rate=1.0,
            wall_clock_seconds=1.5,
            productive_tokens=100,
            orchestration_tokens=10,
            usage_provenance=("fake authoritative provider",),
            receipt="measured",
        )

    result = evaluate_variants("real brief", runner=runner)
    assert len(calls) == 3
    assert all(case.receipt == "measured" for case in result.cases)
    assert EvalMetrics(
        variant="single-agent",
        pass_rate=1,
        wall_clock_seconds=1,
        productive_tokens=1,
        orchestration_tokens=0,
    ).receipt == "test-double"


def test_real_eval_runner_requires_explicit_variant_specs(tmp_path: Path) -> None:
    assignment = Assignment(harness="luna", model="configured")

    def specs(variant: Variant, brief: str) -> list[InitiativeSpec]:
        return [
            InitiativeSpec(
                id=variant,
                name=variant,
                brief=brief,
                assignment=assignment,
                routes=Routes(writes=["**"]),
            )
        ]

    runner = RealVariantRunner(tmp_path, specs=specs)
    assert runner.specs("dag", "same brief")[0].assignment.model == "configured"
    assert runner.specs("single-agent", "same brief")[0].routes.writes == ["**"]


def test_repomap_is_explicitly_unavailable_without_optional_dependency(
    tmp_path: Path,
) -> None:
    result = RepoMapAdapter(backend=None).build(
        RepoMapRequest(repo_root=tmp_path, token_budget=20)
    )
    assert result.available is False
    assert result.source == "unavailable"


def test_eta_requires_explicit_estimate_and_subtracts_running_elapsed():
    spec = InitiativeSpec(
        id="a", name="A", brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
        duration_estimate_seconds=10,
    )
    plan = Plan.fold([
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(plan_id="p", at=AT, attempt_id="att", initiative_id="a", assignment=spec.assignment),
    ])
    eta = makespan_eta(plan, now=AT.replace(second=7))
    assert eta.remaining_seconds == 3
    unknown_spec = spec.model_copy(update={"id": "b", "duration_estimate_seconds": None})
    unknown = Plan.fold([
        PlanCreated(plan_id="q", at=AT, brief="brief"),
        PlanProposed(plan_id="q", at=AT, version=1, initiatives=[unknown_spec]),
        PlanApproved(plan_id="q", at=AT, version=1),
    ])
    assert makespan_eta(unknown, now=AT).eta is None


def test_repomap_zero_budget_does_not_emit_or_invoke_backend(
    tmp_path: Path,
) -> None:
    @final
    class FakeRepoMap:
        def __init__(self) -> None:
            self.called = False

        def build(self, request: RepoMapRequest) -> str:
            del request
            self.called = True
            return "should not be emitted"

    backend = FakeRepoMap()
    result = RepoMapAdapter(backend=backend).build(
        RepoMapRequest(repo_root=tmp_path, token_budget=0)
    )
    assert result.text == ""
    assert result.tokens == 0
    assert backend.called is False


def test_repomap_cache_key_tracks_scope_content_and_changed_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = source / "a.py"
    _ = target.write_text("one", encoding="utf-8")
    adapter = RepoMapAdapter(backend=None)
    request = RepoMapRequest(repo_root=tmp_path, scope=("src",), changed_paths=("src/a.py",), token_budget=2)
    first = adapter.build(request)
    _ = target.write_text("two", encoding="utf-8")
    second = adapter.build(request)
    assert first.cache_key != second.cache_key


def test_repomap_fake_backend_is_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    @final
    class FakeRepoMap:
        def build(self, request: RepoMapRequest) -> str:
            del request
            return "zeta\\nalpha\\n" + ("x" * 200)

    request = RepoMapRequest(repo_root=tmp_path, scope=("herdsman",), token_budget=4)
    adapter = RepoMapAdapter(backend=FakeRepoMap())
    first = adapter.build(request)
    second = adapter.build(request)
    assert first == second
    assert first.available is True
    assert first.tokens is not None
    assert first.tokens <= 4
    assert first.hard_token_bound is False
    assert len(first.text) <= 16
    exact = RepoMapAdapter(backend=FakeRepoMap(), counter=len).build(request)
    assert exact.tokens is not None
    assert exact.tokens == len(exact.text) <= 4
    assert exact.hard_token_bound is True


def test_retired_nodes_keep_their_packet_and_usage_in_burn_and_ledger() -> None:
    spec = InitiativeSpec(
        id="a", name="A", brief="do it",
        assignment=Assignment(harness="luna", model="cheap"),
    )
    packet = TaskPacket("a", "A", "do it", spec.assignment, spec.routes, ())
    receipt = packet.snapshot()
    events = [
        PlanCreated(plan_id="p", at=AT, brief="brief"),
        PlanProposed(plan_id="p", at=AT, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=AT, version=1),
        AttemptStarted(
            plan_id="p", at=AT, attempt_id="att", initiative_id="a",
            assignment=spec.assignment, packet_tokens=receipt.total_tokens,
            packet_snapshot=receipt,
        ),
        CheckpointRecorded(
            plan_id="p", at=AT, checkpoint=Checkpoint(
                id="cp", attempt_id="att", exit_code=0,
                usage=Usage(input_tokens=30, output_tokens=20, source="harness"),
            )
        ),
    ]
    # A recalibration drops the unfinished node for a materially different one.
    plan = Plan.fold([
        *events,
        PlanProposed(
            plan_id="p", at=AT, version=2,
            initiatives=[spec.model_copy(update={"id": "b", "name": "B", "brief": "do it differently"})],
        ),
    ])

    assert [item.spec.id for item in plan.retired] == ["a"]
    assert plan.ready() == ["b"]
    # Retired work is out of the plan but not out of the bill: its packets stay
    # in the overhead and its usage stays in the productive denominator.
    ledger = token_ledger(plan)
    assert any(
        entry.initiative_id == "a" and entry.attempt_id == "att"
        for entry in ledger.entries
    )
    assert ledger.productive_tokens == 50
    assert ledger.orchestration_tokens == receipt.total_tokens
    assert plan.accounted_token_burn() == 50
    assert burn_down(plan, ledger).accounted_tokens == 50
