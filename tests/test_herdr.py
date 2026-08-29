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
from herdsman.daemon import Daemon
from herdsman.herdr import (
    HerdrAdapter,
    HerdrConfig,
    HerdrIncompatible,
    HerdrProtocolError,
    HerdrResourceError,
    HerdrUnavailable,
    RuntimeFact,
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
    "worktree.remove": {"type": "worktree_removed"},
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
    ) -> None:
        self.path: Path = path
        self.errors: Frame = errors or {}
        self.responses: dict[str, Frame] = RESPONSES | (responses or {})
        self.pre_ack: list[Frame] = pre_ack or []
        self.subscription_ack_type: str = subscription_ack_type
        self.wrong_response_id_for: set[str] = wrong_response_id_for or set()
        self.methods: list[str] = []
        self.server: asyncio.Server | None = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while line := await reader.readline():
            request = cast(Frame, json.loads(line))
            method = str(request["method"])
            self.methods.append(method)
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
                for frame in PUSHED:
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
                "version": {"min": "0.7.1", "max": "0.7.3"},
                "protocol": {"min": 15, "max": 17},
                "timeout": 2.5,
            }
        )
    )

    assert HerdrConfig.from_project(tmp_path) == HerdrConfig(
        binary=sys.executable,
        socket_path="runtime/herdr.sock",
        min_version="0.7.1",
        max_version="0.7.3",
        min_protocol=15,
        max_protocol=17,
        timeout=2.5,
    )


def test_missing_binary_is_rejected_before_connecting(tmp_path: Path) -> None:
    adapt = HerdrAdapter(
        HerdrConfig(binary="definitely-not-herdr", socket_path=str(tmp_path / "missing.sock")),
        project_root=tmp_path,
    )

    with pytest.raises(HerdrUnavailable, match="not available"):
        asyncio.run(adapt.check_ready())


def test_ping_enforces_all_compatibility_bounds(tmp_path: Path) -> None:
    cases = [
        ("0.7.1", 16, HerdrConfig(min_version="0.7.2", max_version=None)),
        ("0.7.3", 16, HerdrConfig(min_version=None, max_version="0.7.2")),
        ("0.7.2", 15, HerdrConfig(min_protocol=16, max_protocol=None)),
        ("0.7.2", 17, HerdrConfig(min_protocol=None, max_protocol=16)),
    ]

    for version, protocol, config in cases:
        config = HerdrConfig(
            binary=sys.executable,
            socket_path=str(tmp_path / f"{version}-{protocol}.sock"),
            min_version=config.min_version,
            max_version=config.max_version,
            min_protocol=config.min_protocol,
            max_protocol=config.max_protocol,
        )
        server = FakeHerdr(
            Path(config.socket_path),
            responses={"ping": {"type": "pong", "version": version, "protocol": protocol}},
        )

        async def check() -> None:
            async with server:
                with pytest.raises(HerdrIncompatible):
                    await HerdrAdapter(config, project_root=tmp_path).check_ready()

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
            pane = await adapt.run(worktree, "echo hello")
            with pytest.raises(HerdrProtocolError, match="unexpected acknowledgement"):
                _ = [fact async for fact in adapt.observe(pane)]

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


@pytest.mark.skipif(
    os.environ.get("HERDSMAN_TEST_REAL_HERDR") != "1",
    reason="set HERDSMAN_TEST_REAL_HERDR=1 to exercise the installed herdr daemon",
)
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
            pane = await adapt.run(worktree, "printf 'gate-0\\n'; sleep 1; exit")
            return [fact async for fact in adapt.observe(pane)]
        finally:
            if worktree is not None:
                await adapt.remove_worktree(worktree)

    facts = asyncio.run(scenario())
    assert any(fact.kind == "pane_exited" for fact in facts)
