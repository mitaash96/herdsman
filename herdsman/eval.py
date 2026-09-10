"""Public, model-free evaluation harness for execution variants."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, final
from collections.abc import Awaitable, Callable, Sequence
import inspect
from uuid import uuid4

from .checkpoint import Completion, GitCheckpointCollector
from .classes import (
    Assignment,
    Checkpoint,
    InitiativeSpec,
    PlanApproved,
    PlanCreated,
    PlanProposed,
    Routes,
    RuntimeObserved,
)
from .daemon import Daemon
from .herdr import HerdrAdapter, RuntimeInventory
from .observability import token_ledger
from .runtime import CHECKPOINT_MARKER, CHECKPOINT_PATTERN
from .store import EventStore


Variant = Literal["single-agent", "dag", "assignment"]


@dataclass(frozen=True)
class EvalMetrics:
    variant: Variant
    pass_rate: float
    wall_clock_seconds: float
    productive_tokens: int
    orchestration_tokens: int
    usage_provenance: tuple[str, ...] = ()
    derivation: str = "runner-reported authoritative usage and elapsed wall clock"
    receipt: Literal["measured", "test-double"] = "test-double"

    @property
    def total_tokens(self) -> int:
        return self.productive_tokens + self.orchestration_tokens

    @property
    def overhead_ratio(self) -> float | None:
        return (
            self.orchestration_tokens / self.productive_tokens
            if self.productive_tokens
            else None
        )


@dataclass(frozen=True)
class EvalResult:
    brief: str
    cases: tuple[EvalMetrics, ...]


@final
class RealVariantRunner:
    """Execute variants through configured Herdsman/herdr resources.

    The returned usage is collected from the executor checkpoint marker and
    packet ledger, not estimated by this harness. The project must provide the
    ordinary `.herdsman/luna.json` and herdr configuration and a running herdr
    daemon; no model or provider defaults are invented here.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        specs: Callable[[Variant, str], Sequence[InitiativeSpec]],
        timeout: float = 600.0,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.specs = specs
        self.timeout = timeout

    def __call__(self, variant: Variant, brief: str) -> EvalMetrics:
        return asyncio.run(self.run(variant, brief))

    async def run(self, variant: Variant, brief: str) -> EvalMetrics:
        root = self.project_root
        store = EventStore(root / ".herdsman" / "events.db")
        plan_id = f"eval-{variant}-{uuid4().hex}"
        at = datetime.now(UTC)
        daemon = Daemon(store, project_root=root)
        try:
            specs = list(self.specs(variant, brief))
            if not specs:
                raise ValueError(f"{variant} evaluation requires at least one initiative")
            for event in (
                PlanCreated(plan_id=plan_id, at=at, brief=brief),
                PlanProposed(plan_id=plan_id, at=at, version=1, initiatives=specs),
                PlanApproved(plan_id=plan_id, at=at, version=1),
            ):
                _ = daemon.append(event)
            started = time.perf_counter()
            plan = await daemon.run_plan(
                plan_id,
                runtime_factory=lambda: HerdrAdapter(project_root=root),
                collector=GitCheckpointCollector(
                    checks=("uv run pytest -q",), project_root=root
                ),
                timeout=self.timeout,
            )
            wall = time.perf_counter() - started
            ledger = token_ledger(plan)
            settled = sum(
                item.state == "settled" for item in plan.initiatives.values()
            )
            return EvalMetrics(
                variant=variant,
                pass_rate=settled / len(plan.initiatives),
                wall_clock_seconds=wall,
                productive_tokens=ledger.productive_tokens,
                orchestration_tokens=ledger.orchestration_tokens,
                usage_provenance=tuple(ledger.provenance),
                receipt="measured",
            )
        finally:
            store.close()


