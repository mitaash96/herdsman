"""The adapter against a fake herdr socket server.

Gate 0 asks for a worktree, a mock worker, and received runtime events. The
fake server speaks herdr's line-delimited JSON protocol, so this exercises the
real framing, request ids, subscription handshake, and event filtering.
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from herdsman.classes import PlanCreated, RuntimeObserved
from herdsman.herdr import (
    AgentLaunch,
    HerdrAdapter,
    HerdrConfig,
    HerdrOperationError,
    HerdrProtocolError,
    HerdrResourceError,
    HerdrUnavailable,
    PINNED_HERDR_PROTOCOL,
    PINNED_HERDR_VERSION,
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

AGENT: dict[str, object] = {
    "pane_id": PANE, "agent": "claude", "agent_status": "idle",
    "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": "sess-1"},
}
LAUNCH = AgentLaunch(name="hs-0123456789ab", kind="claude",
                    args=("--dangerously-skip-permissions",), prompt="do it")

Frame = dict[str, object]

RESPONSES: dict[str, Frame] = {
    "ping": {
        "type": "pong",
        "version": PINNED_HERDR_VERSION,
        "protocol": PINNED_HERDR_PROTOCOL,
    },
    "worktree.create": {
        "type": "worktree_created",
        "workspace": {"workspace_id": "ws1"},
        "worktree": {"worktree_id": "wt1", "path": "/repo/.worktrees/gate-0"},
        "root_pane": {"pane_id": PANE},
    },
    "agent.start": {"type": "agent_started", "agent": AGENT, "argv": ["claude"]},
    "agent.get": {"type": "agent_info", "agent": AGENT},
    "agent.wait": {"type": "agent_info", "agent": AGENT},
    "agent.prompt": {"type": "agent_prompted", "agent": AGENT},
    "agent.send_keys": {"type": "ok"},
    "workspace.create": {"type": "workspace_created", "root_pane": {"pane_id": PANE}},
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


def test_pin_drift_warns_once_and_never_blocks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = FakeHerdr(
        tmp_path / "drift.sock",
        responses={"ping": {"type": "pong", "version": "9.9.9", "protocol": 999}},
    )

    async def check() -> None:
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            await adapt.check_ready()
            await adapt.check_ready(force=True)
            await adapt.check_ready(force=True)

    asyncio.run(check())
    warnings = [record.message for record in caplog.records if "differs from the pinned" in record.message]
    assert len(warnings) == 1
    assert "9.9.9" in warnings[0] and "protocol 999" in warnings[0]


def test_socket_disappearance_is_retryable_and_reconnect_repings(tmp_path: Path) -> None:
    path = tmp_path / "restart.sock"
    first = FakeHerdr(path)
    adapt = HerdrAdapter(
        HerdrConfig(binary=sys.executable, socket_path=str(path), timeout=0.2),
        project_root=tmp_path,
    )

    async def scenario() -> list[str]:
        async with first:
            await adapt.check_ready()
        path.unlink(missing_ok=True)
        with pytest.raises(HerdrUnavailable, match="cannot connect to herdr socket"):
            _ = await adapt.inventory()
        second = FakeHerdr(path)
        async with second:
            _ = await adapt.inventory()
        return second.methods

    assert asyncio.run(scenario())[:2] == ["ping", "worktree.list"]


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
                _ = await adapt.run(worktree, LAUNCH)

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
            pane = await adapt.run(worktree, LAUNCH)
            facts = [fact async for fact in adapt.observe(pane)]
            assert [fact.kind for fact in facts] == ["agent_settled"]

    asyncio.run(bad_ack())
    asyncio.run(pre_ack())


def test_run_starts_an_agent_and_settles_on_the_prompt_wait(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[])

    async def scenario() -> list[RuntimeFact]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact async for fact in adapt.observe(pane)]

    facts = asyncio.run(scenario())
    assert [fact.kind for fact in facts] == ["agent_settled"]
    assert cast(Frame, facts[0].detail["agent_session"])["value"] == "sess-1"
    assert server.methods.index("events.subscribe") < server.methods.index("agent.start") < server.methods.index("agent.prompt")
    start = next(r for r in server.requests if r["method"] == "agent.start")
    assert start["params"] == {"name": LAUNCH.name, "kind": "claude", "pane_id": PANE,
                               "args": list(LAUNCH.args), "timeout_ms": 120000}
    prompt = next(r for r in server.requests if r["method"] == "agent.prompt")
    assert prompt["params"] == {"target": PANE, "text": "do it", "wait": {}}
    assert "pane.send_input" not in server.methods


def test_start_waits_for_interactive_readiness_not_just_idle(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[], responses={
        "agent.start": {"type": "agent_started", "agent": {**AGENT, "launch_pending": True}},
    })

    async def scenario() -> None:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            _ = [fact async for fact in adapt.observe(pane)]
    asyncio.run(scenario())
    assert server.methods.index("agent.get") < server.methods.index("agent.prompt")


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


def test_runtime_payload_redacts_real_env_argv_and_output_shapes(tmp_path: Path) -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    server = FakeHerdr(
        tmp_path / "redact.sock",
        responses={"pane.get": {"type": "pane_info", "pane": {"pane_id": PANE}}},
        pushed=[
            {
                "event": "pane.output_matched",
                "data": {
                    "pane_id": PANE,
                    "text": f"export OPENAI_API_KEY={secret}",
                    "env": {"DATABASE_PASSWORD": "database-password"},
                    "argv": ["pi", "--token", "plain-command-secret"],
                },
            },
            {"event": "pane.exited", "data": {"pane_id": PANE, "exit_code": 0}},
        ],
    )

    async def scenario() -> RuntimeFact:
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            pane = PANE
            return [fact async for fact in adapt.observe(pane)][0]

    detail = asyncio.run(scenario()).detail
    assert secret not in json.dumps(detail)
    assert detail["text"] == "export OPENAI_API_KEY=[redacted]"
    assert detail["env"] == {"DATABASE_PASSWORD": "[redacted]"}
    assert detail["argv"] == ["pi", "--token", "[redacted]"]


def test_herdr_error_does_not_echo_a_secret(tmp_path: Path) -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    server = FakeHerdr(
        tmp_path / "error.sock",
        errors={"pane.focus": {"code": "denied", "message": f"token={secret}"}},
    )

    async def scenario() -> None:
        async with server:
            adapt = HerdrAdapter(
                HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
                project_root=tmp_path,
            )
            await adapt.focus_pane(PANE)

    with pytest.raises(HerdrOperationError) as caught:
        asyncio.run(scenario())
    assert secret not in str(caught.value)
    assert "[redacted]" in str(caught.value)


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
    _ = store.append(
        PlanCreated(plan_id="plan_1", at=datetime(2026, 8, 25, tzinfo=UTC), brief="gate 0")
    )
    herdr = FakeHerdr(tmp_path / "herdr.sock")

    async def scenario() -> list[RuntimeObserved]:
        async with herdr:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            pane = await adapt.run(worktree, LAUNCH)
            observed = [
                event
                async for event in adapt.observe_events("plan_1", "attempt_1", pane)
            ]
            await adapt.remove_worktree(worktree)
            return observed

    try:
        observed = asyncio.run(scenario())
        for event in observed:
            _ = store.append(event)

        assert [event.kind for event in observed] == ["agent_settled"]
        assert observed[0].detail["agent_status"] == "idle"
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
            ]
            assert reopened.load("plan_1").brief == "gate 0"
        finally:
            reopened.close()
    finally:
        store.close()


def test_focus_sends_the_pane_id_and_checks_the_result_type(tmp_path: Path) -> None:
    """Focus is one request with no worktree, no subscription and no fallback."""
    herdr = FakeHerdr(
        tmp_path / "herdr.sock", responses={"pane.focus": {"type": "pane_focused"}}
    )

    async def scenario() -> None:
        async with herdr:
            adapt = adapter(tmp_path)
            await adapt.focus_pane(PANE)

    asyncio.run(scenario())
    assert herdr.methods == ["ping", "pane.focus"]
    assert herdr.requests[-1]["params"] == {"pane_id": PANE}


def test_focus_rejects_an_empty_pane_and_an_unexpected_result(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pane reference cannot be empty"):
        asyncio.run(adapter(tmp_path).focus_pane(""))

    herdr = FakeHerdr(
        tmp_path / "herdr.sock", responses={"pane.focus": {"type": "pane_zoom"}}
    )

    async def scenario() -> None:
        async with herdr:
            await adapter(tmp_path).focus_pane(PANE)

    with pytest.raises(HerdrProtocolError):
        asyncio.run(scenario())


def test_a_missing_pane_is_a_typed_resource_error(tmp_path: Path) -> None:
    herdr = FakeHerdr(
        tmp_path / "herdr.sock",
        errors={"agent.start": {"code": "not_found", "message": "no such pane"}},
    )

    async def scenario() -> None:
        async with herdr:
            adapt = adapter(tmp_path)
            worktree = await adapt.create_worktree("gate-0")
            _ = await adapt.run(worktree, LAUNCH)

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


def test_rearm_waits_without_prompting(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[], responses={
        "pane.get": {"type": "pane_info", "pane": dict(pane_entry(PANE, "ws1"))},
    })

    async def scenario() -> list[RuntimeObserved]:
        async with server:
            return [event async for event in adapter(tmp_path).observe_events(
                "plan_1", "attempt_1", PANE, rearm=True)]

    assert [f.kind for f in asyncio.run(scenario())] == ["agent_settled"]
    assert "agent.prompt" not in server.methods
    assert server.requests[-1]["params"] == {"target": PANE, "until": ["idle", "done"]}


def test_rearm_reuses_the_waiter_parked_by_run(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[])

    async def scenario() -> list[RuntimeFact]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact async for fact in adapt.observe(pane, rearm=True)]

    assert [f.kind for f in asyncio.run(scenario())] == ["agent_settled"]
    assert server.methods.count("agent.wait") == 1


def test_a_blocked_settle_waits_for_idle_and_reports_the_block(tmp_path: Path) -> None:
    blocked = {**AGENT, "agent_status": "blocked"}
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[], responses={
        "agent.prompt": {"type": "agent_prompted", "agent": blocked},
    })
    async def scenario() -> list[str]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact.kind async for fact in adapt.observe(pane)]
    assert asyncio.run(scenario()) == ["agent_blocked", "agent_settled"]
    waits = [r["params"] for r in server.requests if r["method"] == "agent.wait"]
    assert waits[-1] == {"target": PANE, "until": ["idle", "done"]}


def test_startup_not_ready_keeps_waiting_for_the_operator(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[],
        errors={"agent.start": {"code": "agent_not_ready", "message": "trust dialog"}})
    async def scenario() -> list[str]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact.kind async for fact in adapt.observe(pane)]
    assert asyncio.run(scenario()) == ["agent_settled"]


def test_pending_startup_dialog_does_not_time_out_operator_input(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[], responses={
        "agent.start": {"type": "agent_started", "agent": {**AGENT, "launch_pending": True, "agent_status": "blocked"}},
        "agent.wait": {"type": "agent_info", "agent": {**AGENT, "launch_pending": True}},
    })
    async def scenario() -> list[str]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact.kind async for fact in adapt.observe(pane)]
    assert asyncio.run(scenario()) == ["agent_settled"]
    assert server.methods.index("agent.wait") < server.methods.index("agent.get") < server.methods.index("agent.prompt")


def test_an_agent_gone_before_settling_yields_no_settle(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock",
        errors={"agent.prompt": {"code": "agent_not_found", "message": "gone"}},
        pushed=[{"event": "pane.exited", "data": {"pane_id": PANE}}])
    async def scenario() -> list[str]:
        async with server:
            adapt = adapter(tmp_path)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            return [fact.kind async for fact in adapt.observe(pane)]
    assert asyncio.run(scenario()) == ["pane_exited"]


def test_operation_errors_keep_herdrs_code() -> None:
    with pytest.raises(HerdrOperationError) as caught:
        HerdrAdapter._raise_api_error("agent.prompt", {"code": "agent_prompt_stalled", "message": "x"})  # pyright: ignore[reportPrivateUsage]
    assert caught.value.code == "agent_prompt_stalled"


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


def test_restart_interrupts_then_reprompts_until_working(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")
    async def scenario() -> str:
        async with server:
            return await adapter(tmp_path).restart_agent(PANE, "do it")
    assert asyncio.run(scenario()) == PANE
    assert server.methods == ["ping", "agent.send_keys", "agent.prompt"]
    assert server.requests[-2]["params"] == {"target": PANE, "keys": ["esc"]}
    assert server.requests[-1]["params"] == {
        "target": PANE, "text": "do it",
        "wait": {"until": ["working", "blocked"], "timeout_ms": 30000},
    }


def test_interrupt_sends_esc_to_the_agent(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")
    async def scenario() -> None:
        async with server:
            await adapter(tmp_path).interrupt_pane(PANE)
    asyncio.run(scenario())
    assert server.requests[-1]["params"] == {"target": PANE, "keys": ["esc"]}


def test_visible_agent_returns_final_info_after_a_block(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", responses={
        "agent.prompt": {"type": "agent_prompted", "agent": {**AGENT, "agent_status": "blocked"}},
    })
    panes: list[str] = []
    async def scenario() -> Frame:
        async with server:
            return await adapter(tmp_path).run_agent_visible(
                LAUNCH, label="planner", timeout=2, on_pane=panes.append)
    assert asyncio.run(scenario()) == AGENT
    assert panes == [PANE]
    assert server.methods == ["ping", "workspace.create", "agent.start", "agent.wait", "agent.prompt", "agent.wait"]
    assert "agent.send_keys" not in server.methods


@pytest.mark.parametrize("cancel", [False, True])
def test_visible_agent_interrupts_on_timeout_or_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock")
    async def scenario() -> None:
        async with server:
            adapt = adapter(tmp_path)
            prompting = asyncio.Event()
            async def blocked_prompt(_pane: str, _prompt: str) -> Frame:
                prompting.set()
                _ = await asyncio.Event().wait()
                return {}
            monkeypatch.setattr(adapt, "_prompt_agent", blocked_prompt)
            task = asyncio.create_task(adapt.run_agent_visible(
                LAUNCH, label="planner", timeout=10 if cancel else 0.05))
            _ = await prompting.wait()
            if cancel:
                _ = task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                _ = await task
    asyncio.run(scenario())
    assert server.requests[-1]["method"] == "agent.send_keys"
    assert server.requests[-1]["params"] == {"target": PANE, "keys": ["esc"]}


def test_visible_agent_does_not_accept_unknown_as_settled(tmp_path: Path) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", responses={
        "agent.prompt": {"type": "agent_prompted", "agent": {**AGENT, "agent_status": "unknown"}},
    })
    async def scenario() -> None:
        async with server:
            with pytest.raises(HerdrProtocolError, match="not idle or done"):
                _ = await adapter(tmp_path).run_agent_visible(LAUNCH, label="planner", timeout=2)
    asyncio.run(scenario())
    assert server.requests[-1]["method"] == "agent.send_keys"


def test_cancelled_observation_cancels_its_prompt_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeHerdr(tmp_path / "herdr.sock", pushed=[])
    async def scenario() -> None:
        async with server:
            adapt = adapter(tmp_path)
            prompting = asyncio.Event()
            async def blocked_prompt(_pane: str, _prompt: str) -> Frame:
                prompting.set()
                _ = await asyncio.Event().wait()
                return {}
            monkeypatch.setattr(adapt, "_prompt_agent", blocked_prompt)
            pane = await adapt.run(await adapt.create_worktree("gate-0"), LAUNCH)
            waiter = adapt._waiters[pane]  # pyright: ignore[reportPrivateUsage]
            async def observe() -> None:
                _ = [fact async for fact in adapt.observe(pane)]
            task = asyncio.create_task(observe())
            _ = await prompting.wait()
            _ = task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert waiter.cancelled()
            await adapt.aclose()
    asyncio.run(scenario())


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
            _ = await adapt.restart_agent("", "echo hello")
        with pytest.raises(ValueError, match="prompt"):
            _ = await adapt.restart_agent(PANE, "  ")

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
    reason="needs installed herdr and pi integration",
)
def test_real_herdr_agent_settles_and_reports_its_session(tmp_path: Path) -> None:
    for args in (
        ("init", "-q"), ("config", "user.email", "live@example.invalid"),
        ("config", "user.name", "Live"), ("commit", "--allow-empty", "-qm", "initial"),
    ):
        _ = subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    launch = AgentLaunch(name="hs-livetest0001", kind="pi", args=(),
                         prompt="Reply with the single word ok and do nothing else.")

    async def scenario() -> list[RuntimeFact]:
        adapt = HerdrAdapter(project_root=tmp_path)
        worktree: str | None = None
        try:
            worktree = await adapt.create_worktree("agent-live")
            pane = await adapt.run(worktree, launch)
            facts = [fact async for fact in adapt.observe(pane)]
            recovered = HerdrAdapter(project_root=tmp_path)
            try:
                rearmed = [fact async for fact in recovered.observe(pane, rearm=True)]
                assert [fact.kind for fact in rearmed] == ["agent_settled"]
            finally:
                await recovered.aclose()
            return facts
        finally:
            await adapt.aclose()
            if worktree is not None:
                await adapt.remove_worktree(worktree)
            # worktree.create also opens the source project workspace. Only
            # close this exact temporary project's workspace, never a snapshot diff.
            result = await adapt._request("workspace.list", {})  # pyright: ignore[reportPrivateUsage]
            for workspace in cast(list[Frame], result["workspaces"]):
                worktree_info = workspace.get("worktree")
                if isinstance(worktree_info, dict) and cast(Frame, worktree_info).get("checkout_path") == str(tmp_path):
                    _ = await adapt._request("workspace.close", {"workspace_id": workspace["workspace_id"]})  # pyright: ignore[reportPrivateUsage]

    facts = asyncio.run(asyncio.wait_for(scenario(), 300))
    assert [fact.kind for fact in facts] == ["agent_settled"]
    assert facts[0].detail["agent_status"] in {"idle", "done"}
    session = facts[0].detail["agent_session"]
    assert isinstance(session, dict) and session["value"]
