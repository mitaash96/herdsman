"""The adapter against a fake herdr socket server.

Gate 0 asks for a worktree, a mock worker, and received runtime events. The
fake server speaks herdr's line-delimited JSON protocol, so this exercises the
real framing, request ids, subscription handshake, and event filtering.
"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from herdsman.classes import PlanCreated, RuntimeObserved
from herdsman.checkpoint import Completion
from herdsman.daemon import Daemon
from herdsman.runtime import (
    CHECKPOINT_MARKER,
    CHECKPOINT_PATTERN,
    completion_from_detail,
)
from herdsman.herdr import (
    HerdrAdapter,
    HerdrConfig,
    HerdrProtocolError,
    HerdrResourceError,
    HerdrUnavailable,
    PaneEntry,
    Reconciliation,
    RuntimeFact,
    RuntimeInventory,
    WorktreeEntry,
    reconcile_inventory,
    to_runtime_observed,
)
from herdsman.store import EventStore

PANE = "ws1:p1"

Frame = dict[str, object]

RESPONSES: dict[str, Frame] = {
    "ping": {"type": "pong", "version": "0.7.2", "protocol": 16},
    "worktree.create": {
        "type": "worktree_created",
        "workspace": {"workspace_id": "ws1"},
        "worktree": {"worktree_id": "wt1", "path": "/repo/.worktrees/gate-0"},
        "root_pane": {"pane_id": PANE},
    },
    "pane.send_input": {"type": "ok"},
    "pane.send_keys": {"type": "ok"},
    "pane.send_text": {"type": "ok"},
    "pane.focus": {"type": "pane_focused"},
    "worktree.remove": {"type": "worktree_removed"},
    "worktree.list": {
        "type": "worktree_list",
        "source": {
            "repo_key": "k",
            "repo_name": "repo",
            "repo_root": "/repo",
            "source_checkout_path": "/repo",
        },
        "worktrees": [],
    },
    "pane.list": {"type": "pane_list", "panes": []},
}

# What the mock worker produces once its command is running.
PUSHED: list[Frame] = [
    {"event": "pane.output_matched", "data": {"pane_id": PANE, "text": "hello"}},
    # Filtered out: another pane's traffic must never reach this observer.
    {"event": "pane.output_matched", "data": {"pane_id": "ws9:p9", "text": "noise"}},
    # Filtered out: an unknown event must not expand Herdsman's runtime API.
    {"event": "pane.unexpected", "data": {"pane_id": PANE}},
    {"event": "pane.exited", "data": {"pane_id": PANE, "exit_code": 0}},
]


class FakeHerdr:
    """One herdr server: a request per connection, plus a subscription stream."""

    def __init__(
        self,
        path: Path,
        errors: Frame | None = None,
        *,
        responses: dict[str, Frame] | None = None,
        pre_ack: list[Frame] | None = None,
        subscription_ack_type: str = "subscription_started",
        wrong_response_id_for: set[str] | None = None,
        pushed: list[Frame] | None = None,
    ) -> None:
        self.path: Path = path
        self.errors: Frame = errors or {}
        self.responses: dict[str, Frame] = RESPONSES | (responses or {})
        self.pre_ack: list[Frame] = pre_ack or []
        self.subscription_ack_type: str = subscription_ack_type
        self.wrong_response_id_for: set[str] = wrong_response_id_for or set()
        self.pushed: list[Frame] = PUSHED if pushed is None else pushed
        self.methods: list[str] = []
        self.requests: list[Frame] = []
        self.server: asyncio.Server | None = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while line := await reader.readline():
            request = cast(Frame, json.loads(line))
            method = str(request["method"])
            self.methods.append(method)
            self.requests.append(request)
            response_id = "wrong" if method in self.wrong_response_id_for else request["id"]
            if method in self.errors:
                await self._send(writer, {"id": response_id, "error": self.errors[method]})
                continue
            if method == "events.subscribe":
                for frame in self.pre_ack:
                    await self._send(writer, frame)
                await self._send(
                    writer,
                    {"id": response_id, "result": {"type": self.subscription_ack_type}},
                )
                for frame in self.pushed:
                    await self._send(writer, frame)
                continue
            await self._send(writer, {"id": response_id, "result": self.responses[method]})
        writer.close()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, frame: Frame) -> None:
        writer.write((json.dumps(frame) + "\n").encode())
        await writer.drain()

    async def __aenter__(self) -> "FakeHerdr":
        self.server = await asyncio.start_unix_server(self._handle, str(self.path))
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


def adapter(tmp_path: Path) -> HerdrAdapter:
    return HerdrAdapter(
        HerdrConfig(binary=sys.executable, socket_path=str(tmp_path / "herdr.sock")),
        project_root=tmp_path,
    )


def test_project_config_is_loaded(tmp_path: Path) -> None:
    config_path = tmp_path / ".herdsman" / "herdr.json"
    config_path.parent.mkdir()
    _ = config_path.write_text(
        json.dumps(
            {
                "binary": sys.executable,
                "socket": "runtime/herdr.sock",
                "timeout": 2.5,
            }
        )
    )

    assert HerdrConfig.from_project(tmp_path) == HerdrConfig(
        binary=sys.executable,
        socket_path="runtime/herdr.sock",
        timeout=2.5,
    )


def test_missing_binary_is_rejected_before_connecting(tmp_path: Path) -> None:
    adapt = HerdrAdapter(
        HerdrConfig(binary="definitely-not-herdr", socket_path=str(tmp_path / "missing.sock")),
        project_root=tmp_path,
    )

    with pytest.raises(HerdrUnavailable, match="not available"):
        asyncio.run(adapt.check_ready())


def test_ping_accepts_newer_versions_when_response_shape_is_supported(
    tmp_path: Path,
) -> None:
    server = FakeHerdr(
        tmp_path / "newer.sock",
        responses={"ping": {"type": "pong", "version": "9.9.9", "protocol": 999}},
    )

    async def check() -> None:
        async with server:
            adapter = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            await adapter.check_ready()

    asyncio.run(check())


def test_ping_requires_pong_response_type(tmp_path: Path) -> None:
    server = FakeHerdr(
        tmp_path / "wrong-ping.sock",
        responses={"ping": {"type": "wrong", "version": "0.7.2", "protocol": 16}},
    )

    async def check() -> None:
        async with server:
            adapter = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            with pytest.raises(HerdrProtocolError, match="unexpected result type"):
                await adapter.check_ready()

    asyncio.run(check())


def test_response_id_and_result_type_are_checked(tmp_path: Path) -> None:
    async def wrong_id() -> None:
        server = FakeHerdr(tmp_path / "wrong-id.sock", wrong_response_id_for={"ping"})
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            with pytest.raises(HerdrProtocolError, match="request id"):
                await adapt.check_ready()

    async def wrong_type() -> None:
        server = FakeHerdr(
            tmp_path / "wrong-type.sock",
            responses={"worktree.create": {"type": "wrong"}},
        )
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            with pytest.raises(HerdrProtocolError, match="unexpected result type"):
                _ = await adapt.create_worktree("gate-0")

    _ = asyncio.run(wrong_id())
    _ = asyncio.run(wrong_type())


def test_subscription_ack_and_pre_ack_events_are_checked(tmp_path: Path) -> None:
    async def bad_ack() -> None:
        server = FakeHerdr(tmp_path / "bad-ack.sock", subscription_ack_type="wrong")
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            worktree = await adapt.create_worktree("gate-0")
            with pytest.raises(HerdrProtocolError, match="unexpected acknowledgement"):
                _ = await adapt.run(worktree, "echo hello")

    async def pre_ack() -> None:
        server = FakeHerdr(
            tmp_path / "pre-ack.sock",
            pre_ack=[
                {"event": "pane.output_matched", "data": {"pane_id": PANE, "text": "early"}}
            ],
        )
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, "echo hello")
            facts = [fact async for fact in adapt.observe(pane)]
            assert [fact.detail.get("text") for fact in facts] == ["early", "hello", None]

    asyncio.run(bad_ack())
    asyncio.run(pre_ack())


def test_run_subscribes_before_sending_a_fast_exit_command(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> list[RuntimeFact]:
        async with server:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, "printf 'fast exit\\n'; exit")
            return [fact async for fact in adapt.observe(pane)]

    facts = asyncio.run(scenario())
    assert [fact.kind for fact in facts] == ["pane_output_matched", "pane_exited"]
    assert server.methods.index("events.subscribe") < server.methods.index("pane.send_input")


def test_an_unrelated_worktree_removal_does_not_end_an_observation(
    tmp_path: Path,
) -> None:
    """N11: worktree events are machine-wide, so an unattributable one is noise."""
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={"pane.get": {"type": "pane_info", "pane": {"pane_id": PANE}}},
        pushed=[
            {"event": "worktree.removed", "data": {"workspace_id": "ws9"}},
            {"event": "pane.exited", "data": {"pane_id": PANE, "exit_code": 0}},
        ],
    )

    async def scenario() -> list[RuntimeFact]:
        async with server:
            # No create_worktree, so the pane's workspace is unknown to us.
            return [fact async for fact in adapter(tmp_path).observe(PANE)]

    assert [fact.kind for fact in asyncio.run(scenario())] == ["pane_exited"]


def test_runtime_fact_becomes_auditable_domain_event() -> None:
    fact = RuntimeFact("pane_exited", {"pane_id": "w1:p1", "workspace_id": "w1"})
    event = to_runtime_observed(
        "plan-1", "attempt-1", fact, at=datetime(2026, 8, 25, tzinfo=UTC)
    )

    assert event.kind == "pane_exited"
    assert event.at.tzinfo is not None
    assert event.detail == fact.detail


def test_a_mock_worker_run_streams_runtime_events_into_the_store(tmp_path: Path) -> None:
    """worktree -> mock worker -> observed events -> persisted and reloadable."""
    store = EventStore(tmp_path / "events.db")
    daemon = Daemon(store)
    _ = daemon.append(
        PlanCreated(plan_id="plan_1", at=datetime(2026, 8, 25, tzinfo=UTC), brief="gate 0")
    )
    herdr = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> list[RuntimeObserved]:
        async with herdr:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, "echo hello")
            observed = [
                event
                async for event in adapt.observe_events("plan_1", "attempt_1", pane)
            ]
            await adapt.remove_worktree(worktree)
            return observed

    try:
        observed = asyncio.run(scenario())
        for event in observed:
            _ = daemon.append(event)

        assert [event.kind for event in observed] == ["pane_output_matched", "pane_exited"]
        assert observed[0].detail["text"] == "hello"
        assert herdr.methods.count("ping") == 1  # readiness is checked once
        assert "worktree.remove" in herdr.methods
        # A worked-in worktree is dirty by definition, and herdr refuses an
        # unforced remove of one; discard is the explicit release action.
        remove = next(r for r in herdr.requests if r["method"] == "worktree.remove")
        assert cast(Frame, remove["params"])["force"] is True

        # The events survive a restart, reloaded from disk rather than memory.
        store.close()
        reopened = EventStore(tmp_path / "events.db")
        try:
            replayed = reopened.read("plan_1")
            assert [event.type for event in replayed] == [
                "plan_created",
                "runtime_observed",
                "runtime_observed",
            ]
            assert reopened.load("plan_1").brief == "gate 0"
        finally:
            reopened.close()
    finally:
        store.close()


def test_a_missing_pane_is_a_typed_resource_error(tmp_path: Path) -> None:
    herdr = FakeHerdr(
        tmp_path / "herdr.sock",
        errors={"pane.send_input": {"code": "not_found", "message": "no such pane"}},
    )

    async def scenario() -> None:
        async with herdr:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            _ = await adapt.run(worktree, "echo hello")

    with pytest.raises(HerdrResourceError):
        asyncio.run(scenario())


def test_nudge_sends_free_text_to_a_live_pane(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> None:
        async with server:
            await adapter(tmp_path).nudge_pane(PANE, "keep going")

    asyncio.run(scenario())
    nudged = next(r for r in server.requests if r["method"] == "pane.send_text")
    assert cast(Frame, nudged["params"]) == {"pane_id": PANE, "text": "keep going"}


def test_focus_targets_a_pane_reference(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> None:
        async with server:
            await adapter(tmp_path).focus_pane(PANE)

    asyncio.run(scenario())
    focused = next(r for r in server.requests if r["method"] == "pane.focus")
    assert cast(Frame, focused["params"]) == {"pane_id": PANE}


def worktree_entry(
    path: str, branch: str | None, workspace_id: str | None
) -> Frame:
    """A WorktreeInfo-shaped herdr entry (protocol 16 required fields)."""
    return {
        "path": path,
        "branch": branch,
        "open_workspace_id": workspace_id,
        "label": path.rsplit("/", 1)[-1],
        "is_bare": False,
        "is_detached": False,
        "is_prunable": False,
        "is_linked_worktree": workspace_id is not None,
    }


def pane_entry(pane_id: str, workspace_id: str) -> Frame:
    """A PaneInfo-shaped herdr entry (protocol 16 required fields)."""
    return {
        "pane_id": pane_id,
        "terminal_id": f"t-{pane_id}",
        "workspace_id": workspace_id,
        "tab_id": f"tab-{workspace_id}",
        "focused": False,
        "agent_status": "idle",
        "revision": 1,
    }


def test_inventory_lists_project_worktrees_and_owned_panes_only(
    tmp_path: Path,
) -> None:
    """Ownership is the herdsman/ branch prefix; unattributable panes stay out."""
    owned_open = worktree_entry(
        "/repo/.worktrees/attempt-1", "herdsman/plan_1/init_1/attempt_1", "ws-owned"
    )
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={
            "worktree.list": {
                "type": "worktree_list",
                "source": {
                    "repo_key": "k",
                    "repo_name": "repo",
                    "repo_root": "/repo",
                    "source_checkout_path": "/repo",
                },
                "worktrees": [
                    worktree_entry("/repo", "main", "ws-main"),
                    owned_open,
                    worktree_entry(
                        "/repo/.worktrees/attempt-2",
                        "herdsman/plan_1/init_2/attempt_2",
                        None,
                    ),
                    worktree_entry("/repo/.worktrees/user", "feature", "ws-user"),
                ],
            },
            "pane.list": {
                "type": "pane_list",
                "panes": [
                    pane_entry("ws-owned:p1", "ws-owned"),
                    pane_entry("ws-user:p2", "ws-user"),
                ],
            },
        },
    )

    async def scenario() -> tuple[RuntimeInventory, list[str]]:
        async with server:
            adapt = adapter(tmp_path)
            first = await adapt.inventory()
            second = await adapt.inventory()
            return first, [f"{w.path}:{w.herdsman_owned}" for w in second.worktrees]

    inventory, ownership = asyncio.run(scenario())
    assert ownership == [
        "/repo:False",
        "/repo/.worktrees/attempt-1:True",
        "/repo/.worktrees/attempt-2:True",
        "/repo/.worktrees/user:False",
    ]
    # Only panes of Herdsman-owned open workspaces are listed; the closed
    # owned worktree can have no panes and the user's panes are not ours.
    assert inventory.panes == (PaneEntry("ws-owned:p1", "ws-owned"),)
    assert inventory.worktrees[1].detail == owned_open
    listing = next(r for r in server.requests if r["method"] == "worktree.list")
    assert cast(Frame, listing["params"]) == {"cwd": str(tmp_path)}
    panes = next(r for r in server.requests if r["method"] == "pane.list")
    assert panes["params"] == {}


def test_inventory_skips_the_pane_request_without_owned_workspaces(
    tmp_path: Path,
) -> None:
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={
            "worktree.list": {
                "type": "worktree_list",
                "source": {
                    "repo_key": "k",
                    "repo_name": "repo",
                    "repo_root": "/repo",
                    "source_checkout_path": "/repo",
                },
                "worktrees": [worktree_entry("/repo", "main", "ws-main")],
            },
        },
    )

    async def scenario() -> RuntimeInventory:
        async with server:
            return await adapter(tmp_path).inventory()

    inventory = asyncio.run(scenario())
    assert inventory.panes == ()
    assert "pane.list" not in server.methods


def test_inventory_rejects_malformed_listings(tmp_path: Path) -> None:
    owned_open = worktree_entry(
        "/repo/.worktrees/attempt-1", "herdsman/plan_1/init_1/attempt_1", "ws-owned"
    )

    async def case(sock: str, responses: dict[str, Frame]) -> None:
        server = FakeHerdr(tmp_path / sock, responses=responses)
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            _ = await adapt.inventory()

    with pytest.raises(HerdrProtocolError, match="entry has no path"):
        asyncio.run(
            case(
                "wt.sock",
                {
                    "worktree.list": {
                        "type": "worktree_list",
                        "source": {},
                        "worktrees": [{"branch": "herdsman/p/i/a"}],
                    }
                },
            )
        )
    with pytest.raises(HerdrProtocolError, match="no worktrees"):
        asyncio.run(
            case("wts.sock", {"worktree.list": {"type": "worktree_list"}})
        )
    with pytest.raises(HerdrProtocolError, match="no pane_id or workspace_id"):
        asyncio.run(
            case(
                "pane.sock",
                {
                    "worktree.list": {
                        "type": "worktree_list",
                        "source": {},
                        "worktrees": [owned_open],
                    },
                    "pane.list": {
                        "type": "pane_list",
                        "panes": [{"pane_id": "p"}],
                    },
                },
            )
        )


def test_reconciliation_classifies_surviving_missing_orphaned() -> None:
    """Pure classification: sorted, deterministic, no sockets needed."""
    inventory = RuntimeInventory(
        worktrees=(
            WorktreeEntry("/repo", "main", "ws-main", False, {}),
            WorktreeEntry(
                "/repo/.worktrees/a1",
                "herdsman/plan_1/init_1/attempt_1",
                "ws-a1",
                True,
                {},
            ),
            WorktreeEntry(
                "/repo/.worktrees/a2", "herdsman/plan_1/init_2/attempt_2", None, True, {}
            ),
        ),
        panes=(PaneEntry("ws-a1:p1", "ws-a1"), PaneEntry("ws-a1:p2", "ws-a1")),
    )

    first = reconcile_inventory(
        inventory,
        worktree_refs=("/repo/.worktrees/a1", "ws-gone", "ws-a1"),
        pane_refs=("ws-a1:p1", "ws-a1:p9"),
    )
    assert first == Reconciliation(
        surviving_worktrees=("/repo/.worktrees/a1", "ws-a1"),
        missing_worktrees=("ws-gone",),
        orphaned_worktrees=("/repo/.worktrees/a2",),
        surviving_panes=("ws-a1:p1",),
        missing_panes=("ws-a1:p9",),
        orphaned_panes=("ws-a1:p2",),
    )
    # Deterministic: equal inputs, equal output, regardless of input order.
    assert reconcile_inventory(
        inventory,
        worktree_refs=("ws-a1", "ws-gone", "/repo/.worktrees/a1"),
        pane_refs=("ws-a1:p9", "ws-a1:p1"),
    ) == first
    # The operator's own worktree is never claimed as an orphan, and a
    # branchless worktree cannot be attributed either.  Surviving references
    # claim their worktrees against orphan status.
    claimed = reconcile_inventory(
        inventory,
        worktree_refs=("/repo", "/repo/.worktrees/a1"),
        pane_refs=("ws-a1:p1",),
    )
    assert claimed.orphaned_worktrees == ("/repo/.worktrees/a2",)
    assert claimed.orphaned_panes == ("ws-a1:p2",)


def test_reconnect_observation_arms_the_marker_waiter_itself(tmp_path: Path) -> None:
    """Resume on a surviving pane mid-command: re-arm the checkpoint wait.

    A fresh adapter has no parked waiter -- only `run` creates one -- so a
    reconnect observation that passes `match` must arm its own
    pane.wait_for_output or the completion marker would never be seen.
    """
    marker = f"{CHECKPOINT_MARKER} {{}}"
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={
            "pane.get": {
                "type": "pane_info",
                "pane": dict(pane_entry(PANE, "ws1")),
            },
            "pane.wait_for_output": {
                "type": "output_matched",
                "pane_id": PANE,
                "revision": 1,
                "matched_line": marker,
                "read": {"text": marker},
            },
        },
        pushed=[
            {"event": "pane.agent_status_changed", "data": {"pane_id": PANE}}
        ],
    )

    async def scenario() -> list[RuntimeObserved]:
        async with server:
            adapt = adapter(tmp_path)
            return [
                fact
                async for fact in adapt.observe_events(
                    "plan_1", "attempt_1", PANE, match=CHECKPOINT_PATTERN
                )
            ]

    facts = asyncio.run(scenario())
    assert [fact.kind for fact in facts] == ["pane_output_matched"]
    waited = next(r for r in server.requests if r["method"] == "pane.wait_for_output")
    params = cast(Frame, waited["params"])
    assert params["pane_id"] == PANE
    assert params["match"] == {"type": "regex", "value": CHECKPOINT_PATTERN}


def test_reconnect_observation_reuses_the_waiter_parked_by_run(tmp_path: Path) -> None:
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={
            "pane.wait_for_output": {
                "type": "output_matched",
                "pane_id": PANE,
                "revision": 1,
                "matched_line": "HERDSMAN_CHECKPOINT {}",
                "read": {"text": "HERDSMAN_CHECKPOINT {}"},
            }
        },
    )

    async def scenario() -> list[RuntimeFact]:
        async with server:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, "echo hello", match=CHECKPOINT_PATTERN)
            return [fact async for fact in adapt.observe(pane, match=CHECKPOINT_PATTERN)]

    facts = asyncio.run(scenario())
    assert [fact.kind for fact in facts] == ["pane_output_matched"]
    assert server.methods.count("pane.wait_for_output") == 1


def test_observation_without_match_arms_no_waiter(tmp_path: Path) -> None:
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        responses={
            "pane.get": {
                "type": "pane_info",
                "pane": dict(pane_entry(PANE, "ws1")),
            }
        },
        pushed=[{"event": "pane.exited", "data": {"pane_id": PANE, "exit_code": 0}}],
    )

    async def scenario() -> list[RuntimeFact]:
        async with server:
            return [fact async for fact in adapter(tmp_path).observe(PANE)]

    facts = asyncio.run(scenario())
    assert [fact.kind for fact in facts] == ["pane_exited"]
    assert "pane.wait_for_output" not in server.methods


def test_restart_interrupts_the_process_then_reissues_the_command(
    tmp_path: Path,
) -> None:
    """A restart interrupts the foreground process before the re-issue.

    The hung executor must be stopped before the cached command is re-sent,
    or the bytes would feed the hung process instead of a fresh prompt.
    """
    server = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> str:
        async with server:
            return await adapter(tmp_path).restart_process(PANE, "echo hello")

    assert asyncio.run(scenario()) == PANE
    assert server.methods == ["ping", "pane.send_keys", "pane.send_input"]
    interrupted = next(r for r in server.requests if r["method"] == "pane.send_keys")
    assert cast(Frame, interrupted["params"]) == {"pane_id": PANE, "keys": ["C-c"]}
    restarted = next(r for r in server.requests if r["method"] == "pane.send_input")
    assert cast(Frame, restarted["params"]) == {
        "pane_id": PANE,
        "text": "echo hello",
        "keys": ["Enter"],
    }


def test_intervention_primitives_reject_empty_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapt = adapter(tmp_path)
        with pytest.raises(ValueError, match="pane reference"):
            await adapt.nudge_pane("", "keep going")
        with pytest.raises(ValueError, match="nudge text"):
            await adapt.nudge_pane(PANE, "  ")
        with pytest.raises(ValueError, match="pane reference"):
            await adapt.focus_pane("")
        with pytest.raises(ValueError, match="pane reference"):
            _ = await adapt.restart_process("", "echo hello")
        with pytest.raises(ValueError, match="command"):
            _ = await adapt.restart_process(PANE, "  ")

    asyncio.run(scenario())


def test_focus_on_a_missing_pane_is_a_typed_resource_error(tmp_path: Path) -> None:
    server = FakeHerdr(
        tmp_path / "herdr.sock",
        errors={"pane.focus": {"code": "not_found", "message": "no such pane"}},
    )

    async def scenario() -> None:
        async with server:
            await adapter(tmp_path).focus_pane(PANE)

    with pytest.raises(HerdrResourceError):
        asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1",
    reason="set HERDSMAN_TEST_REAL_HERDR=1 to exercise the installed herdr daemon",
)
@pytest.mark.usefixtures("herdr_workspaces")
def test_real_herdr_worktree_run_observe_remove(tmp_path: Path) -> None:
    """Gate 0 integration check against an installed, disposable herdr project."""
    _ = (tmp_path / "README").write_text("gate 0\n")
    for args in (
        ("init",),
        ("config", "user.email", "gate0@example.invalid"),
        ("config", "user.name", "Gate 0"),
        ("add", "."),
        ("commit", "-m", "initial"),
    ):
        _ = subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    async def scenario() -> list[RuntimeFact]:
        adapt = HerdrAdapter(project_root=tmp_path)
        worktree: str | None = None
        try:
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, "printf 'gate-0\\n'; exit")
            return [fact async for fact in adapt.observe(pane)]
        finally:
            if worktree is not None:
                await adapt.remove_worktree(worktree)

    facts = asyncio.run(scenario())
    assert any(fact.kind == "pane_exited" for fact in facts)


@pytest.mark.skipif(
    os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1",
    reason="set HERDSMAN_TEST_REAL_HERDR=1 to exercise the installed herdr daemon",
)
@pytest.mark.usefixtures("herdr_workspaces")
def test_real_herdr_recovers_the_checkpoint_marker(tmp_path: Path) -> None:
    """The marker path, end to end, against the installed herdr.

    Fake runtimes yield the marker directly and cannot see how herdr delivers
    it.  Three fake-based passes went green while the live path recovered
    nothing, so this assertion has to run against the real daemon.
    """
    _ = (tmp_path / "README").write_text("marker\n")
    for args in (
        ("init",),
        ("config", "user.email", "marker@example.invalid"),
        ("config", "user.name", "Marker"),
        ("add", "."),
        ("commit", "-m", "initial"),
    ):
        _ = subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    payload = (
        '{"exit_code":0,"usage":'
        '{"input_tokens":11,"output_tokens":7,"source":"harness"}}'
    )

    async def scenario() -> Completion | None:
        adapt = HerdrAdapter(project_root=tmp_path)
        worktree: str | None = None
        try:
            worktree = await adapt.create_worktree("marker")
            # Mirrors executor_command: the marker literal also appears inside
            # the echoed command line, and the pane exits when the work ends.
            pane = await adapt.run(
                worktree,
                f"printf '{CHECKPOINT_MARKER} %s\\n' {shlex.quote(payload)}",
                match=CHECKPOINT_PATTERN,
            )
            found: Completion | None = None
            async for fact in adapt.observe(pane):
                evidence = completion_from_detail(fact.detail)
                if evidence is not None:
                    found = evidence
            return found
        finally:
            if worktree is not None:
                await adapt.remove_worktree(worktree)

    completion = asyncio.run(scenario())
    assert completion is not None
    assert completion.exit_code == 0
    assert completion.usage.input_tokens == 11
    assert completion.usage.source == "harness"