@final
class _MeasuredClock:
    """Logical wall clock measured at the fake runtime boundary.

    A batch of concurrently live fake attempts costs one deterministic unit;
    sequential batches add units. This avoids asserting against host scheduler
    timing while still measuring the runtime's observed work, rather than
    returning fixture metrics.
    """

    def __init__(self) -> None:
        self.active = 0
        self.units = 0

    def start(self) -> None:
        self.active += 1

    def finish(self) -> None:
        self.active -= 1
        if self.active == 0:
            self.units += 1


@final
class _MeasuredRuntime:
    def __init__(self, clock: _MeasuredClock) -> None:
        self.clock = clock
        self.initiative_id = ""
        self.command = ""

    async def create_worktree(self, branch: str) -> str:
        self.initiative_id = branch.split("/")[2]
        self.clock.start()
        return f"eval-worktree-{self.initiative_id}"

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        del worktree_ref
        if match != CHECKPOINT_PATTERN:
            raise RuntimeError("evaluation runtime requires checkpoint matching")
        self.command = command
        return f"eval-pane-{self.initiative_id}"

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ):
        del pane_ref, match
        # Usage is emitted by the fake harness from the command it received;
        # the daemon then records it through the ordinary checkpoint path.
        input_tokens = max(len(self.command), 1)
        output_tokens = max(len(self.command) // 40, 1)
        marker = f"{CHECKPOINT_MARKER} " + json.dumps(
            {
                "exit_code": 0,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "source": "harness",
                },
            },
            separators=(",", ":"),
        )
        yield RuntimeObserved(
            plan_id=plan_id,
            at=datetime.now(UTC),
            attempt_id=attempt_id,
            kind="pane_output_matched",
            detail={"read": {"text": marker}},
        )
        self.clock.finish()

    async def remove_worktree(self, worktree_ref: str) -> None:
        del worktree_ref

    async def aclose(self) -> None:
        return None

    async def worktree_path(self, worktree_ref: str) -> Path:
        del worktree_ref
        return Path(".")

    async def inventory(self) -> RuntimeInventory:
        return RuntimeInventory((), ())


