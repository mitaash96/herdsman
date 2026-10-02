"""Boundaries for planner output, executor packets, and completion evidence."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import ValidationError

from .checkpoint import Completion
from .classes import (
    AgentSession,
    ArtifactRef,
    AssetSnapshot,
    Assignment,
    EXECUTOR_HARNESS,
    Initiative,
    InitiativePolicy,
    InitiativeSpec,
    MemoryLeaf,
    PacketSection,
    PacketSnapshot,
    Plan,
    PlanProposed,
    Routes,
    RuntimeObserved,
    TokenCategory,
    TokenSource,
    Usage,
)
from .effort import effective_effort, effort_argv
from .kitchen import (
    KITCHEN_DIR,
    KITCHEN_FILE,
    CapabilityState,
    Kitchen,
    KitchenConfigError,
)
from .herdr import AgentLaunch, HerdrError, JsonObject
from .store import atomic_write_bytes
from .memory import MemoryDelivery, deliver_memory, leaf_version

PaneRunner = Callable[[AgentLaunch, float], Awaitable[JsonObject]]
"""Runs an interactive planner; returns final herdr AgentInfo.

HerdrError permits headless fallback only before a pane is exposed. A runner
must turn errors after exposure into RuntimeError to prevent duplicate work.
"""


_DEFAULT_ASSIGNMENT = Assignment(harness=EXECUTOR_HARNESS, model="cheap-1")
"""The pre-Kitchen fill, kept for projects that declare no executor default."""
_MODEL_TIER_NAME = "models.json"
_PROMPT_PLACEHOLDER = "{prompt}"
"""The one packet placeholder a harness argv template must hold, exactly once."""


class LunaConfigError(RuntimeError):
    """The project-local harness/Kitchen configuration is absent or invalid.

    The pre-Kitchen name is kept as the runtime's public configuration-error
    type: daemon, CLI, and tests catch it wherever a launch cannot be compiled.
    """


class PlannerError(RuntimeError):
    """The supervised planner did not return one valid initiative."""


class CompletionError(RuntimeError):
    """The executor did not reach a valid completion boundary."""


@dataclass(frozen=True)
class HarnessSpec:
    """One harness's bounded/interactive launch args and usage capability."""

    argv: tuple[str, ...]
    """Ends with the prompt placeholder; the prompt replaces it at compile."""
    model_argv: tuple[str, ...] = ()
    """Inserted before the prompt with the model appended, only when one is set."""
    agent_args: tuple[str, ...] = ()
    """Interactive launch args, separate from the bounded argv template."""
    usage: CapabilityState = "unknown"
    """Declared usage support, not a prerequisite for interactive settlement."""


@dataclass(frozen=True)
class FailureDelta:
    """One prior attempt's failure evidence, bounded at compile time.

    `check` is the failed check's name when the failure was a check result;
    `error` is the normalized failure reason.  These are the only fields a
    retry packet carries about a failed attempt -- never its transcript.
    """

    attempt_id: str
    error: str
    check: str | None = None


_MAX_FAILURE_DELTAS = 5
_MAX_FAILURE_CHARS = 400
_MAX_CONTEXT_BRIEF = 600


