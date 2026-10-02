"""Small, project-local adapter for herdr's JSON-lines socket API.

Herdsman keeps herdr's objects opaque.  This module turns the few facts needed
by Gate 0 into ``RuntimeFact`` values; callers can explicitly turn those facts
into Herdsman ``RuntimeObserved`` events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .agent_hooks import HOOK_KINDS, harness_settled
from .classes import RuntimeObserved
from .store import atomic_write_bytes
from .redact import redact, redact_value

JsonObject = dict[str, object]
logger = logging.getLogger(__name__)


class HerdrError(RuntimeError):
    """Base class for adapter and herdr API failures."""


class HerdrConfigError(HerdrError):
    """Project-local adapter configuration is invalid."""


class HerdrUnavailable(HerdrError):
    """The configured herdr binary or server cannot be reached."""


class HerdrProtocolError(HerdrError):
    """herdr returned a malformed or unexpected JSON response."""


class HerdrResourceError(HerdrError):
    """A referenced herdr workspace, worktree, or pane does not exist."""


class HerdrOperationError(HerdrError):
    """herdr rejected an otherwise well-formed operation."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code: str = code


def _object(value: object, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise HerdrProtocolError(f"herdr response {what} is not an object")
    return cast(JsonObject, value)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_text(*values: object) -> str | None:
    for value in values:
        text = _text(value)
        if text is not None:
            return text
    return None


PINNED_HERDR_VERSION = "0.9.3"
"""The herdr release this Herdsman is verified against."""

PINNED_HERDR_PROTOCOL = 22
"""The herdr private protocol generation this adapter speaks."""

HERDR_INSTALL_HINT = (
    "install or update herdr to "
    + PINNED_HERDR_VERSION
    + " (see the herdr project's install instructions), then run `herdr status`"
)


def pin_status(version: str | None, protocol: int | None) -> str | None:
    """A human-readable drift warning, or None when the peer matches the pin.

    Drift warns and never blocks: each operation already validates its own
    response, so a herdr newer than the pin usually works, and refusing to
    coordinate over a version string would strand working setups.
    """
    if version is None:
        return "herdr version is unknown; " + HERDR_INSTALL_HINT
    notes: list[str] = []
    if version != PINNED_HERDR_VERSION:
        notes.append(f"herdr {version} differs from the pinned {PINNED_HERDR_VERSION}")
    if protocol is not None and protocol != PINNED_HERDR_PROTOCOL:
        notes.append(f"protocol {protocol} differs from the pinned {PINNED_HERDR_PROTOCOL}")
    if not notes:
        return None
    return "; ".join(notes) + " — " + HERDR_INSTALL_HINT


@dataclass(frozen=True)
class HerdrConfig:
    """The adapter's project-local herdr settings.

    Readiness is based on the ping and operation response shapes rather than a
    pinned release.
    """

    binary: str = "herdr"
    socket_path: str = field(
        default_factory=lambda: os.environ.get(
            "HERDR_SOCKET_PATH", "~/.config/herdr/herdr.sock"
        )
    )
    timeout: float = 10.0

    @property
    def socket(self) -> str:
        """Alias matching herdr's own terminology."""
        return self.socket_path

    @classmethod
    def from_project(
        cls, root: str | os.PathLike[str] = ".", path: str | os.PathLike[str] | None = None
    ) -> "HerdrConfig":
        root_path = Path(root)
        if path is not None:
            configured = Path(path)
            if not configured.is_absolute():
                configured = root_path / configured
        elif "HERDSMAN_HERDR_CONFIG" in os.environ:
            configured = Path(os.environ["HERDSMAN_HERDR_CONFIG"])
        else:
            candidates = (
                root_path / ".herdsman" / "herdr.json",
                root_path / ".herdsman" / "config.json",
            )
            configured = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        if not configured.exists():
            return cls()
        return cls.from_file(configured)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "HerdrConfig":
        file_path = Path(path)
        try:
            raw = cast(object, json.loads(file_path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise HerdrConfigError(f"cannot read herdr config {file_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise HerdrConfigError(f"invalid JSON in herdr config {file_path}: {exc}") from exc
        try:
            data = _object(raw, f"config {file_path}")
        except HerdrProtocolError as exc:
            raise HerdrConfigError(str(exc)) from exc
        # Accept a top-level {"herdr": {...}} as well as a direct object.  The
        # latter keeps the project-local file short and obvious.
        nested = data.get("herdr")
        if isinstance(nested, dict):
            data = cast(JsonObject, nested)

        def string_setting(key: str, default: str) -> str:
            value = data.get(key, default)
            if not isinstance(value, str):
                raise HerdrConfigError(f"herdr config {key} must be a string")
            return value

        timeout = data.get("timeout", cls.timeout)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise HerdrConfigError("herdr config timeout must be a number")
        try:
            return cls(
                binary=string_setting(
                    "binary", _first_text(data.get("herdr_binary")) or cls.binary
                ),
                socket_path=string_setting(
                    "socket_path",
                    _first_text(data.get("socket"), data.get("herdr_socket"))
                    or cls().socket_path,
                ),
                timeout=float(timeout),
            )
        except (TypeError, ValueError) as exc:
            raise HerdrConfigError(f"invalid herdr config {file_path}: {exc}") from exc

    def __post_init__(self) -> None:
        if not self.binary:
            raise HerdrConfigError("herdr binary cannot be empty")
        if not self.socket_path:
            raise HerdrConfigError("herdr socket cannot be empty")
        if self.timeout <= 0:
            raise HerdrConfigError("herdr timeout must be positive")


@dataclass(frozen=True)
class RuntimeFact:
    """An external herdr fact with no herdr type in Herdsman's domain model."""

    kind: str
    detail: JsonObject

    def as_event(
        self,
        plan_id: str,
        attempt_id: str,
        at: datetime | None = None,
    ) -> RuntimeObserved:
        return to_runtime_observed(plan_id, attempt_id, self, at=at)


@dataclass(frozen=True)
class AgentLaunch:
    """One interactive agent start and the prompt that hands it its packet."""

    name: str
    kind: str
    args: tuple[str, ...]
    prompt: str
    marker_dir: Path | None = None


_AGENT_START_TIMEOUT_MS = 120_000
_SETTLED = ["idle", "done"]
# Seconds after launch before the read-only check that the launch prompt began a turn.
_LAUNCH_CHECK_S = 3.0
_LAUNCH_POLL_S = 1.0


@dataclass(frozen=True)
class _Worktree:
    ref: str
    workspace_id: str | None
    path: str | None
    root_pane: str | None
    detail: JsonObject


_HERDSMAN_BRANCH_PREFIX = "herdsman/"
"""Worktrees Herdsman creates are branched herdsman/{plan}/{initiative}/{attempt}."""


@dataclass(frozen=True)
class WorktreeEntry:
    """One project worktree as herdr reports it, with Herdsman's ownership flag.

    Ownership is the branch prefix Herdsman itself chose at creation; a
    worktree whose branch herdr cannot report is honestly not claimed.  The
    raw herdr entry rides along for audit, as with ``RuntimeFact``.
    """

    path: str
    branch: str | None
    workspace_id: str | None
    herdsman_owned: bool
    detail: JsonObject


@dataclass(frozen=True)
class PaneEntry:
    """One live pane inside a Herdsman-owned workspace."""

    pane_id: str
    workspace_id: str


@dataclass(frozen=True)
class RuntimeInventory:
    """The Herdsman-relevant slice of live herdr state, project-scoped."""

    worktrees: tuple[WorktreeEntry, ...]
    panes: tuple[PaneEntry, ...]


@dataclass(frozen=True)
class Reconciliation:
    """Deterministic surviving/missing/orphaned classification of references.

    Surviving and missing classify persisted references against one live
    inventory; orphaned lists Herdsman-owned live resources no persisted
    reference claims.  Nothing here launches, restarts, or removes work.
    """

    surviving_worktrees: tuple[str, ...] = ()
    missing_worktrees: tuple[str, ...] = ()
    orphaned_worktrees: tuple[str, ...] = ()
    surviving_panes: tuple[str, ...] = ()
    missing_panes: tuple[str, ...] = ()
    orphaned_panes: tuple[str, ...] = ()


def reconcile_inventory(
    inventory: RuntimeInventory,
    *,
    worktree_refs: Sequence[str] = (),
    pane_refs: Sequence[str] = (),
) -> Reconciliation:
    """Classify persisted references against one inventory, deterministically.

    A worktree reference survives when it equals a live worktree's checkout
    path or open workspace id (both are shapes Herdsman persists as refs); a
    pane reference survives when that exact pane id is alive inside a
    Herdsman-owned workspace.  Orphans are Herdsman-owned live resources
    claimed by no persisted reference.  Output tuples are sorted, so equal
    inputs always yield an equal classification.
    """
    worktree_ref_set = set(worktree_refs)
    pane_ref_set = set(pane_refs)

    def _claimed(entry: WorktreeEntry) -> bool:
        return entry.path in worktree_ref_set or (
            entry.workspace_id is not None and entry.workspace_id in worktree_ref_set
        )

    def _live(ref: str) -> bool:
        return any(ref in (entry.path, entry.workspace_id) for entry in inventory.worktrees)

    owned_pane_ids = {pane.pane_id for pane in inventory.panes}
    return Reconciliation(
        surviving_worktrees=tuple(sorted(ref for ref in worktree_ref_set if _live(ref))),
        missing_worktrees=tuple(sorted(ref for ref in worktree_ref_set if not _live(ref))),
        orphaned_worktrees=tuple(
            sorted(
                entry.path
                for entry in inventory.worktrees
                if entry.herdsman_owned and not _claimed(entry)
            )
        ),
        surviving_panes=tuple(sorted(pane_ref_set & owned_pane_ids)),
        missing_panes=tuple(sorted(pane_ref_set - owned_pane_ids)),
        orphaned_panes=tuple(sorted(owned_pane_ids - pane_ref_set)),
    )


def to_runtime_observed(
    plan_id: str,
    attempt_id: str,
    fact: RuntimeFact,
    *,
    at: datetime | None = None,
) -> RuntimeObserved:
    """Translate one adapter fact into the existing untyped domain event."""
    timestamp = at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("runtime observation timestamp must be timezone-aware")
    return RuntimeObserved(
        plan_id=plan_id,
        at=timestamp,
        attempt_id=attempt_id,
        kind=fact.kind,
        detail=dict(fact.detail),
    )


class HerdrAdapter:
    """The narrow Gate 0 boundary around herdr's local socket."""

    _WORKTREE_EVENTS: frozenset[str] = frozenset(
        {"worktree_created", "worktree_opened", "worktree_removed"}
    )
    _PANE_EVENTS: frozenset[str] = frozenset(
        {
            "pane_output_matched",
            "pane_output_changed",
            "pane_agent_status_changed",
            "pane_exited",
        }
    )

    def __init__(
        self,
        config: HerdrConfig | None = None,
        *,
        project_root: str | os.PathLike[str] = ".",
        config_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.project_root: Path = Path(project_root).expanduser().resolve()
        self.config: HerdrConfig = config or HerdrConfig.from_project(
            self.project_root, path=config_path
        )
        self._ready: tuple[str, int] | None = None
        self._pin_warning_emitted: bool = False
        self._request_number: int = 0
        self._worktrees: dict[str, _Worktree] = {}
        self._pane_worktrees: dict[str, _Worktree] = {}
        self._subscriptions: dict[
            str, tuple[asyncio.StreamReader, asyncio.StreamWriter, list[JsonObject]]
        ] = {}
        self._waiters: dict[str, asyncio.Task[list[RuntimeFact] | None]] = {}
        self._facts: dict[str, asyncio.Queue[RuntimeFact]] = {}
        self._turns: dict[str, tuple[str, Path | None, int]] = {}
        self._blocked: set[str] = set()

    async def check_ready(self, *, force: bool = False) -> None:
        """Check the binary, server, and supported herdr response shape.

        Ping capabilities are intentionally not used as a global gate.  Each
        operation validates its own response, so an unavailable feature is
        reported by that operation as a ``HerdrOperationError``.
        """
        if self._ready is not None and not force:
            return
        self._ready = None
        binary = shutil.which(self.config.binary)
        if binary is None:
            raise HerdrUnavailable(
                f"herdr binary {self.config.binary!r} is not available; "
                + "install the configured herdr binary"
            )
        result = await self._request("ping", {}, check=False)
        self._expect_type(result, "ping", "pong")
        version = result.get("version")
        protocol = result.get("protocol")
        if not isinstance(version, str) or not isinstance(protocol, int) or isinstance(protocol, bool):
            raise HerdrProtocolError("herdr ping response lacks version or protocol")
        self._ready = (version, protocol)
        drift = pin_status(version, protocol)
        if drift is not None and not self._pin_warning_emitted:
            logger.warning("%s", drift)
            self._pin_warning_emitted = True

    async def create_worktree(self, branch: str) -> str:
        if not branch.strip():
            raise ValueError("worktree branch cannot be empty")
        await self.check_ready()
        result = await self._request(
            "worktree.create",
            {"cwd": str(self.project_root), "branch": branch, "focus": False},
        )
        # Response: {"workspace": WorkspaceInfo, "worktree": WorktreeInfo,
        # "root_pane": PaneInfo}.
        self._expect_type(result, "worktree.create", "worktree_created")
        workspace = _object(result.get("workspace", {}), "workspace")
        worktree = _object(result.get("worktree", {}), "worktree")
        root_pane_value = result.get("root_pane", result.get("pane"))
        root_pane = (
            _first_text(
                cast(JsonObject, root_pane_value).get("pane_id"),
                cast(JsonObject, root_pane_value).get("id"),
            )
            if isinstance(root_pane_value, dict)
            else _first_text(root_pane_value)
        )
        workspace_id = _first_text(
            workspace.get("workspace_id"), worktree.get("open_workspace_id")
        )
        path = _first_text(worktree.get("path"))
        ref = _first_text(
            result.get("worktree_ref"), result.get("worktree_id"),
            worktree.get("worktree_id"), worktree.get("id"), workspace_id, path,
        )
        if ref is None:
            raise HerdrProtocolError("herdr worktree.create response has no reference")
        state = _Worktree(ref, workspace_id, path, root_pane, dict(result))
        self._worktrees[ref] = state
        if root_pane:
            self._pane_worktrees[root_pane] = state
        return ref

    async def create_workspace(self, label: str) -> str:
        """Open a plain (no worktree) workspace at the project root; return its root pane."""
        await self.check_ready()
        result = await self._request(
            "workspace.create",
            {"cwd": str(self.project_root), "label": label, "focus": False},
        )
        self._expect_type(result, "workspace.create", "workspace_created")
        pane = result.get("root_pane")
        pane_id = _first_text(cast(JsonObject, pane).get("pane_id")) if isinstance(pane, dict) else None
        if pane_id is None:
            raise HerdrProtocolError("herdr workspace.create response has no root pane")
        return pane_id

    async def run(self, worktree_ref: str, launch: AgentLaunch) -> str:
        if not worktree_ref:
            raise ValueError("worktree reference cannot be empty")
        await self.check_ready()
        worktree = await self._resolve_worktree(worktree_ref)
        pane = worktree.root_pane or await self._root_pane(worktree)
        if pane in self._subscriptions:
            raise HerdrOperationError(f"pane {pane!r} already has an active observation")
        # Subscribe before startup so a startup exit is not lost.
        reader, writer, pending = await self._subscribe(pane)
        self._facts[pane] = asyncio.Queue()
        self._turns[pane] = (launch.kind, launch.marker_dir, 0)
        try:
            # The prompt rides on the launch argv, so the harness holds it until
            # its input is live (and across a trust dialog) instead of herdr
            # typing it into a TUI that may not be listening yet.
            self._begin_turn(pane)
            await self._start_agent(pane, launch)
        except BaseException:
            _ = self._facts.pop(pane, None)
            _ = self._turns.pop(pane, None)
            await self._close(writer)
            raise
        self._pane_worktrees[pane] = worktree
        self._subscriptions[pane] = (reader, writer, pending)
        self._waiters[pane] = asyncio.create_task(self._launch_until_settled(pane))
        return pane

    async def _start_agent(self, pane: str, launch: AgentLaunch) -> None:
        """Launch the harness with its prompt on argv; `_confirm_launch` checks it took."""
        if "\n" in launch.prompt or "\t" in launch.prompt:
            # herdr refuses these on a launch argv; hand long specs over as a file.
            raise ValueError("launch prompt must be a single line; pass a file pointer")
        try:
            result = await self._request(
                "agent.start",
                {"name": launch.name, "kind": launch.kind, "pane_id": pane,
                 "args": [*launch.args, launch.prompt], "timeout_ms": _AGENT_START_TIMEOUT_MS},
                unbounded=True,
            )
            # Not awaiting launch_pending: with a launch prompt it lasts the whole first turn.
            self._expect_type(result, "agent.start", "agent_started")
        except HerdrOperationError as exc:
            if exc.code != "agent_not_ready":
                raise  # a startup dialog is left for the operator

    async def run_agent_visible(
        self,
        launch: AgentLaunch,
        *,
        label: str,
        timeout: float,
        on_pane: Callable[[str], None] | None = None,
    ) -> JsonObject:
        """Run an interactive agent in a reviewable workspace; return final AgentInfo."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        pane: str | None = None
        finished = False
        try:
            async with asyncio.timeout(timeout):
                pane = await self.create_workspace(label)
                if on_pane is not None:
                    on_pane(pane)
                self._turns[pane] = (launch.kind, launch.marker_dir, 0)
                self._begin_turn(pane)
                await self._start_agent(pane, launch)
                await self._confirm_launch(pane)
                result = await self._request(
                    "agent.wait", {"target": pane, "until": _SETTLED}, unbounded=True
                )
                self._expect_type(result, "agent.wait", "agent_info")
                agent, _ = await self._settle(pane, result)
                finished = True
                return agent
        finally:
            if pane is not None and not finished:
                with contextlib.suppress(HerdrError, OSError):
                    await asyncio.shield(self.interrupt_pane(pane))
            if pane is not None:
                _ = self._turns.pop(pane, None)

    async def notify_user(self, message: str) -> bool:
        """Request a herdr notification; return whether herdr displayed it."""
        if not message.strip():
            raise ValueError("notification message cannot be empty")
        # herdr 0.9.1 / protocol 22: title is the only required parameter.
        result = await self._request("notification.show", {"title": redact(message)})
        self._expect_type(result, "notification.show", "notification_show")
        shown = result.get("shown")
        reason = result.get("reason")
        if not isinstance(shown, bool) or reason not in (
            "shown", "disabled", "rate_limited", "no_foreground_client", "busy"
        ):
            raise HerdrProtocolError("herdr notification.show response lacks shown or reason")
        return shown

    async def nudge_pane(self, pane_ref: str, text: str) -> None:
        """Send free-text guidance to a live pane without starting new work."""
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        if not text.strip():
            raise ValueError("nudge text cannot be empty")
        await self.check_ready()
        result = await self._request(
            "pane.send_text", {"pane_id": pane_ref, "text": text}
        )
        self._expect_type(result, "pane.send_text", "ok")

    async def focus_pane(self, pane_ref: str) -> None:
        """Focus the herdr pane running a task, from its pane reference."""
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        await self.check_ready()
        result = await self._request("pane.focus", {"pane_id": pane_ref})
        self._expect_type(result, "pane.focus", "pane_focused", "pane_info", "ok")

    async def restart_agent(
        self, pane_ref: str, prompt: str, *, marker_dir: Path | None = None
    ) -> str:
        """Interrupt and re-prompt the same live agent; return when its new turn starts."""
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        await self.interrupt_pane(pane_ref)
        if pane_ref not in self._turns:
            await self._recover_turn(pane_ref, marker_dir, restore=False)
        self._begin_turn(pane_ref)
        _ = await self._submit(
            pane_ref, prompt, {"until": ["working", "blocked"], "timeout_ms": 30_000}
        )
        return pane_ref

    async def interrupt_pane(self, pane_ref: str) -> None:
        """Interrupt an agent turn with Esc without exiting its TUI."""
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        await self.check_ready()
        result = await self._request(
            "agent.send_keys", {"target": pane_ref, "keys": ["esc"]}
        )
        self._expect_type(result, "agent.send_keys", "ok")

    async def worktree_path(self, worktree_ref: str) -> Path:
        """Expose the checkout path only to mechanical collectors."""
        if not worktree_ref:
            raise ValueError("worktree reference cannot be empty")
        await self.check_ready()
        worktree = await self._resolve_worktree(worktree_ref)
        if worktree.path is None:
            raise HerdrResourceError(
                f"worktree {worktree_ref!r} has no checkout path"
            )
        return Path(worktree.path)

    async def observe(
        self, pane_ref: str, *, rearm: bool = False, marker_dir: Path | None = None
    ) -> AsyncGenerator[RuntimeFact, None]:
        """Observe until herdr settles the turn or the pane exits.

        Recovery/restart uses rearm to wait on a live agent without prompting.
        A waiter parked by run is reused, never doubled.
        """
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        await self.check_ready()
        worktree = self._pane_worktrees.get(pane_ref)
        if worktree is None:
            pane_result = await self._request("pane.get", {"pane_id": pane_ref})
            self._expect_type(pane_result, "pane.get", "pane_info")
            pane = _object(pane_result.get("pane"), "pane")
            workspace_id = _first_text(pane.get("workspace_id"))
            worktree = _Worktree(pane_ref, workspace_id, None, pane_ref, {})
            self._pane_worktrees[pane_ref] = worktree

        subscription = self._subscriptions.pop(pane_ref, None)
        if subscription is None:
            reader, writer, pending = await self._subscribe(pane_ref)
        else:
            reader, writer, pending = subscription
        queue = self._facts.setdefault(pane_ref, asyncio.Queue())
        waiter = self._waiters.pop(pane_ref, None)
        queued: asyncio.Task[RuntimeFact] | None = None
        try:
            if waiter is None and rearm:
                await self._recover_turn(pane_ref, marker_dir)
                waiter = asyncio.create_task(self._wait_settled(pane_ref))
            if waiter is not None:
                while not waiter.done():
                    queued = asyncio.create_task(queue.get())
                    _ = await asyncio.wait({waiter, queued}, return_when=asyncio.FIRST_COMPLETED)
                    if queued.done():
                        yield queued.result()
                    else:
                        _ = queued.cancel()
                        _ = await asyncio.gather(queued, return_exceptions=True)
                    queued = None
                while not queue.empty():
                    yield queue.get_nowait()
                settled = await waiter
                if settled is not None:
                    for fact in settled:
                        yield fact
                    return
            for frame in pending:
                fact = self._fact_for_frame(frame, pane_ref, worktree.workspace_id)
                if fact is None:
                    continue
                if fact.kind in {"pane_exited", "worktree_removed"}:
                    # Backlog: every pending frame was read before the
                    # subscription was acknowledged, and `run` acknowledges
                    # before it sends any input -- so this cannot be our pane's
                    # exit.  herdr pane ids recycle across a restart, so a
                    # stale terminal frame would otherwise end a live
                    # observation and surface as a phantom missing checkpoint.
                    continue
                yield fact
            while True:
                frame = await self._read_frame(reader, "herdr event stream", timeout=None)
                fact = self._fact_for_frame(frame, pane_ref, worktree.workspace_id)
                if fact is None:
                    continue
                yield fact
                if fact.kind in {"pane_exited", "worktree_removed"}:
                    return
        finally:
            if queued is not None:
                _ = queued.cancel()
                _ = await asyncio.gather(queued, return_exceptions=True)
            _ = self._facts.pop(pane_ref, None)
            self._blocked.discard(pane_ref)
            if waiter is not None:
                _ = waiter.cancel()
                _ = await asyncio.gather(waiter, return_exceptions=True)
            await self._close(writer)

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        rearm: bool = False,
    ) -> AsyncGenerator[RuntimeObserved, None]:
        """Stream facts already translated to Herdsman audit events."""
        marker_dir = self.project_root / ".herdsman" / "hooks" / attempt_id
        async with contextlib.aclosing(
            self.observe(pane_ref, rearm=rearm, marker_dir=marker_dir)
        ) as facts:
            async for fact in facts:
                yield fact.as_event(plan_id, attempt_id)

    async def inventory(self) -> RuntimeInventory:
        """List this project's worktrees and the panes of Herdsman-owned ones.

        Read-only: no worktree is created, opened, or removed and no pane is
        started.  Ownership is the `herdsman/` branch prefix Herdsman itself
        sets at creation, so the operator's own worktrees and panes are never
        inventoried or reported as orphans.  Panes are enumerated only for
        Herdsman-owned open workspaces; a pane herdr cannot attribute to a
        workspace is a protocol violation, not an unknown to guess about.
        """
        await self.check_ready()
        listing = await self._request("worktree.list", {"cwd": str(self.project_root)})
        self._expect_type(listing, "worktree.list", "worktree_list")
        entries_value = listing.get("worktrees")
        if not isinstance(entries_value, list):
            raise HerdrProtocolError("herdr worktree.list response has no worktrees")
        worktrees: list[WorktreeEntry] = []
        owned_workspaces: set[str] = set()
        for entry_value in cast(list[object], entries_value):
            if not isinstance(entry_value, dict):
                continue
            entry = cast(JsonObject, entry_value)
            path = _text(entry.get("path"))
            if path is None:
                raise HerdrProtocolError("herdr worktree.list entry has no path")
            branch = _text(entry.get("branch"))
            workspace_id = _text(entry.get("open_workspace_id"))
            herdsman_owned = branch is not None and branch.startswith(_HERDSMAN_BRANCH_PREFIX)
            worktrees.append(
                WorktreeEntry(path, branch, workspace_id, herdsman_owned, dict(entry))
            )
            if herdsman_owned and workspace_id is not None:
                owned_workspaces.add(workspace_id)
        return RuntimeInventory(tuple(worktrees), await self._owned_panes(owned_workspaces))

    async def _owned_panes(self, workspaces: set[str]) -> tuple[PaneEntry, ...]:
        if not workspaces:
            return ()
        # The workspace filter is applied here, not server-side: herdr's
        # pane.list takes an optional workspace id, so the one global listing
        # covers every Herdsman-owned workspace in a single request.
        listing = await self._request("pane.list", {})
        self._expect_type(listing, "pane.list", "pane_list")
        panes_value = listing.get("panes")
        if not isinstance(panes_value, list):
            raise HerdrProtocolError("herdr pane.list response has no panes")
        panes: list[PaneEntry] = []
        for pane_value in cast(list[object], panes_value):
            if not isinstance(pane_value, dict):
                continue
            pane = cast(JsonObject, pane_value)
            pane_id = _text(pane.get("pane_id"))
            workspace_id = _text(pane.get("workspace_id"))
            if pane_id is None or workspace_id is None:
                raise HerdrProtocolError(
                    "herdr pane.list entry has no pane_id or workspace_id"
                )
            if workspace_id in workspaces:
                panes.append(PaneEntry(pane_id, workspace_id))
        return tuple(panes)

    def _emit_blocked(self, pane: str, agent: JsonObject) -> None:
        queue = self._facts.get(pane)
        if queue is not None and pane not in self._blocked:
            self._blocked.add(pane)
            queue.put_nowait(RuntimeFact("agent_blocked", cast(JsonObject, redact_value(agent))))

    def _begin_turn(self, pane: str) -> None:
        kind, marker_dir, _ = self._turns[pane]
        since_ns = time.monotonic_ns()
        if kind in HOOK_KINDS:
            if marker_dir is None:
                raise HerdrOperationError("missing harness marker directory")
            try:
                boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                if not boot:
                    raise ValueError("empty boot id")
                marker_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(marker_dir / "since", json.dumps([since_ns, boot]).encode())
            except (OSError, ValueError) as exc:
                raise HerdrOperationError(f"cannot persist harness prompt boundary: {exc}") from exc
        self._turns[pane] = (kind, marker_dir, since_ns)

    async def _recover_turn(
        self, pane: str, marker_dir: Path | None, *, restore: bool = True
    ) -> None:
        previous = self._turns.get(pane)
        if previous is None:
            result = await self._request("agent.get", {"target": pane})
            self._expect_type(result, "agent.get", "agent_info")
            agent = _object(result.get("agent"), "agent")
            kind = _text(agent.get("agent"))
            if kind is None:
                raise HerdrProtocolError("herdr agent has no kind")
        else:
            kind, directory, _ = previous
            marker_dir = directory or marker_dir
        since_ns = 0
        if restore and kind in HOOK_KINDS:
            try:
                if marker_dir is None:
                    raise ValueError("missing marker directory")
                raw = cast(object, json.loads((marker_dir / "since").read_text()))
                boundary = cast(list[object], raw) if isinstance(raw, list) else []
                boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                if (len(boundary) != 2
                        or type(boundary[0]) is not int or boundary[0] <= 0
                        or not boot or boundary[1] != boot):
                    raise ValueError("invalid boundary or different OS boot")
                since_ns = boundary[0]
            except (OSError, ValueError) as exc:
                raise HerdrOperationError(f"cannot recover harness prompt boundary: {exc}") from exc
        self._turns[pane] = (kind, marker_dir, since_ns)

    async def _submit(self, pane: str, prompt: str, wait: JsonObject) -> JsonObject:
        """Submit this turn's prompt; re-submit once only on proof it never arrived."""
        request: JsonObject = {"target": pane, "text": prompt, "wait": wait}
        try:
            result = await self._request("agent.prompt", request, unbounded=True)
        except HerdrOperationError as exc:
            if exc.code != "agent_prompt_stalled" or not await self._undelivered(pane):
                raise
            # Fresh Claude can drop a prompt submitted right after its trust
            # dialog. A second stall fails closed.
            result = await self._request("agent.prompt", request, unbounded=True)
        self._expect_type(result, "agent.prompt", "agent_prompted")
        return result

    async def _undelivered(self, pane: str) -> bool:
        """Hook kinds only: no Start since this turn's boundary and herdr reports idle.

        UserPromptSubmit writes Start synchronously on acceptance, so its
        absence proves the harness never took the prompt. Anything else is
        ambiguous and must not be re-submitted.
        """
        kind, marker_dir, since_ns = self._turns[pane]
        if kind not in HOOK_KINDS or marker_dir is None or since_ns <= 0:
            return False
        try:
            if int((marker_dir / "start").read_text()) > since_ns:
                return False
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return False
        result = await self._request("agent.get", {"target": pane})
        self._expect_type(result, "agent.get", "agent_info")
        return _object(result.get("agent"), "agent").get("agent_status") in _SETTLED

    async def _launch_until_settled(self, pane: str) -> list[RuntimeFact] | None:
        try:
            await self._confirm_launch(pane)
        except HerdrResourceError:
            return None
        return await self._wait_settled(pane)

    async def _confirm_launch(self, pane: str) -> None:
        """Read-only check, `_LAUNCH_CHECK_S` after launch, that the prompt began a turn.

        Working, done, blocked (an operator dialog holds the prompt), a completed
        turn, or this turn's Start marker all pass; a fast agent may already be
        finished. `unknown` means the harness is not up yet, so keep reading.
        Settled with no sign of a turn fails, but only on two reads
        `_LAUNCH_POLL_S` apart: pi reports a brief startup idle before its first turn.
        """
        await asyncio.sleep(_LAUNCH_CHECK_S)
        idle_reads = 0
        async with asyncio.timeout(_AGENT_START_TIMEOUT_MS / 1000):
            while True:
                result = await self._request("agent.get", {"target": pane})
                self._expect_type(result, "agent.get", "agent_info")
                agent = _object(result.get("agent"), "agent")
                status = agent.get("agent_status")
                if status == "blocked":
                    self._emit_blocked(pane, agent)
                    return
                if (status in ("working", "done") or agent.get("completion_seq") is not None
                        or self._turn_started(pane)):
                    queue = self._facts.get(pane)
                    if queue is not None:
                        queue.put_nowait(RuntimeFact("launch_confirmed", {"agent_status": status}))
                    return
                if status != "unknown":
                    idle_reads += 1
                    if idle_reads == 2:
                        raise HerdrOperationError(
                            f"launch prompt not observed: agent is {status} with no turn "
                            + f"{_LAUNCH_CHECK_S:g}s after start",
                            "agent_prompt_stalled",
                        )
                await asyncio.sleep(_LAUNCH_POLL_S)

    def _turn_started(self, pane: str) -> bool:
        """Hook kinds: this turn's Start marker exists (herdr may not report working)."""
        kind, marker_dir, since_ns = self._turns[pane]
        if kind not in HOOK_KINDS or marker_dir is None:
            return False
        try:
            return int((marker_dir / "start").read_text()) > since_ns
        except (OSError, ValueError):
            return False

    async def _wait_settled(self, pane: str) -> list[RuntimeFact] | None:
        try:
            result = await self._request(
                "agent.wait", {"target": pane, "until": _SETTLED}, unbounded=True
            )
            self._expect_type(result, "agent.wait", "agent_info")
            _, facts = await self._settle(pane, result)
            return facts
        except HerdrResourceError:
            return None

    async def _settle(self, pane: str, result: JsonObject) -> tuple[JsonObject, list[RuntimeFact]]:
        agent = _object(result.get("agent"), "agent")
        while True:
            status = agent.get("agent_status")
            if status == "blocked":
                self._emit_blocked(pane, agent)
                await asyncio.sleep(0.1)
            elif status not in _SETTLED:
                raise HerdrProtocolError("herdr settle response is not idle or done")
            else:
                self._blocked.discard(pane)
                kind, marker_dir, since_ns = self._turns.get(pane, (str(agent.get("agent")), None, 0))
                if kind not in HOOK_KINDS:
                    break
                if marker_dir is None or since_ns <= 0:
                    raise HerdrOperationError("missing harness prompt boundary")
                if harness_settled(marker_dir, since_ns):
                    break
                # Already-idle waits return immediately; stay inside the caller's budget.
                await asyncio.sleep(0.1)
            result = await self._request(
                "agent.wait", {"target": pane, "until": _SETTLED}, unbounded=True
            )
            self._expect_type(result, "agent.wait", "agent_info")
            agent = _object(result.get("agent"), "agent")
        session = agent.get("agent_session")
        return agent, [RuntimeFact("agent_settled", {
            "agent_status": agent.get("agent_status"),
            "agent_session": session if isinstance(session, dict) else None,
        })]

    async def aclose(self) -> None:
        """Release anything parked by `run` that was never observed."""
        waiters = list(self._waiters.values())
        for waiter in waiters:
            _ = waiter.cancel()
        _ = await asyncio.gather(*waiters, return_exceptions=True)
        self._waiters.clear()
        for _reader, writer, _pending in self._subscriptions.values():
            await self._close(writer)
        self._subscriptions.clear()
        self._facts.clear()
        self._turns.clear()
        self._blocked.clear()

    async def remove_worktree(self, worktree_ref: str) -> None:
        if not worktree_ref:
            raise ValueError("worktree reference cannot be empty")
        await self.check_ready()
        worktree = await self._resolve_worktree(worktree_ref)
        if worktree.workspace_id is None:
            raise HerdrResourceError(
                f"worktree {worktree_ref!r} has no open herdr workspace"
            )
        try:
            # `force` is not optional here.  An attempt worktree that did any
            # work is dirty by definition -- that dirt is what the checkpoint
            # measured -- and herdr refuses an unforced remove with
            # `dirty_worktree_requires_force`.  The only caller is
            # `discard_initiative`, which already gates on a failed, cancelled,
            # or settled initiative, so releasing the checkout is the explicit intent.
            result = await self._request(
                "worktree.remove", {"workspace_id": worktree.workspace_id, "force": True}
            )
        except HerdrResourceError:
            # A command is allowed to exit its root shell.  Herdr then closes
            # the workspace, but the linked worktree can still be removed by
            # reopening that path through herdr first.
            if worktree.path is None:
                raise
            opened = await self._request(
                "worktree.open",
                {
                    "cwd": str(self.project_root),
                    "path": worktree.path,
                    "focus": False,
                },
            )
            self._expect_type(opened, "worktree.open", "worktree_opened")
            # worktree.open response: {"workspace": WorkspaceInfo,
            # "worktree": WorktreeInfo, "root_pane": PaneInfo}.
            workspace = _object(opened.get("workspace"), "workspace")
            workspace_id = _first_text(workspace.get("workspace_id"))
            if workspace_id is None:
                raise HerdrProtocolError("herdr worktree.open response has no workspace_id")
            result = await self._request(
                "worktree.remove", {"workspace_id": workspace_id, "force": True}
            )
        self._expect_type(result, "worktree.remove", "worktree_removed")
        _ = self._worktrees.pop(worktree_ref, None)
        for pane, owner in list(self._pane_worktrees.items()):
            if owner.ref == worktree.ref:
                _ = self._pane_worktrees.pop(pane, None)

    async def _resolve_worktree(self, ref: str) -> _Worktree:
        known = self._worktrees.get(ref)
        if known is not None:
            return known

        # A workspace id is the stable opaque reference returned by herdr.  The
        # fallback list lookup also permits a path-shaped reference after an
        # adapter restart, without making paths part of Herdsman's API.
        try:
            # workspace_info.workspace and worktree_list.worktrees[] resolve refs
            # after an adapter restart.
            result = await self._request("workspace.get", {"workspace_id": ref})
            self._expect_type(result, "workspace.get", "workspace_info")
            workspace = _object(result.get("workspace"), "workspace")
            workspace_id = _first_text(workspace.get("workspace_id"), ref)
            path = None
            worktree_value = workspace.get("worktree")
            if isinstance(worktree_value, dict):
                worktree = cast(JsonObject, worktree_value)
                path = _first_text(worktree.get("checkout_path"))
            state = _Worktree(ref, workspace_id, path, None, dict(workspace))
            self._worktrees[ref] = state
            return state
        except HerdrResourceError:
            listing = await self._request(
                "worktree.list", {"cwd": str(self.project_root)}
            )
            self._expect_type(listing, "worktree.list", "worktree_list")
            entries_value = listing.get("worktrees")
            if not isinstance(entries_value, list):
                raise HerdrProtocolError("herdr worktree.list response has no worktrees")
            entries = cast(list[object], entries_value)
            for entry_value in entries:
                if not isinstance(entry_value, dict):
                    continue
                entry = cast(JsonObject, entry_value)
                if entry.get("path") != ref:
                    continue
                workspace_id = _first_text(entry.get("open_workspace_id"))
                state = _Worktree(ref, workspace_id, ref, None, entry)
                self._worktrees[ref] = state
                return state
            raise HerdrResourceError(f"unknown herdr worktree {ref!r}")

    async def _root_pane(self, worktree: _Worktree) -> str:
        if worktree.workspace_id is None:
            raise HerdrResourceError(f"worktree {worktree.ref!r} has no workspace")
        # pane_info.pane and pane_list.panes[] carry pane_id references.
        result = await self._request(
            "pane.list", {"workspace_id": worktree.workspace_id}
        )
        self._expect_type(result, "pane.list", "pane_list")
        panes_value = result.get("panes")
        if not isinstance(panes_value, list) or not panes_value:
            raise HerdrResourceError(
                f"herdr workspace {worktree.workspace_id!r} has no root pane"
            )
        first = cast(list[object], panes_value)[0]
        pane = _object(first, "pane list entry")
        pane_ref = _text(pane.get("pane_id"))
        if pane_ref is None:
            raise HerdrProtocolError("herdr pane.list entry has no pane_id")
        return pane_ref

    async def _subscribe(
        self, pane_ref: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, list[JsonObject]]:
        reader, writer = await self._connect()
        self._request_number += 1
        request_id = f"herdsman-{self._request_number}"
        request: JsonObject = {
            "id": request_id,
            "method": "events.subscribe",
            "params": {
                "subscriptions": [
                    {"type": event} for event in (
                        "worktree.created", "worktree.opened", "worktree.removed"
                    )
                ]
                + [
                    # `pane.output_matched` is deliberately absent: herdr
                    # evaluates it once, when the subscription is created, and
                    # never re-fires for later output. This stream carries
                    # only lifecycle; agent.prompt/wait carries settlement.
                    {"type": "pane.agent_status_changed", "pane_id": pane_ref},
                    {"type": "pane.exited"},
                ]
            },
        }
        pending: list[JsonObject] = []
        # Request: {"subscriptions": [...]}; stream event: {"event": str,
        # "data": object}; acknowledgement: {"type": "subscription_started"}.
        try:
            await self._write_frame(writer, request)
            while True:
                frame = await self._read_frame(
                    reader, "herdr subscription acknowledgement", timeout=self.config.timeout
                )
                if frame.get("id") != request_id:
                    pending.append(frame)
                    continue
                if "error" in frame:
                    self._raise_api_error("events.subscribe", frame["error"])
                result = _object(frame.get("result"), "subscription acknowledgement")
                if result.get("type") != "subscription_started":
                    raise HerdrProtocolError(
                        "herdr events.subscribe returned an unexpected acknowledgement"
                    )
                return reader, writer, pending
        except BaseException:
            self._ready = None
            await self._close(writer)
            raise

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), self.config.timeout)
        except (TimeoutError, OSError):
            pass

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        path = os.path.expanduser(self.config.socket_path)
        if not os.path.isabs(path):
            path = str(self.project_root / path)
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(path), self.config.timeout
            )
        except asyncio.TimeoutError as exc:
            self._ready = None
            raise HerdrUnavailable(f"timed out connecting to herdr socket {path}") from exc
        except OSError as exc:
            self._ready = None
            raise HerdrUnavailable(f"cannot connect to herdr socket {path}: {exc}") from exc

    async def _request(
        self,
        method: str,
        params: JsonObject,
        *,
        check: bool = True,
        unbounded: bool = False,
    ) -> JsonObject:
        """Issue one request.

        `unbounded` drops the read deadline for methods that block server-side
        until something happens (agent startup/settle); the caller's own
        deadline bounds those.
        """
        if check:
            await self.check_ready()
        reader, writer = await self._connect()
        self._request_number += 1
        request_id = f"herdsman-{self._request_number}"
        try:
            await self._write_frame(
                writer, {"id": request_id, "method": method, "params": params}
            )
            frame = await self._read_frame(
                reader,
                f"herdr {method} response",
                timeout=None if unbounded else self.config.timeout,
            )
            # Response envelope: {"id": str, "result": object} or
            # {"id": str, "error": {"code": str, "message": str}}.
            if frame.get("id") != request_id:
                raise HerdrProtocolError(
                    f"herdr {method} response has unexpected request id"
                )
            if "error" in frame:
                self._raise_api_error(method, frame["error"])
            return _object(frame.get("result"), f"{method} result")
        except (HerdrUnavailable, HerdrProtocolError):
            self._ready = None
            raise
        finally:
            await self._close(writer)

    async def _write_frame(self, writer: asyncio.StreamWriter, value: JsonObject) -> None:
        try:
            writer.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            await asyncio.wait_for(writer.drain(), self.config.timeout)
        except asyncio.TimeoutError as exc:
            self._ready = None
            raise HerdrUnavailable("timed out writing to herdr socket") from exc
        except (OSError, ConnectionError) as exc:
            self._ready = None
            raise HerdrUnavailable(f"herdr socket write failed: {exc}") from exc

    async def _read_frame(
        self,
        reader: asyncio.StreamReader,
        what: str,
        *,
        timeout: float | None,
    ) -> JsonObject:
        try:
            raw = (
                await reader.readline()
                if timeout is None
                else await asyncio.wait_for(reader.readline(), timeout)
            )
        except asyncio.TimeoutError as exc:
            self._ready = None
            raise HerdrUnavailable(f"timed out reading {what}") from exc
        except (OSError, ConnectionError) as exc:
            self._ready = None
            raise HerdrUnavailable(f"herdr socket read failed: {exc}") from exc
        if not raw:
            self._ready = None
            raise HerdrUnavailable(f"herdr socket closed while reading {what}")
        try:
            value = cast(object, json.loads(raw.decode("utf-8")))
            return _object(value, what)
        except (UnicodeDecodeError, json.JSONDecodeError, HerdrProtocolError) as exc:
            self._ready = None
            raise HerdrProtocolError(f"malformed {what}: {exc}") from exc

    def _expect_type(self, result: JsonObject, method: str, *expected: str) -> None:
        if result.get("type") not in expected:
            self._ready = None
            raise HerdrProtocolError(
                f"herdr {method} returned unexpected result type {result.get('type')!r}"
            )

    @staticmethod
    def _raise_api_error(method: str, error: object) -> None:
        # ErrorBody: {"code": str, "message": str}.
        detail: object
        if isinstance(error, dict):
            error_object = cast(JsonObject, error)
            message = error_object.get("message")
            detail = message if message is not None else error_object
        else:
            detail = error
        code = (
            str(cast(JsonObject, error).get("code", ""))
            if isinstance(error, dict)
            else ""
        )
        text = redact(str(detail))
        normalized = (code + " " + text).lower().replace("-", "_").replace(" ", "_")
        # A stall names no resource, whatever words its message uses.
        if code != "agent_prompt_stalled" and any(
            word in normalized for word in ("not_found", "missing", "unknown", "stale")
        ):
            raise HerdrResourceError(f"herdr {method} failed: {text}")
        raise HerdrOperationError(f"herdr {method} failed: {text}", code)

    @classmethod
    def _fact_for_frame(
        cls, frame: JsonObject, pane_ref: str, workspace_id: str | None
    ) -> RuntimeFact | None:
        event_value = frame.get("event")
        data_value = frame.get("data")
        # Pane payloads use pane_id; worktree payloads use workspace_id and may
        # include a nested workspace object.
        data = cast(JsonObject, data_value) if isinstance(data_value, dict) else frame
        event = event_value if isinstance(event_value, str) else data.get("type")
        if not isinstance(event, str):
            return None
        kind = event.replace(".", "_")
        if kind in cls._WORKTREE_EVENTS:
            workspace_value = data.get("workspace")
            workspace = (
                cast(JsonObject, workspace_value)
                if isinstance(workspace_value, dict)
                else cast(JsonObject, {})
            )
            event_workspace = _first_text(
                data.get("workspace_id"), workspace.get("workspace_id")
            )
            # Worktree events are machine-wide: unrelated workspaces arrive on
            # this stream too.  Without our own workspace id nothing can be
            # attributed, so drop them; the pane's own exit still ends observe.
            if workspace_id is None or event_workspace != workspace_id:
                return None
            return RuntimeFact(kind, cast(JsonObject, redact_value(data)))
        if kind not in cls._PANE_EVENTS:
            return None
        if data.get("pane_id") != pane_ref:
            return None
        return RuntimeFact(kind, cast(JsonObject, redact_value(data)))


__all__ = [
    "AgentLaunch",
    "HerdrAdapter",
    "HerdrConfig",
    "HerdrConfigError",
    "HerdrError",
    "HerdrOperationError",
    "HerdrProtocolError",
    "HerdrResourceError",
    "HerdrUnavailable",
    "PaneEntry",
    "Reconciliation",
    "RuntimeFact",
    "RuntimeInventory",
    "WorktreeEntry",
    "reconcile_inventory",
    "to_runtime_observed",
]