@final
class _MeasuredCollector:
    def capture_base(
        self,
        path: Path,
        *,
        inputs: Sequence[Path] = (),
        timeout: float | None = None,
    ) -> str:
        del path, inputs, timeout
        return "eval-base"

    def diagnose(
        self,
        path: Path,
        attempt_id: str,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> str | None:
        del path, attempt_id, base_sha, timeout
        return None

    def collect(
        self,
        path: Path,
        attempt_id: str,
        completion: Completion,
        *,
        base_sha: str,
        timeout: float | None = None,
    ) -> Checkpoint:
        del path, timeout
        return Checkpoint(
            id=f"eval-checkpoint-{attempt_id}",
            attempt_id=attempt_id,
            changed_paths=["src/eval.py"],
            base_sha=base_sha,
            head_sha="eval-head",
            exit_code=completion.exit_code,
            usage=completion.usage,
            patch_path=f".herdsman/artifacts/{attempt_id}.patch",
        )


def _fixture_specs(variant: Variant, brief: str) -> list[InitiativeSpec]:
    assignment = Assignment(harness="luna", model="eval")
    if variant == "single-agent":
        return [InitiativeSpec(id="single", name="single", brief=brief, assignment=assignment)]
    if variant == "dag":
        return [
            InitiativeSpec(id="dag-a", name="dag-a", brief=brief, assignment=assignment, routes=Routes(writes=["src/a.py"])),
            InitiativeSpec(id="dag-b", name="dag-b", brief=brief, assignment=assignment, routes=Routes(writes=["src/b.py"])),
        ]
    return [
        InitiativeSpec(id="assigned-a", name="assigned-a", brief=brief, assignment=assignment, routes=Routes(writes=["src/a.py"])),
        InitiativeSpec(id="assigned-b", name="assigned-b", brief=brief, assignment=Assignment(harness="luna", model="eval-alt"), routes=Routes(writes=["src/b.py"])),
    ]


def deterministic_fixture_runner(variant: Variant, brief: str) -> EvalMetrics:
    """Deterministic test double using the Daemon fake-runtime/collector seams.

    This fixture is not a paid/model receipt. Real receipts must provide a
    runner that executes the configured variant and returns authoritative usage.
    """
    with TemporaryDirectory(prefix="herdsman-eval-") as raw_root:
        root = Path(raw_root)
        config = root / ".herdsman"
        config.mkdir()
        _ = (config / "luna.json").write_text(
            json.dumps({"binary": "eval-luna"}), encoding="utf-8"
        )
        store = EventStore(config / "events.db")
        try:
            plan_id = f"eval-{variant}-{uuid4().hex}"
            specs = _fixture_specs(variant, brief)
            at = datetime.now(UTC)
            daemon = Daemon(store, project_root=root)
            for event in (
                PlanCreated(plan_id=plan_id, at=at, brief=brief),
                PlanProposed(plan_id=plan_id, at=at, version=1, initiatives=specs),
                PlanApproved(plan_id=plan_id, at=at, version=1),
            ):
                _ = daemon.append(event)
            clock = _MeasuredClock()
            started = time.perf_counter()
            plan = asyncio.run(
                daemon.run_plan(
                    plan_id,
                    runtime_factory=lambda: _MeasuredRuntime(clock),
                    collector=_MeasuredCollector(),
                    checks=(),
                )
            )
            # ``wall_clock_seconds`` is a measured logical runtime duration;
            # host elapsed is retained only to prove the run actually executed.
            host_elapsed = time.perf_counter() - started
            if host_elapsed <= 0:
                raise RuntimeError("evaluation runtime did not execute")
            ledger = token_ledger(plan)
            settled = sum(item.state == "settled" for item in plan.initiatives.values())
            return EvalMetrics(
                variant=variant,
                pass_rate=settled / len(plan.initiatives),
                wall_clock_seconds=float(clock.units),
                productive_tokens=ledger.productive_tokens,
                orchestration_tokens=ledger.orchestration_tokens,
                usage_provenance=tuple(ledger.provenance),
                receipt="test-double",
            )
        finally:
            store.close()


VariantRunner = Callable[
    [Variant, str], EvalMetrics | Awaitable[EvalMetrics]
]


def evaluate_variants(
    brief: str,
    *,
    runner: VariantRunner | None = None,
) -> EvalResult:
    """Execute and compare one real runner for all three variant shapes.

    The runner owns the actual harness/model invocation and must return
    authoritative productive usage plus orchestration usage and wall clock.
    Async runners are accepted. Use ``deterministic_fixture_runner`` only as a
    labelled core regression test double.
    """
    if not brief.strip():
        raise ValueError("evaluation brief cannot be empty")
    if runner is None:
        raise ValueError(
            "a variant runner is required; use RealVariantRunner with explicit variant specs"
        )
    variants: tuple[Variant, ...] = ("single-agent", "dag", "assignment")
    measured: list[EvalMetrics] = []
    for variant in variants:
        result = runner(variant, brief)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        measured.append(result)
    cases = tuple(measured)
    for case in cases:
        if case.variant not in variants:
            raise ValueError(f"unknown evaluation variant {case.variant!r}")
        if case.pass_rate < 0 or case.pass_rate > 1:
            raise ValueError("pass rate must be between zero and one")
        if case.wall_clock_seconds < 0:
            raise ValueError("wall clock must not be negative")
        if case.productive_tokens < 0 or case.orchestration_tokens < 0:
            raise ValueError("token counts must not be negative")
        if not case.usage_provenance:
            raise ValueError("runner must provide usage provenance")
        if case.overhead_ratio is not None and case.overhead_ratio > 0.20:
            raise ValueError(
                f"{case.variant} orchestration overhead exceeds the 20% target"
            )
    return EvalResult(brief=brief, cases=cases)


__all__ = [
    "EvalMetrics",
    "EvalResult",
    "RealVariantRunner",
    "Variant",
    "VariantRunner",
    "deterministic_fixture_runner",
    "evaluate_variants",
]