@dataclass(frozen=True)
class TaskPacket:
    """The only executor input compiled from a planner initiative."""

    initiative_id: str
    name: str
    brief: str
    assignment: Assignment
    routes: Routes
    subtasks: tuple[str, ...]
    inputs: tuple[ArtifactRef, ...] = ()
    """Upstream checkpoints by reference. Never the DAG, never a prose handoff."""
    memory: tuple[str, ...] = ()
    """Backward-compatible intervention lines."""
    memory_pointers: tuple[str, ...] = ()
    memory_inline: tuple[str, ...] = ()
    memory_leaf_ids: tuple[str, ...] = ()
    memory_leaf_versions: tuple[str, ...] = ()
    memory_mode: str = "legacy"
    memory_pull_command: str | None = None
    failures: tuple[str, ...] = ()
    """Bounded failure deltas from this initiative's prior attempts, one line
    each. Never the failed attempt's transcript."""
    assets: tuple[AssetSnapshot, ...] = ()
    """Library assets this initiative declared, exactly as its plan version
    froze them. Only the initiative's own closure travels -- never the shelf,
    never a sibling's assets -- and an initiative that declared none carries no
    section at all, so a Library the plan does not use costs nothing."""

    handoff_path: str | None = None

    def sections(self) -> tuple[tuple[str, object], ...]:
        """Return the exact ordered packet sections used for inspection."""
        return (
            ("initiative_id", self.initiative_id),
            ("name", self.name),
            ("brief", self.brief),
            ("assignment", self.assignment.model_dump(mode="json")),
            ("routes", self.routes.model_dump(mode="json")),
            ("handoff_path", self.handoff_path),
            ("subtasks", list(self.subtasks)),
            ("inputs", [ref.model_dump(mode="json") for ref in self.inputs]),
            ("memory", list(self.memory)),
            ("memory_pointers", list(self.memory_pointers)),
            ("memory_inline", list(self.memory_inline)),
            ("memory_leaf_ids", list(self.memory_leaf_ids)),
            ("memory_leaf_versions", list(self.memory_leaf_versions)),
            ("memory_mode", self.memory_mode),
            ("memory_pull_command", self.memory_pull_command),
            ("failures", list(self.failures)),
            *(
                ()
                if not self.assets
                else ((
                    "assets",
                    [
                        {
                            "ref": asset.ref,
                            "digest": asset.digest,
                            "title": asset.title,
                            "body": asset.body,
                        }
                        for asset in self.assets
                    ],
                ),)
            ),
        )

    def json(self) -> str:
        return json.dumps(
            dict(self.sections()), separators=(",", ":"), sort_keys=True
        )

    def snapshot(
        self,
        *,
        source: str = "estimate",
        provenance: str = "local estimate",
        counter: Callable[[str], int] | None = None,
    ) -> PacketSnapshot:
        """Measure canonical packet fragments and reconcile to ``packet.json``.

        ``counter`` is the explicit tokenizer seam. The fallback remains a
        labelled estimate; it is not treated as a provider hard count.
        """
        ordered = sorted(self.sections(), key=lambda item: item[0])
        items = [
            json.dumps(name, separators=(",", ":"), sort_keys=True)
            + ":"
            + json.dumps(value, separators=(",", ":"), sort_keys=True)
            for name, value in ordered
        ]
        full = "{" + ",".join(items) + "}"
        count = counter or estimate_tokens
        full_tokens = max(count(full), 0)
        costs: list[int] = []
        prefix = ""
        previous_tokens = 0
        for index, item in enumerate(items):
            prefix += ("{" if index == 0 else ",") + item
            if index == len(items) - 1:
                prefix += "}"
            current_tokens = max(count(prefix), 0)
            if current_tokens < previous_tokens:
                raise ValueError("packet counter must be monotonic over canonical prefixes")
            costs.append(current_tokens - previous_tokens)
            previous_tokens = current_tokens
        if previous_tokens != full_tokens:
            raise ValueError("packet counter returned inconsistent results")
        section_source = (
            "tokenizer" if counter is not None and source == "estimate" else source
        )
        sections = [
            PacketSection(
                name=name,
                value=value,
                input_tokens=cost,
                source=cast(TokenSource, section_source),
                phase="preflight",
                provenance=provenance,
            )
            for (name, value), cost in zip(ordered, costs, strict=True)
        ]
        return PacketSnapshot(
            sections=sections,
            total_tokens=full_tokens,
            provenance=provenance,
        )


def packet_snapshot(
    packet: TaskPacket,
    *,
    counter: Callable[[str], int] | None = None,
) -> PacketSnapshot:
    """Compatibility function for callers that prefer a functional seam."""
    return packet.snapshot(counter=counter)


def preflight_packet(
    packet: TaskPacket,
    counter: Callable[[str], int] | None = None,
    *,
    provenance: str = "local estimate",
) -> PacketSnapshot:
    """Count packet sections before launch using one optional tokenizer seam."""
    return packet.snapshot(
        counter=counter,
        provenance=provenance,
    )


def packet_diff(previous: PacketSnapshot, current: PacketSnapshot):
    """Compare packets by section value and measured cost."""
    from .classes import PacketDiff

    before = {section.name: section for section in previous.sections}
    after = {section.name: section for section in current.sections}
    changed = sorted(
        name for name in set(before) & set(after)
        if before[name].model_dump(mode="json") != after[name].model_dump(mode="json")
    )
    provenance = sorted(
        {previous.provenance, current.provenance}
        | {section.provenance for section in previous.sections}
        | {section.provenance for section in current.sections}
    )
    return PacketDiff(
        changed_sections=changed,
        added_sections=sorted(set(after) - set(before)),
        removed_sections=sorted(set(before) - set(after)),
        token_delta=current.total_tokens - previous.total_tokens,
        before_tokens=previous.total_tokens,
        after_tokens=current.total_tokens,
        provenance=provenance,
        derivation="canonical launched packet total delta; section values compared by name",
    )


def remaining_work_brief(initiative: Initiative) -> str:
    """The active instruction for a node that already has completed claims.

    A retry must not re-issue work recorded done or skipped, and the
    planner's or operator's original brief may name it. So the execution
    brief is rebuilt from the unfinished claims alone; the original brief
    stays in the plan's history and never becomes the launched command.
    Routes, contracts, memory, and failure evidence still ride the packet —
    only the instruction changes.
    """
    remaining = initiative.remaining_claims
    lines = [
        f"Continue initiative {initiative.spec.id} ({initiative.spec.name}).",
        "Execute only the unfinished claims listed below. Do not redo work "
        + "already recorded done or skipped, and leave completed work and its "
        + "evidence unchanged.",
    ]
    if remaining:
        lines.append("Unfinished claims:")
        lines.extend(f"- {claim}" for claim in remaining)
    else:
        lines.append(
            "No unfinished claims remain: do not re-execute any completed work."
        )
    return "\n".join(lines)


