"""Small, project-local adapter for herdr's JSON-lines socket API.

Herdsman keeps herdr's objects opaque.  This module turns the few facts needed
by Gate 0 into ``RuntimeFact`` values; callers can explicitly turn those facts
into Herdsman ``RuntimeObserved`` events.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
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


class HerdrIncompatible(HerdrError):
    """The connected herdr server is outside the configured compatibility range."""


class HerdrProtocolError(HerdrError):
    """herdr returned a malformed or unexpected JSON response."""


class HerdrResourceError(HerdrError):
    """A referenced herdr workspace, worktree, or pane does not exist."""


class HerdrOperationError(HerdrError):
    """herdr rejected an otherwise well-formed operation."""


_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def _version_key(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise HerdrConfigError(f"invalid herdr version {value!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


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

    ``0.7.2``/protocol ``16`` are the pinned Gate 0 release.  A project may
    widen either range in its own ``.herdsman/herdr.json`` when it has tested a
    compatible release.
    """

    binary: str = "herdr"
    socket_path: str = field(
        default_factory=lambda: os.environ.get(
            "HERDR_SOCKET_PATH", "~/.config/herdr/herdr.sock"
        )
    )
    min_version: str | None = "0.7.2"
    max_version: str | None = "0.7.2"
    min_protocol: int | None = 16
    max_protocol: int | None = 16
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

        compatibility = data.get("compatibility")
        compat = cast(JsonObject, compatibility) if isinstance(compatibility, dict) else {}
        version_value = data.get(
            "version", data.get("expected_version", compat.get("version"))
        )
        protocol_value_raw = data.get(
            "protocol", data.get("expected_protocol", compat.get("protocol"))
        )
        version = (
            cast(JsonObject, version_value)
            if isinstance(version_value, dict)
            else {}
        )
        protocol = (
            cast(JsonObject, protocol_value_raw)
            if isinstance(protocol_value_raw, dict)
            else cast(JsonObject, {})
        )
        exact_protocol = (
            protocol_value_raw
            if isinstance(protocol_value_raw, int) and not isinstance(protocol_value_raw, bool)
            else None
        )
        exact_version = version_value if isinstance(version_value, str) else None
        min_version = _first_text(
            data.get("min_version"), data.get("version_min"),
            data.get("version_minimum"), version.get("min"), version.get("minimum"),
        )
        max_version = _first_text(
            data.get("max_version"), data.get("version_max"),
            data.get("version_maximum"), version.get("max"), version.get("maximum"),
        )
        if exact_version is not None:
            min_version = max_version = exact_version

        def protocol_number(*keys: str) -> int | None:
            for key in keys:
                value = data.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
            for key in keys:
                inner_key = key.removeprefix("protocol_")
                value = protocol.get(inner_key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
                if inner_key == "min":
                    value = protocol.get("minimum")
                elif inner_key == "max":
                    value = protocol.get("maximum")
                else:
                    value = None
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
            return None

        def string_setting(key: str, default: str) -> str:
            value = data.get(key, default)
            if not isinstance(value, str):
                raise HerdrConfigError(f"herdr config {key} must be a string")
            return value

        timeout = data.get("timeout", cls.timeout)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise HerdrConfigError("herdr config timeout must be a number")
        min_protocol = protocol_number(
            "min_protocol", "protocol_min", "protocol_minimum"
        )
        max_protocol = protocol_number(
            "max_protocol", "protocol_max", "protocol_maximum"
        )
        if exact_protocol is not None:
            min_protocol = max_protocol = exact_protocol
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
                min_version=min_version if min_version is not None else cls.min_version,
                max_version=max_version if max_version is not None else cls.max_version,
                min_protocol=min_protocol if min_protocol is not None else cls.min_protocol,
                max_protocol=max_protocol if max_protocol is not None else cls.max_protocol,
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
        if self.min_version is not None:
            _ = _version_key(self.min_version)
        if self.max_version is not None:
            _ = _version_key(self.max_version)
        if (
            self.min_version is not None
            and self.max_version is not None
            and _version_key(self.min_version) > _version_key(self.max_version)
        ):
            raise HerdrConfigError("herdr minimum version exceeds maximum version")
        if self.min_protocol is not None and self.min_protocol < 0:
            raise HerdrConfigError("herdr minimum protocol cannot be negative")
        if self.max_protocol is not None and self.max_protocol < 0:
            raise HerdrConfigError("herdr maximum protocol cannot be negative")
        if (
            self.min_protocol is not None
            and self.max_protocol is not None
            and self.min_protocol > self.max_protocol
        ):
            raise HerdrConfigError("herdr minimum protocol exceeds maximum protocol")


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

    async def check_ready(self, *, force: bool = False) -> None:
        """Check the binary, server, and configured version/protocol range."""
        if self._ready is not None and not force:
            return
        binary = shutil.which(self.config.binary)
        if binary is None:
            raise HerdrUnavailable(
                f"herdr binary {self.config.binary!r} is not available; "
                + "install the configured herdr release"
            )
        result = await self._request("ping", {}, check=False)
        version = result.get("version")
        protocol = result.get("protocol")
        if not isinstance(version, str) or not isinstance(protocol, int) or isinstance(protocol, bool):
            raise HerdrProtocolError("herdr ping response lacks version or protocol")
        try:
            version_number = _version_key(version)
        except HerdrConfigError as exc:
            raise HerdrProtocolError(str(exc)) from exc
        if self.config.min_version and version_number < _version_key(self.config.min_version):
            raise HerdrIncompatible(
                f"herdr version {version} is older than supported {self.config.min_version}"
            )
        if self.config.max_version and version_number > _version_key(self.config.max_version):
            raise HerdrIncompatible(
                f"herdr version {version} is newer than supported {self.config.max_version}"
            )
        if self.config.min_protocol is not None and protocol < self.config.min_protocol:
            raise HerdrIncompatible(
                f"herdr protocol {protocol} is older than supported {self.config.min_protocol}"
            )
        if self.config.max_protocol is not None and protocol > self.config.max_protocol:
            raise HerdrIncompatible(
                f"herdr protocol {protocol} is newer than supported {self.config.max_protocol}"
            )
        self._ready = (version, protocol)

    async def create_worktree(self, branch: str) -> str:
        if not branch.strip():
            raise ValueError("worktree branch cannot be empty")
        await self.check_ready()
        result = await self._request(
            "worktree.create",
            {"cwd": str(self.project_root), "branch": branch, "focus": False},
        )
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

    async def run(self, worktree_ref: str, command: str) -> str:
        if not worktree_ref:
            raise ValueError("worktree reference cannot be empty")
        if not command.strip():
            raise ValueError("command cannot be empty")
        await self.check_ready()
        worktree = await self._resolve_worktree(worktree_ref)
        pane = worktree.root_pane or await self._root_pane(worktree)
        result = await self._request(
            "pane.send_input",
            {"pane_id": pane, "text": command, "keys": ["Enter"]},
        )
        self._expect_type(result, "pane.send_input", "ok", "pane_input_sent")
        self._pane_worktrees[pane] = worktree
        return pane

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

    async def observe(self, pane_ref: str) -> AsyncIterator[RuntimeFact]:
        """Stream only relevant events for one pane until it exits."""
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

        reader, writer, pending = await self._subscribe(pane_ref)
        try:
            for frame in pending:
                fact = self._fact_for_frame(frame, pane_ref, worktree.workspace_id)
                if fact is not None:
                    yield fact
                    if fact.kind in {"pane_exited", "worktree_removed"}:
                        return
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
        self, plan_id: str, attempt_id: str, pane_ref: str
    ) -> AsyncIterator[RuntimeObserved]:
        """Stream facts already translated to Herdsman audit events."""
        async for fact in self.observe(pane_ref):
            yield fact.as_event(plan_id, attempt_id)

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
            result = await self._request(
                "worktree.remove", {"workspace_id": worktree.workspace_id, "force": False}
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
            workspace = _object(opened.get("workspace"), "workspace")
            workspace_id = _first_text(workspace.get("workspace_id"))
            if workspace_id is None:
                raise HerdrProtocolError("herdr worktree.open response has no workspace_id")
            result = await self._request(
                "worktree.remove", {"workspace_id": workspace_id, "force": False}
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
                    {
                        "type": "pane.output_matched",
                        "pane_id": pane_ref,
                        "source": "recent_unwrapped",
                        "match": {"type": "regex", "value": ".*"},
                        "lines": 20,
                        "strip_ansi": True,
                    },
                    {"type": "pane.agent_status_changed", "pane_id": pane_ref},
                    {"type": "pane.exited"},
                ]
            },
        }
        pending: list[JsonObject] = []
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
        self, method: str, params: JsonObject, *, check: bool = True
    ) -> JsonObject:
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
                reader, f"herdr {method} response", timeout=self.config.timeout
            )
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
            if workspace_id is not None and event_workspace != workspace_id:
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
    "HerdrIncompatible",
    "HerdrOperationError",
    "HerdrProtocolError",
    "HerdrResourceError",
    "HerdrUnavailable",
    "RuntimeFact",
    "to_runtime_observed",
]
