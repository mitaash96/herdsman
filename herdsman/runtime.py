"""Boundaries for planner output, executor packets, and completion evidence."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from .checkpoint import Completion
from .classes import (
    ArtifactRef,
    Assignment,
    EXECUTOR_HARNESS,
    InitiativeSpec,
    MemoryLeaf,
    PlanProposed,
    Routes,
    Usage,
)
from .memory import MemoryDelivery, deliver_memory, leaf_version


_DEFAULT_ASSIGNMENT = Assignment(harness=EXECUTOR_HARNESS, model="cheap-1")
_LUNA_MAPPING_NAME = "luna.json"
_HARNESS_MAPPING_NAME = "harnesses.json"
_MODEL_TIER_NAME = "models.json"
_PROMPT_PLACEHOLDER = "{prompt}"
"""The one packet placeholder a harness argv template must hold, exactly once."""


class LunaConfigError(RuntimeError):
    """The project-local Luna executable mapping is absent or invalid."""


class PlannerError(RuntimeError):
    """The supervised planner did not return one valid initiative."""


class CompletionError(RuntimeError):
    """The executor did not emit valid completion evidence."""


@dataclass(frozen=True)
class HarnessSpec:
    """One harness's compiled launch template: argv plus optional model argv."""

    argv: tuple[str, ...]
    """Ends with the prompt placeholder; the prompt replaces it at compile."""
    model_argv: tuple[str, ...] = ()
    """Inserted before the prompt with the model appended, only when one is set."""


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
    failures: tuple[str, ...] = ()
    """Bounded failure deltas from this initiative's prior attempts, one line
    each. Never the failed attempt's transcript."""

    def json(self) -> str:
        return json.dumps(
            {
                "initiative_id": self.initiative_id,
                "name": self.name,
                "brief": self.brief,
                "assignment": self.assignment.model_dump(mode="json"),
                "routes": self.routes.model_dump(mode="json"),
                "subtasks": list(self.subtasks),
                "inputs": [ref.model_dump(mode="json") for ref in self.inputs],
                "memory": list(self.memory),
                "memory_pointers": list(self.memory_pointers),
                "memory_inline": list(self.memory_inline),
                "memory_leaf_ids": list(self.memory_leaf_ids),
                "memory_leaf_versions": list(self.memory_leaf_versions),
                "memory_mode": self.memory_mode,
                "failures": list(self.failures),
            },
            separators=(",", ":"),
            sort_keys=True,
        )


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
) -> TaskPacket:
    """Copy only this initiative's contract and its inputs across the boundary.

    An executor sees its own node and the evidence its dependencies produced —
    never sibling briefs, never the plan. A retry compiles the task's current
    brief version and assignment — the attempt snapshots them — every
    run-scoped memory leaf as one deterministic line, and at most the last few
    failure deltas as bounded one-line evidence; the failed attempt's
    transcript never crosses the boundary.
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
        subtasks=tuple(spec.subtasks),
        inputs=tuple(inputs),
        memory=legacy,
        memory_pointers=() if delivery is None else delivery.pointers,
        memory_inline=() if delivery is None else delivery.inline,
        memory_leaf_ids=carried_ids,
        memory_leaf_versions=carried_versions,
        memory_mode="legacy" if delivery is None else delivery.mode,
        # Oldest first in, most recent kept: a retry needs the freshest
        # failures, and the bound keeps the packet lean.
        failures=tuple(
            _failure_line(delta) for delta in list(failures)[-_MAX_FAILURE_DELTAS:]
        ),
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


def resolve_luna_binary(project_root: str | os.PathLike[str] = ".") -> str:
    """Read the explicit project-local Luna executable mapping."""
    mapping_path = _mapping_path(project_root, _LUNA_MAPPING_NAME)
    try:
        raw = cast(object, json.loads(mapping_path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise LunaConfigError(
            f"Luna mapping is missing at {mapping_path}; create it with "
            + '{"binary":"/path/to/luna"}'
        ) from exc
    except OSError as exc:
        raise LunaConfigError(f"cannot read Luna mapping {mapping_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LunaConfigError(f"invalid JSON in Luna mapping {mapping_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LunaConfigError(
            f"Luna mapping {mapping_path} must be exactly "
            + '{"binary":"/path/to/luna"}'
        )
    mapping = cast(dict[str, object], raw)
    if set(mapping) != {"binary"}:
        raise LunaConfigError(
            f"Luna mapping {mapping_path} must be exactly "
            + '{"binary":"/path/to/luna"}'
        )
    binary = mapping["binary"]
    if not isinstance(binary, str) or not binary.strip():
        raise LunaConfigError(
            f"Luna mapping {mapping_path} field binary must be a non-empty string"
        )
    return binary


def resolve_model_tiers(
    project_root: str | os.PathLike[str] = ".",
) -> dict[str, str]:
    """Read the optional project-local model tier map.

    `{"cheap-1": "cheap", "opus-5": "frontier"}`. Absent means no opinion, and
    no opinion means no warning — Herdsman does not ship a model catalog, and
    guessing a tier from a model name would be a warning nobody can trust.
    """
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


CHECKPOINT_MARKER = "HERDSMAN_CHECKPOINT"
# Anchored so the shell's echo of the command, which contains the marker inside
# the prompt text, cannot match.  See `completion_from_detail`.
CHECKPOINT_PATTERN = f"^{CHECKPOINT_MARKER} "


def resolve_harness(
    harness: str, *, project_root: str | os.PathLike[str] = "."
) -> HarnessSpec:
    """Resolve one harness's launch template, selected solely by the name.

    Luna keeps its explicit `.herdsman/luna.json` mapping unchanged. Every
    other harness is configured in `.herdsman/harnesses.json` as an argv
    template holding exactly one prompt placeholder plus an optional model
    argv -- no discovery, health, capabilities, defaults, or fallback: those
    stay Sprint 8. An unconfigured harness fails here, at command
    compilation, instead of launching something that cannot run.
    """
    if harness == EXECUTOR_HARNESS:
        return HarnessSpec(
            argv=(
                resolve_luna_binary(project_root),
                "--no-session",
                "--mode",
                "text",
                "--print",
                _PROMPT_PLACEHOLDER,
            ),
            model_argv=("--model",),
        )
    return _resolve_harness_entry(harness, project_root=project_root)


def _mapping_path(project_root: str | os.PathLike[str], name: str) -> Path:
    return Path(project_root).expanduser().resolve() / ".herdsman" / name


def _resolve_harness_entry(
    harness: str, *, project_root: str | os.PathLike[str]
) -> HarnessSpec:
    mapping_path = _mapping_path(project_root, _HARNESS_MAPPING_NAME)
    example = (
        '{"harness-name":{"argv":["/path/to/harness","--print",'
        + f'{_PROMPT_PLACEHOLDER}],"model_argv":["--model"]}}'
    )
    try:
        raw = cast(object, json.loads(mapping_path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise LunaConfigError(
            f"harness mapping is missing at {mapping_path}; create it with {example}"
        ) from exc
    except OSError as exc:
        raise LunaConfigError(f"cannot read harness mapping {mapping_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LunaConfigError(f"invalid JSON in harness mapping {mapping_path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise LunaConfigError(
            f"harness mapping {mapping_path} must be a non-empty object"
        )
    mapping = cast(dict[str, object], raw)
    entry = mapping.get(harness)
    if entry is None:
        raise LunaConfigError(
            f"harness {harness!r} is not configured in {mapping_path}; "
            + "a task can launch only a configured harness"
        )
    if not isinstance(entry, dict):
        raise LunaConfigError(
            f"harness {harness!r} in {mapping_path} must be an object"
        )
    entry_dict = cast(dict[str, object], entry)
    unknown = set(entry_dict) - {"argv", "model_argv"}
    if unknown:
        raise LunaConfigError(
            f"harness {harness!r} in {mapping_path} has unknown fields "
            + f"{sorted(unknown)}; only argv and model_argv are read"
        )
    argv = entry_dict.get("argv")
    if not isinstance(argv, list) or len(cast(list[object], argv)) < 2:
        raise LunaConfigError(
            f"harness {harness!r} in {mapping_path} needs an argv array of at "
            + "least the executable and the prompt placeholder"
        )
    placeholder_count = 0
    template: list[str] = []
    for element in cast(list[object], argv):
        if not isinstance(element, str) or not element.strip():
            raise LunaConfigError(
                f"harness {harness!r} in {mapping_path} argv elements must be "
                + "non-empty strings"
            )
        if element == _PROMPT_PLACEHOLDER:
            placeholder_count += 1
        elif "{" in element or "}" in element:
            raise LunaConfigError(
                f"harness {harness!r} in {mapping_path} argv element "
                + f"{element!r} holds an unknown placeholder; the only one read "
                + f"is {_PROMPT_PLACEHOLDER}"
            )
        template.append(element)
    if placeholder_count != 1:
        raise LunaConfigError(
            f"harness {harness!r} in {mapping_path} argv must hold exactly one "
            + f"{_PROMPT_PLACEHOLDER} element, got {placeholder_count}"
        )
    model_argv: list[str] = []
    raw_model_argv = entry_dict.get("model_argv", [])
    if not isinstance(raw_model_argv, list):
        raise LunaConfigError(
            f"harness {harness!r} in {mapping_path} model_argv must be an array"
        )
    for element in cast(list[object], raw_model_argv):
        if not isinstance(element, str) or not element.strip():
            raise LunaConfigError(
                f"harness {harness!r} in {mapping_path} model_argv elements must "
                + "be non-empty strings"
            )
        if "{" in element or "}" in element:
            raise LunaConfigError(
                f"harness {harness!r} in {mapping_path} model_argv element "
                + f"{element!r} holds a placeholder; the model value is appended, "
                + "never substituted"
            )
        model_argv.append(element)
    return HarnessSpec(argv=tuple(template), model_argv=tuple(model_argv))


def executor_command(
    packet: TaskPacket, *, project_root: str | os.PathLike[str] = "."
) -> str:
    """Compile the explicit harness invocation carrying one packet."""
    spec = resolve_harness(packet.assignment.harness, project_root=project_root)
    prompt = (
        (
            "Implement the supplied Herdsman task packet in this worktree. "
            "Do not modify global harness configuration. Run the requested checks. "
            "After the work and checks finish, print exactly one final line beginning "
            "HERDSMAN_CHECKPOINT followed by JSON with integer exit_code and a usage "
            "object containing integer input_tokens, integer output_tokens, and "
            "source=\"harness\". The line is machine-read; do not omit it.\n"
            "TASK_PACKET="
        )
        + packet.json()
    )
    args = list(spec.argv)
    index = args.index(_PROMPT_PLACEHOLDER)
    model = packet.assignment.model
    if model:
        model_args = [*spec.model_argv, model]
        args[index:index] = model_args
        index += len(model_args)
    args[index] = prompt
    # The pane is deliberately left alive.  The checkpoint marker is the
    # completion boundary; exiting the shell makes herdr drop the pane, and a
    # dropped pane's output cannot be read back (`pane.wait_for_output` and
    # `pane.read` both fail with "pane not found").  `herdsman discard`
    # releases the worktree once its evidence has been reviewed.
    return " ".join(shlex.quote(arg) for arg in args)


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    stdout, stderr = await process.communicate()
    return stdout or b"", stderr or b""


class PiMemoryAuthor:
    """One bounded, non-interactive Pi call for evidence-only memory salvage."""

    def __init__(self, *, binary: str = "pi", model: str = "default", timeout: float = 120.0) -> None:
        self.binary = binary
        self.model = model
        self.timeout = timeout

    async def salvage(self, report: str) -> object:
        prompt = (
            "Return JSON only as {\"leaves\":[...]} for project-local memory. "
            "Each leaf must contain id, subject, one-line claim, evidence refs copied "
            "only from the supplied report, scope, origin, lifetime, and optional body. "
            "Do not invent evidence, status, versions, or owner fields. "
            "Use origin=salvage and lifetime=project.\nEVIDENCE_REPORT=\n" + report
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
    """One bounded, non-interactive Pi call for the supervised frontier."""

    binary: str
    model: str
    timeout: float

    def __init__(
        self,
        *,
        binary: str = "pi",
        model: str = "default",
        timeout: float = 120.0,
    ) -> None:
        self.binary = binary
        self.model = model
        self.timeout = timeout

    async def propose(self, brief: str) -> object:
        prompt = (
            (
                "You are Herdsman's supervised frontier planner. Return JSON only, "
                "with an initiatives array. Each initiative must have id, name, brief, "
                "assignment {harness, model}, routes {reads, writes}, subtasks, and "
                "depends_on listing the ids it consumes. Decompose into independent "
                "initiatives wherever the work allows; dependencies must be acyclic. "
                "Declare write routes precisely — two initiatives that write the same "
                "path cannot run concurrently. Use harness luna.\nBRIEF="
            )
            + brief
        )
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                "--no-session",
                "--mode",
                "json",
                "--print",
                "--model",
                self.model,
                prompt,
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


def usage_from_result(result: object) -> Usage | None:
    """Read planner usage the harness reported, or nothing.

    Token facts come from the harness, never from a local guess: an absent
    usage block means the denominator is understated, which is honest, where a
    fabricated one would quietly flatter the overhead ratio.
    """
    if not isinstance(result, dict):
        return None
    raw = cast(dict[str, object], result).get("usage")
    if not isinstance(raw, dict):
        return None
    payload = dict(cast(dict[str, object], raw))
    _ = payload.setdefault("source", "harness")
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
) -> PlanProposed:
    """Validate planner output as exactly one typed, dependency-free node."""
    selected_assignment = default_assignment or _DEFAULT_ASSIGNMENT
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
    for spec in initiatives:
        if spec.assignment.harness != EXECUTOR_HARNESS:
            raise PlannerError(
                f"executor harness must be explicit {EXECUTOR_HARNESS}, got "
                + f"{spec.assignment.harness!r} on initiative {spec.id}"
            )
    try:
        return PlanProposed(
            plan_id=plan_id,
            at=at,
            version=version,
            initiatives=initiatives,
            usage=usage_from_result(result),
        )
    except ValidationError as exc:
        raise PlannerError(f"invalid proposed plan: {exc}") from exc


def completion_from_detail(detail: Mapping[str, object]) -> Completion | None:
    """Read a completion marker from one Herdr output evidence payload."""
    read_value = detail.get("read")
    read = cast(Mapping[str, object], read_value) if isinstance(read_value, dict) else None
    if detail.get("truncated") is True or (read is not None and read.get("truncated") is True):
        return None
    candidates: list[str] = []
    for source in (detail, read):
        if source is None:
            continue
        for key in ("text", "matched_line"):
            text = source.get(key)
            if isinstance(text, str):
                candidates.append(text)
    marker = CHECKPOINT_MARKER
    for text in candidates:
        for line in text.splitlines():
            if not line.strip().startswith(marker):
                continue
            payload = line.strip()[len(marker) :].strip()
            try:
                raw = cast(object, json.loads(payload))
            except json.JSONDecodeError:
                # Herdr may redeliver a line while the executor is still
                # writing it.  Wait for a complete marker instead of
                # treating that partial evidence as a protocol violation.
                continue
            try:
                if not isinstance(raw, dict):
                    raise ValueError("marker payload is not an object")
                data = cast(dict[str, object], raw)
                exit_code = data.get("exit_code")
                if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                    raise ValueError("marker exit_code must be an integer")
                usage = Usage.model_validate(data.get("usage"))
                if usage.source != "harness":
                    raise CompletionError(
                        "HERDSMAN_CHECKPOINT usage source must be harness"
                    )
                return Completion(exit_code=exit_code, usage=usage)
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                raise CompletionError(f"invalid HERDSMAN_CHECKPOINT marker: {exc}") from exc
    return None


__all__ = [
    "CHECKPOINT_MARKER",
    "CHECKPOINT_PATTERN",
    "CompletionError",
    "FailureDelta",
    "HarnessSpec",
    "LunaConfigError",
    "PiFrontierPlanner",
    "PiMemoryAuthor",
    "PlannerError",
    "TaskPacket",
    "compile_task_packet",
    "estimate_tokens",
    "completion_from_detail",
    "executor_command",
    "proposal_from_result",
    "resolve_harness",
    "resolve_luna_binary",
    "resolve_model_tiers",
    "usage_from_result",
]
