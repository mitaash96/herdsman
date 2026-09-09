"""Small, project-local adapter for herdr's JSON-lines socket API.

Herdsman keeps herdr's objects opaque.  This module turns the few facts needed
by Gate 0 into ``RuntimeFact`` values; callers can explicitly turn those facts
into Herdsman ``RuntimeObserved`` events.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .classes import RuntimeObserved

JsonObject = dict[str, object]


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
        self._request_number: int = 0
        self._worktrees: dict[str, _Worktree] = {}
        self._pane_worktrees: dict[str, _Worktree] = {}
        self._subscriptions: dict[
            str, tuple[asyncio.StreamReader, asyncio.StreamWriter, list[JsonObject]]
        ] = {}
        self._waiters: dict[str, asyncio.Task[RuntimeFact | None]] = {}

    async def check_ready(self, *, force: bool = False) -> None:
        """Check the binary, server, and supported herdr response shape.

        Ping capabilities are intentionally not used as a global gate.  Each
        operation validates its own response, so an unavailable feature is
        reported by that operation as a ``HerdrOperationError``.
        """
        if self._ready is not None and not force:
            return
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

    async def run(
        self, worktree_ref: str, command: str, *, match: str | None = None
    ) -> str:
        if not worktree_ref:
            raise ValueError("worktree reference cannot be empty")
        if not command.strip():
            raise ValueError("command cannot be empty")
        await self.check_ready()
        worktree = await self._resolve_worktree(worktree_ref)
        pane = worktree.root_pane or await self._root_pane(worktree)
        if pane in self._subscriptions:
            raise HerdrOperationError(f"pane {pane!r} already has an active observation")
        # Both the lifecycle stream and the output wait must be established
        # before the command runs.  A fast-exiting root pane is removed by
        # herdr, and neither pane.read nor pane.wait_for_output can recover
        # anything from a pane that is already gone.
        reader, writer, pending = await self._subscribe(pane)
        waiter = (
            asyncio.create_task(self._wait_for_output(pane, match))
            if match is not None
            else None
        )
        try:
            result = await self._request(
                "pane.send_input",
                {"pane_id": pane, "text": command, "keys": ["Enter"]},
            )
            self._expect_type(result, "pane.send_input", "ok", "pane_input_sent")
        except BaseException:
            if waiter is not None:
                _ = waiter.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            raise
        self._pane_worktrees[pane] = worktree
        self._subscriptions[pane] = (reader, writer, pending)
        if waiter is not None:
            self._waiters[pane] = waiter
        return pane

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
        self._expect_type(result, "pane.focus", "pane_focused")

    async def restart_process(self, pane_ref: str, command: str) -> str:
        """Restart the in-pane process: interrupt it, then re-issue its command.

        This is not a domain retry: it creates no new attempt, compiles no
        new packet, and touches no worktree. The foreground process must be
        interrupted before the command is re-issued, or the bytes would feed
        the hung process instead of a fresh shell.
        """
        if not pane_ref:
            raise ValueError("pane reference cannot be empty")
        if not command.strip():
            raise ValueError("command cannot be empty")
        await self.check_ready()
        interrupt = await self._request(
            "pane.send_keys", {"pane_id": pane_ref, "keys": ["C-c"]}
        )
        self._expect_type(interrupt, "pane.send_keys", "ok", "pane_keys_sent")
        result = await self._request(
            "pane.send_input",
            {"pane_id": pane_ref, "text": command, "keys": ["Enter"]},
        )
        self._expect_type(result, "pane.send_input", "ok", "pane_input_sent")
        return pane_ref

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

    async def observe(self, pane_ref: str, *, match: str | None = None) -> AsyncIterator[RuntimeFact]:
        """Stream only relevant events for one pane until it exits.

        herdr's `pane.output_matched` subscription fires once, when the
        subscription is created, and never again, so it cannot carry output
        produced after the pane is launched.  `pane.wait_for_output` is the
        primitive that does: it blocks until the pattern appears and fails
        promptly with a resource error once the pane is gone.  `run` starts it
        before launching the command; this drains it.

        `match` is the recovery resume path: reconnecting to a pane that a
        previous daemon left mid-command must re-arm the marker waiter here,
        or the checkpoint would never be seen.  A waiter parked by `run` is
        reused untouched, so reconnecting never doubles the wait.
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
        waiter = self._waiters.pop(pane_ref, None)
        if waiter is None and match:
            waiter = asyncio.create_task(self._wait_for_output(pane_ref, match))
        try:
            if waiter is not None:
                matched = await waiter
                if matched is not None:
                    # The marker is the completion boundary; the pane is left
                    # running for review, so there is no exit to wait for.
                    yield matched
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
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def observe_events(
        self,
        plan_id: str,
        attempt_id: str,
        pane_ref: str,
        *,
        match: str | None = None,
    ) -> AsyncIterator[RuntimeObserved]:
        """Stream facts already translated to Herdsman audit events."""
        async for fact in self.observe(pane_ref, match=match):
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

    async def _wait_for_output(self, pane_ref: str, match: str) -> RuntimeFact | None:
        """Block until `match` appears in the pane, or the pane is gone."""
        try:
            result = await self._request(
                "pane.wait_for_output",
                {
                    "pane_id": pane_ref,
                    "source": "recent_unwrapped",
                    "match": {"type": "regex", "value": match},
                    "lines": 50,
                    "strip_ansi": True,
                    # Unbounded here; the caller's own deadline is the bound.
                    "timeout_ms": None,
                },
                unbounded=True,
            )
        except HerdrResourceError:
            # The pane exited before printing a match.  The lifecycle stream
            # below reports the exit; the caller decides what that means.
            return None
        # Response: {"type": "output_matched", "pane_id", "revision",
        # "matched_line": str, "read": PaneReadResult}.
        self._expect_type(result, "pane.wait_for_output", "output_matched")
        return RuntimeFact("pane_output_matched", dict(result))

    async def aclose(self) -> None:
        """Release anything parked by `run` that was never observed."""
        for waiter in self._waiters.values():
            _ = waiter.cancel()
        self._waiters.clear()
        for _reader, writer, _pending in self._subscriptions.values():
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self._subscriptions.clear()

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
            # `discard_initiative`, which already gates on a failed or settled
            # initiative, so releasing the checkout is the explicit intent.
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
                    # never re-fires for later output.  Output is recovered by
                    # `pane.wait_for_output` instead; this stream carries only
                    # lifecycle.
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
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            raise

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        path = os.path.expanduser(self.config.socket_path)
        if not os.path.isabs(path):
            path = str(self.project_root / path)
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(path), self.config.timeout
            )
        except asyncio.TimeoutError as exc:
            raise HerdrUnavailable(f"timed out connecting to herdr socket {path}") from exc
        except OSError as exc:
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
        until something happens (`pane.wait_for_output`); the caller's own
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
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _write_frame(self, writer: asyncio.StreamWriter, value: JsonObject) -> None:
        try:
            writer.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            await asyncio.wait_for(writer.drain(), self.config.timeout)
        except asyncio.TimeoutError as exc:
            raise HerdrUnavailable("timed out writing to herdr socket") from exc
        except (OSError, ConnectionError) as exc:
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
            raise HerdrUnavailable(f"timed out reading {what}") from exc
        except (OSError, ConnectionError) as exc:
            raise HerdrUnavailable(f"herdr socket read failed: {exc}") from exc
        if not raw:
            raise HerdrUnavailable(f"herdr socket closed while reading {what}")
        try:
            value = cast(object, json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HerdrProtocolError(f"malformed {what}: {exc}") from exc
        return _object(value, what)

    @staticmethod
    def _expect_type(result: JsonObject, method: str, *expected: str) -> None:
        if result.get("type") not in expected:
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
        text = str(detail)
        normalized = (code + " " + text).lower().replace("-", "_").replace(" ", "_")
        if any(word in normalized for word in ("not_found", "missing", "unknown", "stale")):
            raise HerdrResourceError(f"herdr {method} failed: {text}")
        raise HerdrOperationError(f"herdr {method} failed: {text}")

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
            return RuntimeFact(kind, dict(data))
        if kind not in cls._PANE_EVENTS:
            return None
        if data.get("pane_id") != pane_ref:
            return None
        return RuntimeFact(kind, dict(data))


__all__ = [
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