def compile_task_packet(
    spec: InitiativeSpec,
    inputs: Sequence[ArtifactRef] = (),
    *,
    brief: str | None = None,
    assignment: Assignment | None = None,
    leaves: Sequence[MemoryLeaf] = (),
    failures: Sequence[FailureDelta] = (),
    memory_delivery: MemoryDelivery | None = None,
    capability: str | None = None,
    memory_pull_command: str | None = None,
    subtasks: Sequence[str] | None = None,
    assets: Sequence[AssetSnapshot] = (),
    handoff_path: str | None = None,
) -> TaskPacket:
    """Copy only this initiative's contract and its inputs across the boundary.

    An executor sees its own node and the evidence its dependencies produced —
    never sibling briefs, never the plan. A retry compiles the task's current
    brief version and assignment — the attempt snapshots them — every
    run-scoped memory leaf as one deterministic line, and at most the last few
    failure deltas as bounded one-line evidence; the failed attempt's
    transcript never crosses the boundary. ``subtasks`` overrides the declared
    claims sent as the instruction: a partially completed node compiles only
    its unfinished claims, while the immutable spec keeps the recorded ones.
    """
    delivery = memory_delivery
    if delivery is None and capability is not None:
        delivery = deliver_memory(leaves, capability)
    if delivery is None and leaves and any(leaf.lifetime == "project" for leaf in leaves):
        delivery = deliver_memory(leaves, "A")
    legacy = tuple(_memory_line(leaf) for leaf in leaves) if delivery is None else ()
    carried_ids = tuple(leaf.id for leaf in leaves) if delivery is None else delivery.leaf_ids
    carried_versions = tuple(leaf_version(leaf) for leaf in leaves) if delivery is None else delivery.versions
    return TaskPacket(
        initiative_id=spec.id,
        name=spec.name,
        brief=spec.brief if brief is None else brief,
        assignment=spec.assignment if assignment is None else assignment,
        routes=spec.routes,
        subtasks=tuple(spec.subtasks if subtasks is None else subtasks),
        inputs=tuple(inputs),
        memory=legacy,
        memory_pointers=() if delivery is None else delivery.pointers,
        memory_inline=() if delivery is None else delivery.inline,
        memory_leaf_ids=carried_ids,
        memory_leaf_versions=carried_versions,
        memory_mode="legacy" if delivery is None else delivery.mode,
        memory_pull_command=memory_pull_command,
        # Oldest first in, most recent kept: a retry needs the freshest
        # failures, and the bound keeps the packet lean.
        failures=tuple(
            _failure_line(delta) for delta in list(failures)[-_MAX_FAILURE_DELTAS:]
        ),
        assets=tuple(assets),
        handoff_path=handoff_path,
    )


def _memory_line(leaf: MemoryLeaf) -> str:
    """One deterministic packet line per run-scoped ground-truth leaf."""
    return f"[{leaf.origin}] {leaf.subject}: {leaf.claim}"


def _failure_line(delta: FailureDelta) -> str:
    """One bounded, deterministic packet line per prior failure."""
    check = _one_line(delta.check) if delta.check else "unknown-check"
    return f"[{delta.attempt_id}] {check}: {_one_line(delta.error)}"


def _one_line(text: str) -> str:
    """Collapse whitespace so evidence stays one line inside the byte bound."""
    return " ".join(text.split())[:_MAX_FAILURE_CHARS]


def estimate_tokens(text: str) -> int:
    """Crude character-based estimate, labelled as such.

    Sprint 4 owns real measurement with provenance; counting bytes here is
    enough to compute the overhead ratio without pretending it is exact.
    """
    return len(text) // 4


def _kitchen(project_root: str | os.PathLike[str] = ".") -> Kitchen:
    """Load the project Kitchen, reporting configuration faults as launch errors."""
    try:
        return Kitchen.load(project_root)
    except KitchenConfigError as exc:
        raise LunaConfigError(str(exc)) from exc


def resolve_luna_binary(project_root: str | os.PathLike[str] = ".") -> str:
    """The executor harness's configured executable, read via the project Kitchen."""
    return resolve_harness(EXECUTOR_HARNESS, project_root=project_root).argv[0]


def resolve_model_tiers(
    project_root: str | os.PathLike[str] = ".",
) -> dict[str, str]:
    """Read the Kitchen project-local tier map.

    `{"cheap-1": "cheap", "opus-5": "frontier"}` from the canonical
    document; a project without one still reads the legacy `models.json` with
    its historic error messages. Absent means no opinion, and no opinion means
    no warning — Herdsman does not ship a model catalog, and guessing a tier
    from a model name would be a warning nobody can trust.
    """
    if _mapping_path(project_root, KITCHEN_FILE).exists():
        return dict(_kitchen(project_root).tiers)
    return _legacy_model_tiers(project_root)


def _executor_default(project_root: str | os.PathLike[str] = ".") -> Assignment:
    """The configured initiative executor assignment, or the pre-Kitchen fill."""
    return _kitchen(project_root).defaults.initiative or _DEFAULT_ASSIGNMENT


def _legacy_model_tiers(
    project_root: str | os.PathLike[str],
) -> dict[str, str]:
    """Pre-Kitchen read of `models.json`, preserving its error messages."""
    mapping_path = _mapping_path(project_root, _MODEL_TIER_NAME)
    try:
        raw = cast(object, json.loads(mapping_path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise LunaConfigError(f"cannot read model tiers {mapping_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LunaConfigError(f"invalid JSON in model tiers {mapping_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LunaConfigError(f"model tiers {mapping_path} must be an object")
    tiers: dict[str, str] = {}
    for model, tier in cast(dict[str, object], raw).items():
        if not isinstance(tier, str) or not tier.strip():
            raise LunaConfigError(
                f"model tiers {mapping_path} value for {model!r} must be a string"
            )
        tiers[model] = tier
    return tiers


def resolve_harness(
    harness: str, *, project_root: str | os.PathLike[str] = "."
) -> HarnessSpec:
    """Resolve one harness's launch template, selected solely by the name.

    The canonical `.herdsman/kitchen.json` adapters and Kitchen's legacy
    `luna.json`/`harnesses.json` read compatibility both supply the declared
    argv plus optional model_argv, compiled exactly, and the declared usage
    capability — no discovery, health, defaults, environment, or model-name
    fallback: those stay Sprint 8's other lanes. An undeclared harness fails here,
    at command compilation, instead of launching something that cannot run.
    """
    adapter = _kitchen(project_root).adapter(harness)
    if adapter is None:
        raise LunaConfigError(
            f"harness {harness!r} is not configured in {KITCHEN_DIR}/{KITCHEN_FILE} "
            + "(or its legacy luna.json/harnesses.json); a task can launch only a "
            + "declared adapter"
        )
    return HarnessSpec(
        argv=tuple(adapter.argv),
        model_argv=tuple(adapter.model_argv),
        agent_args=tuple(adapter.agent_args),
        usage=adapter.capabilities.usage,
    )


def _mapping_path(project_root: str | os.PathLike[str], name: str) -> Path:
    return Path(project_root).expanduser().resolve() / ".herdsman" / name


def _compile_argv(
    spec: HarnessSpec, prompt: str, model: str, effort: str | None = None
) -> list[str]:
    """Insert the model and effort argv, then the prompt, into one template."""
    args = list(spec.argv)
    index = args.index(_PROMPT_PLACEHOLDER)
    if model:
        model_args = [*spec.model_argv, model]
        args[index:index] = model_args
        index += len(model_args)
    effort_args = effort_argv(spec.argv[0], effort)
    args[index:index] = effort_args
    index += len(effort_args)
    args[index] = prompt
    return args


def agent_name(attempt_id: str) -> str:
    """A herdr name unique per daemon-minted attempt."""
    return "hs-" + attempt_id.removeprefix("attempt_")[:12]


def write_packet(
    project_root: str | os.PathLike[str], attempt_id: str, packet: TaskPacket
) -> Path:
    """Persist the packet at the daemon-assigned path, never in terminal input."""
    if not attempt_id or Path(attempt_id).name != attempt_id or attempt_id in {".", ".."}:
        raise ValueError("attempt id must be a non-empty filename component")
    path = Path(project_root).expanduser().resolve() / ".herdsman" / "packets" / f"{attempt_id}.json"
    atomic_write_bytes(path, packet.json().encode())
    return path


def executor_launch(
    packet: TaskPacket,
    attempt_id: str,
    packet_path: Path,
    *,
    project_root: str | os.PathLike[str] = ".",
) -> AgentLaunch:
    """Compile interactive args and a short file-pointer prompt; no output protocol."""
    spec = resolve_harness(packet.assignment.harness, project_root=project_root)
    args = [*spec.agent_args]
    if packet.assignment.model:
        args += [*spec.model_argv, packet.assignment.model]
    args += effort_argv(
        spec.argv[0], effective_effort(_kitchen(project_root), packet.assignment)
    )
    return AgentLaunch(
        name=agent_name(attempt_id),
        kind=Path(spec.argv[0]).name,
        args=tuple(args),
        prompt=(
            f"Implement the Herdsman task packet at {packet_path} in this worktree. "
            "Do not modify global harness configuration. Run the requested checks, "
            "then stop."
        ),
    )


def completion_from_event(event: RuntimeObserved) -> Completion | None:
    """Only herdr's settled lifecycle fact completes an interactive attempt."""
    return Completion() if event.kind == "agent_settled" else None


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    stdout, stderr = await process.communicate()
    return stdout or b"", stderr or b""


SMOKE_MARKER = "HERDSMAN_SMOKE_OK"
"""The one line a passing smoke probe must print on stdout."""

SMOKE_PROMPT = (
    "Herdsman adapter smoke test: return exactly the token "
    + SMOKE_MARKER
    + " and nothing else. Use no tools and modify no files."
)
"""The fixed server-side probe prompt. A client never supplies prompt text."""


@dataclass(frozen=True)
class SmokeProcess:
    """One captured adapter smoke subprocess, before any outcome mapping.

    Facts only: how it ended, what it printed, and why there is no output.
    Outcome states (passed/failed/refused/timed_out) are the daemon's mapping
    over these; `marker` is the pass signal -- the fixed smoke token on a line
    of its own in captured stdout, never a substring of some longer line, never
    in stderr and never inferred from the exit alone.
    """

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""
    """Spawn-failure message; never returned to a client as detail."""

    @property
    def marker(self) -> bool:
        return any(line.strip() == SMOKE_MARKER for line in self.stdout.splitlines())


SmokeRunner = Callable[[str, str, Path, float], Awaitable[SmokeProcess]]
"""The injectable smoke seam: (harness, model, project_root, timeout)."""


async def adapter_smoke(
    harness: str,
    model: str,
    project_root: str | os.PathLike[str] = ".",
    timeout: float = 30.0,
) -> SmokeProcess:
    """Run one bounded, pane-less smoke probe through a declared adapter.

    The prompt is fixed server-side and compiled from the adapter's own launch
    template exactly like any other bounded call. The child is killed AND
    awaited on both deadline and cancellation, so a cancelled request leaves no
    process behind (the planner precedent cleans up on timeout only). Returns
    process facts; mapping them to route states is the daemon's job.
    """
    spec = resolve_harness(harness, project_root=project_root)
    argv = _compile_argv(spec, SMOKE_PROMPT, model)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return SmokeProcess(error=str(exc))
    try:
        stdout, stderr = await asyncio.wait_for(_communicate(process), timeout)
    except asyncio.TimeoutError:
        process.kill()
        _ = await process.wait()
        return SmokeProcess(returncode=process.returncode, timed_out=True)
    except asyncio.CancelledError:
        process.kill()
        _ = await process.wait()
        raise
    return SmokeProcess(
        returncode=process.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


class PiMemoryAuthor:
    """One bounded, non-interactive Pi call for evidence-only memory salvage."""

    binary: str
    model: str
    timeout: float

    def __init__(self, *, binary: str = "pi", model: str = "default", timeout: float = 120.0) -> None:
        self.binary = binary
        self.model = model
        self.timeout = timeout

    async def salvage(self, report: str) -> object:
        prompt = (
            "Return JSON only as {\"leaves\":[...]} for project-local memory. "
            + "Each leaf object must contain exactly id, subject, one-line claim, "
            + "evidence refs copied only from the supplied report, scope, and optional body. "
            + "Do not emit at, by, origin, lifetime, status, version, ttl, or owner fields; "
            + "the daemon stamps those metadata fields.\nEVIDENCE_REPORT=\n" + report
        )
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary, "--no-session", "--mode", "json", "--print",
                "--model", self.model, prompt,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(_communicate(process), self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                _ = await process.wait()
                raise
        except (OSError, asyncio.TimeoutError) as exc:
            raise PlannerError(f"memory author invocation failed: {exc}") from exc
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise PlannerError(f"memory author exited {process.returncode}: {error}")
        return _json_result(stdout.decode("utf-8", errors="replace"))


class PiFrontierPlanner:
    """One bounded planner call, interactive when a pane runner is available.

    When a planner harness is configured — explicitly or as the Kitchen's
    `defaults.planner` — the launch is that adapter's declared argv compiled
    through `resolve_harness`; otherwise the historical Pi invocation remains
    the compatibility path. The prompt names the configured initiative
    executor assignment, never a fixed harness name.
    """

    binary: str
    model: str
    timeout: float
    harness: str | None
    executor_assignment: Assignment
    project_root: str
    pane: PaneRunner | None
    effort: str | None
    _planner_model: str
    output_path: Path | None
    last_session: AgentSession | None

    def __init__(
        self,
        *,
        binary: str = "pi",
        model: str = "default",
        timeout: float = 120.0,
        harness: str | None = None,
        effort: str | None = None,
        project_root: str | os.PathLike[str] = ".",
        pane: PaneRunner | None = None,
        output_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.output_path = Path(output_path).expanduser().resolve() if output_path is not None else None
        self.last_session = None
        self.pane = pane
        self.binary = binary
        self.model = model
        self.timeout = timeout
        self.project_root = os.fspath(project_root)
        self.effort = effort
        kitchen = _kitchen(project_root)
        planner_assignment = kitchen.defaults.planner
        self.harness = harness or (
            planner_assignment.harness if planner_assignment is not None else None
        )
        # An explicitly passed model wins; the default sentinel defers to the
        # Kitchen planner assignment's own model.
        self._planner_model = (
            planner_assignment.model if planner_assignment is not None else ""
        )
        self.executor_assignment = kitchen.defaults.initiative or _DEFAULT_ASSIGNMENT

    async def propose(self, brief: str) -> object:
        prompt = (
            (
                "You are Herdsman's supervised frontier planner. Return JSON only, "
                "with an initiatives array. Each initiative must have id, name, brief, "
                "assignment {harness, model}, routes {reads, writes}, subtasks, and "
                "depends_on listing the ids it consumes. Decompose into independent "
                "initiatives wherever the work allows; dependencies must be acyclic. "
                "Declare write routes precisely — two initiatives that write the same "
                "path cannot run concurrently. When Dispatch provides selected roles, "
                "contracts and role assignments in the brief, choose a selected role "
                "for each initiative and include its name as role; include the relevant "
                "selected Library refs under assets and use that role's assignment. "
                "Default every initiative to implementer. Add scout/architect only for a "
                "real unknown/open design choice; reviewer only when acceptance criteria "
                "or policy require review; test-author only when the brief names behaviour "
                "to prove. Never declare write routes for scout/architect/reviewer: the "
                "daemon assigns them. "
                "Treat acceptance criteria as requirements, not another initiative. Use harness "
            )
            + self.executor_assignment.harness
            + ".\nBRIEF="
        ) + brief
        return await self._invoke(prompt)

    async def recalibrate(self, context: str) -> object:
        """One bounded revision call carrying only the compaction context."""
        return await self._invoke(
            recalibration_prompt(
                context, executor_harness=self.executor_assignment.harness
            )
        )

    async def _invoke(self, prompt: str) -> object:
        self.last_session = None
        if self.pane is not None:
            path = self.output_path
            if path is None:
                raise PlannerError("interactive planner needs a daemon-assigned output path")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.unlink(missing_ok=True)  # A retry must not accept an earlier proposal.
            except OSError as exc:
                raise PlannerError(f"cannot prepare planner output {path}: {exc}") from exc
            model = self.model if self.model != "default" else self._planner_model
            if self.harness is not None:
                spec = resolve_harness(self.harness, project_root=self.project_root)
                kind = Path(spec.argv[0]).name
                args = [*spec.agent_args]
                if model:
                    args += [*spec.model_argv, model]
                args += effort_argv(spec.argv[0], self.effort)
            else:
                kind = Path(self.binary).name
                args = ["--model", self.model, *effort_argv(self.binary, self.effort)]
            launch = AgentLaunch(
                name=f"hs-planner-{uuid4().hex[:12]}",
                kind=kind,
                args=tuple(args),
                prompt=(
                    f"Write the complete proposal JSON to {json.dumps(str(path))}, then stop. "
                    + "Do not return the proposal in chat.\n"
                    + prompt.replace("Return JSON only, ", "Produce JSON ", 1)
                ),
            )
            try:
                agent = await self.pane(launch, self.timeout)
            except asyncio.TimeoutError as exc:
                raise PlannerError(f"planner invocation failed: {exc!r}") from exc
            except HerdrError:
                pass  # Runner guarantees no pane was exposed: retain headless fallback.
            except RuntimeError as exc:
                raise PlannerError(f"planner invocation failed: {exc}") from exc
            else:
                session = agent.get("agent_session")
                if session is not None:
                    if not isinstance(session, dict):
                        raise PlannerError("invalid planner session: expected an object")
                    try:
                        self.last_session = AgentSession.model_validate(
                            {**cast(dict[str, object], session), "at": datetime.now(UTC)}
                        )
                    except ValidationError as exc:
                        raise PlannerError(f"invalid planner session: {exc}") from exc
                try:
                    return _json_result(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError) as exc:
                    raise PlannerError(f"cannot read planner output {path}: {exc}") from exc
        if self.harness is not None:
            model = self.model if self.model != "default" else self._planner_model
            argv = _compile_argv(
                resolve_harness(self.harness, project_root=self.project_root),
                prompt,
                model,
                self.effort,
            )
        else:
            argv = [
                self.binary,
                "--no-session",
                "--mode",
                "json",
                "--print",
                "--model",
                self.model,
                prompt,
            ]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    _communicate(process), self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                _ = await process.wait()
                raise
        except (OSError, asyncio.TimeoutError) as exc:
            raise PlannerError(f"planner invocation failed: {exc}") from exc
        output = stdout.decode("utf-8", errors="replace")
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise PlannerError(f"planner exited {process.returncode}: {error}")
        return _json_result(output)


def _json_result(output: str) -> object:
    """Accept Pi's one-object output and its line-oriented JSON mode."""
    try:
        return cast(object, json.loads(output))
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        try:
            return cast(object, json.loads(line))
        except json.JSONDecodeError:
            continue
    raise PlannerError("planner output was not JSON")


def recalibration_prompt(
    context: str, *, executor_harness: str = EXECUTOR_HARNESS
) -> str:
    """The revision call's prompt: remaining work only, pinned JSON shape."""
    return (
        "You are Herdsman's supervised frontier planner revising an existing plan. "
        "Return JSON only, with an initiatives array covering only the revised "
        "remaining work: do not re-declare any entry listed under fixed. Use the "
        "identical output shape as the initial proposal — each initiative must have "
        "id, name, brief, assignment {harness, model}, routes {reads, writes}, "
        "subtasks, and depends_on; a dependency may name a fixed id or another "
        "returned id. You may add, remove, split, merge, rename, or edit remaining "
        "work; an id matching an unfinished node revises that node in place, and "
        "every completed claim listed on a node must be preserved verbatim under "
        "that node's original id, never omitted, renamed, or moved to another node: "
        "revise or extract only the unfinished residual. Copy every other field the "
        "context shows on a node you return — token cap, contract, policy, approval "
        "gate, or duration estimate — unless the revision deliberately changes that "
        "constraint: a re-declared node replaces its spec wholesale. When the "
        "context carries a reason, that is why the operator asked for this "
        "revision: honor it. Use harness "
    ) + executor_harness + ".\nCONTEXT=" + context


def recalibration_context(
    plan: Plan,
    *,
    max_brief_chars: int = _MAX_CONTEXT_BRIEF,
    anchored: Collection[str] = (),
    reason: str | None = None,
) -> str:
    """Snapshot the plan's remaining work and bounded failure evidence.

    The revision planner sees only folded, compact facts: fixed anchors are
    identified by digest and never re-declared, while remaining nodes carry
    their immutable completed claims next to the residual being revised. Event
    streams, transcripts, memory claims, packet snapshots, earlier plan
    versions, and fixed specs are excluded by construction.

    ``anchored`` names nodes the caller must not let the model revise even
    though the fold would allow it — a daemon passes the attempts it is still
    settling, so context and fold agree on what is fixed.

    ``reason`` is the operator's own rationale for this revision, bounded to
    one line like every other failure line. It is the operator's instruction,
    not history: no event stream, transcript, or record of prior revisions
    rides along with it.
    """
    # The domain owns the freeze rule; the function-local import keeps this
    # lane runnable before the producer lands, with no second copy of the rule.
    from .classes import frozen_work

    fixed: list[dict[str, object]] = []
    remaining: list[dict[str, object]] = []
    for initiative in plan.initiatives.values():
        spec = initiative.spec
        if frozen_work(initiative) or spec.id in anchored:
            checkpoint = initiative.latest_checkpoint
            fixed.append(
                {
                    "id": spec.id,
                    "name": spec.name,
                    "digest": spec.digest,
                    "state": initiative.state,
                    "attempts": len(initiative.attempts),
                    "checkpoint_id": checkpoint.id if checkpoint is not None else None,
                }
            )
            continue
        entry: dict[str, object] = {
            "id": spec.id,
            "name": spec.name,
            "brief": initiative.current_brief[:max_brief_chars],
            "assignment": initiative.current_assignment.model_dump(mode="json"),
            "routes": spec.routes.model_dump(mode="json"),
            "subtasks": list(spec.subtasks),
            "depends_on": list(spec.depends_on),
            "state": initiative.state,
            "attempts": len(initiative.attempts),
            "failures": _context_failures(plan, initiative),
            "evidence": _context_evidence(initiative),
            "completed_claims": [
                {"id": claim.id, "claim": claim.brief, "state": claim.state}
                for claim in initiative.completed_claims
            ],
            "approved_checkpoint_ids": [
                checkpoint.id for checkpoint in initiative.approved_checkpoints
            ],
        }
        # Only non-default constraints ride along: a re-declared node replaces
        # its spec wholesale, so an in-place edit must not silently strip a
        # cap, contract, policy, approval gate, asset declaration, or estimate the operator set.
        # With these, every `InitiativeSpec` field is either above or here, so
        # the context is the whole contract a revised node must re-declare.
        if spec.token_cap is not None:
            entry["token_cap"] = spec.token_cap
        if spec.contract is not None:
            entry["contract"] = spec.contract.model_dump(mode="json")
        if spec.policy != InitiativePolicy():
            entry["policy"] = spec.policy.model_dump(mode="json")
        if spec.approval != "automatic":
            entry["approval"] = spec.approval
        if spec.assets:
            entry["assets"] = list(spec.assets)
        if spec.duration_estimate_seconds is not None:
            entry["duration_estimate_seconds"] = spec.duration_estimate_seconds
        remaining.append(entry)
    payload: dict[str, object] = {
        "plan_id": plan.id,
        "version": plan.version,
        "approval": plan.approval,
        "brief": plan.brief[:max_brief_chars],
        "fixed": sorted(fixed, key=lambda item: str(item["id"])),
        "remaining": sorted(remaining, key=lambda item: str(item["id"])),
    }
    if reason is not None and reason.strip():
        payload["reason"] = _one_line(reason)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _context_failures(plan: Plan, initiative: Initiative) -> list[str]:
    """Bounded one-line signatures that fed this initiative's last attempt."""
    if not initiative.attempts:
        return []
    attempt_id = initiative.attempts[-1].id
    lines = [
        _one_line(
            _failure_line(FailureDelta(attempt_id=attempt_id, check=check, error=error))
        )
        for (owner, check, error), record in sorted(plan.failure_signatures.items())
        if owner == initiative.spec.id and attempt_id in record.attempts
    ]
    return lines[-_MAX_FAILURE_DELTAS:]


def _context_evidence(initiative: Initiative) -> list[str]:
    """The latest recorded failure's bounded artifact paths."""
    if not initiative.failures:
        return []
    return [
        _one_line(path)
        for path in initiative.failures[-1].evidence[-_MAX_FAILURE_DELTAS:]
    ]


def usage_from_result(
    result: object, *, category: TokenCategory | None = None
) -> Usage | None:
    """Read planner usage the harness reported, or nothing.

    Token facts come from the harness, never from a local guess: an absent
    usage block means the denominator is understated, which is honest, where a
    fabricated one would quietly flatter the overhead ratio. ``category`` is
    orchestration-owned attribution: an explicit one is stamped over whatever
    the harness claimed, while source, phase, and counts stay the harness's own.
    """
    if not isinstance(result, dict):
        return None
    raw = cast(dict[str, object], result).get("usage")
    if not isinstance(raw, dict):
        return None
    payload = dict(cast(dict[str, object], raw))
    _ = payload.setdefault("source", "harness")
    _ = payload.setdefault("phase", "actual")
    if category is not None:
        payload["category"] = category
    else:
        _ = payload.setdefault("category", "planning")
    try:
        return Usage.model_validate(payload)
    except ValidationError:
        return None


def proposal_from_result(
    result: object,
    *,
    plan_id: str,
    at: datetime,
    version: int = 1,
    default_assignment: Assignment | None = None,
    usage_category: TokenCategory | None = None,
    known_ids: Sequence[str] = (),
    project_root: str | os.PathLike[str] = ".",
) -> PlanProposed:
    """Validate planner output as exactly one typed, dependency-free node.

    ``known_ids`` names nodes the caller will re-declare server-side (a
    recalibration's fixed anchors): a remaining node may depend on them, so
    the DAG is validated against that union, and an id the planner returned
    anyway is a refusal — fixed work is never re-declared by the model.

    The assignment stays exactly what the planner declared; an omitted one is
    filled deterministically from the configured initiative executor
    assignment (`defaults.initiative`), and a project that configures none
    keeps the pre-Kitchen fill. No harness is forced and no fallback is
    chosen here; an undeclared adapter fails later, at command compilation.
    """
    selected_assignment = default_assignment or _executor_default(project_root)
    value = result
    if isinstance(value, PlanProposed):
        initiatives: list[InitiativeSpec] = list(value.initiatives)
    else:
        initiatives_value: object
        if isinstance(value, list):
            initiatives_value = cast(list[object], value)
        elif isinstance(value, dict):
            object_value = cast(dict[str, object], value)
            initiatives_value = object_value.get("initiatives")
            if initiatives_value is None and "initiative" in object_value:
                initiatives_value = [object_value["initiative"]]
            if initiatives_value is None and {"id", "name", "brief"} <= object_value.keys():
                initiatives_value = [object_value]
        else:
            initiatives_value = None
        if not isinstance(initiatives_value, list):
            raise PlannerError("planner output has no initiatives array")
        initiatives = []
        for raw in cast(list[object], initiatives_value):
            if isinstance(raw, InitiativeSpec):
                initiatives.append(raw)
                continue
            if not isinstance(raw, dict):
                raise PlannerError("planner initiative is not an object")
            initiative = dict(cast(dict[str, object], raw))
            _ = initiative.setdefault(
                "assignment", selected_assignment.model_dump(mode="json")
            )
            try:
                initiatives.append(InitiativeSpec.model_validate(initiative))
            except ValidationError as exc:
                raise PlannerError(f"invalid planner initiative: {exc}") from exc
    if not initiatives:
        raise PlannerError("planner returned no initiatives")
    plan_token_cap: int | None = None
    if isinstance(result, dict):
        raw_cap = cast(dict[str, object], result).get("token_cap")
        if raw_cap is None:
            raw_cap = cast(dict[str, object], result).get("plan_token_cap")
        if isinstance(raw_cap, int) and not isinstance(raw_cap, bool):
            plan_token_cap = raw_cap
    known = sorted(set(known_ids))
    collisions = sorted({spec.id for spec in initiatives} & set(known))
    if collisions:
        raise PlannerError(
            "planner re-declared fixed initiative(s) " + ", ".join(collisions)
        )
    anchors = [
        InitiativeSpec(
            id=node_id,
            name=node_id,
            brief=f"fixed {node_id}",
            assignment=selected_assignment,
        )
        for node_id in known
    ]
    try:
        validated = PlanProposed(
            plan_id=plan_id,
            at=at,
            version=version,
            initiatives=[*initiatives, *anchors],
            usage=usage_from_result(cast(object, result), category=usage_category),
            token_cap=plan_token_cap,
        )
    except ValidationError as exc:
        raise PlannerError(f"invalid proposed plan: {exc}") from exc
    return validated.model_copy(update={"initiatives": initiatives})


__all__ = [
    "SMOKE_MARKER",
    "SMOKE_PROMPT",
    "CompletionError",
    "FailureDelta",
    "HarnessSpec",
    "LunaConfigError",
    "PiFrontierPlanner",
    "PiMemoryAuthor",
    "PlannerError",
    "TaskPacket",
    "compile_task_packet",
    "packet_diff",
    "packet_snapshot",
    "preflight_packet",
    "estimate_tokens",
    "agent_name",
    "completion_from_event",
    "executor_launch",
    "write_packet",
    "proposal_from_result",
    "recalibration_context",
    "recalibration_prompt",
    "remaining_work_brief",
    "resolve_harness",
    "resolve_luna_binary",
    "resolve_model_tiers",
    "SmokeProcess",
    "SmokeRunner",
    "adapter_smoke",
    "usage_from_result",
]
