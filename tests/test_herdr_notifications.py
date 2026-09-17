"""notify_user against the fake herdr socket: delivered, non-delivery, and error shapes.

herdr 0.9.1 (protocol 22) `notification.show` takes a required `title` plus
optional `body`/`position`/`sound`, and answers `{type: "notification_show",
shown: bool, reason}` where reason is one of `shown`, `disabled`,
`rate_limited`, `no_foreground_client`, `busy`.  Non-delivery reasons are
transport outcomes, not domain failures: the adapter reports them as a False
return, never an exception.
"""

import asyncio
import sys
from pathlib import Path
from typing import cast

import pytest

from herdsman.herdr import HerdrAdapter, HerdrConfig, HerdrOperationError, HerdrProtocolError
from tests.test_herdr import FakeHerdr

Frame = dict[str, object]

PANE = "ws1:p1"

SHOW: Frame = {"type": "notification_show", "shown": True, "reason": "shown"}
UNDELIVERED: dict[str, Frame] = {
    reason: {"type": "notification_show", "shown": False, "reason": reason}
    for reason in ("disabled", "rate_limited", "no_foreground_client", "busy")
}


def notify_response(tmp_path: Path, response: Frame) -> tuple[HerdrAdapter, FakeHerdr]:
    """An adapter wired to a fake server answering `response` for notification.show."""
    server = FakeHerdr(tmp_path / "notify.sock", responses={"notification.show": response})
    adapt = HerdrAdapter(
        HerdrConfig(binary=sys.executable, socket_path=str(server.path)),
        project_root=tmp_path,
    )
    return adapt, server


def test_notification_shown_returns_true(tmp_path: Path) -> None:
    adapt, server = notify_response(tmp_path, SHOW)

    async def run() -> bool:
        async with server:
            return await adapt.notify_user("Initiative failed: luna-1")

    assert asyncio.run(run()) is True
    # The message rides in the protocol's required `title` parameter.
    request = next(
        request for request in server.requests if request["method"] == "notification.show"
    )
    assert request["method"] == "notification.show"
    assert cast(Frame, request["params"]) == {"title": "Initiative failed: luna-1"}


def test_non_delivery_reasons_return_false_without_raising(tmp_path: Path) -> None:
    for reason, response in UNDELIVERED.items():
        reason_dir = tmp_path / reason
        reason_dir.mkdir()
        adapt, server = notify_response(reason_dir, response)

        async def run() -> bool:
            async with server:
                return await adapt.notify_user("checkpoint ready for review")

        assert asyncio.run(run()) is False, reason


def test_malformed_response_type_is_rejected(tmp_path: Path) -> None:
    adapt, server = notify_response(
        tmp_path, {"type": "unexpected", "shown": True, "reason": "shown"}
    )

    async def run() -> None:
        async with server:
            _ = await adapt.notify_user("hello")

    with pytest.raises(HerdrProtocolError, match="unexpected result type"):
        asyncio.run(run())


def test_missing_shown_or_reason_is_rejected(tmp_path: Path) -> None:
    bad_frames: tuple[Frame, ...] = (
        {"type": "notification_show", "reason": "shown"},
        {"type": "notification_show", "shown": "yes", "reason": "shown"},
        {"type": "notification_show", "shown": True},
        {"type": "notification_show", "shown": True, "reason": "later"},
    )
    for bad in bad_frames:
        adapt, server = notify_response(tmp_path, bad)

        async def run() -> None:
            async with server:
                _ = await adapt.notify_user("hello")

        with pytest.raises(HerdrProtocolError, match="lacks shown or reason"):
            asyncio.run(run())


def test_rpc_error_raises_herdr_operation_error(tmp_path: Path) -> None:
    adapt, server = notify_response(tmp_path, SHOW)
    server.errors = {"notification.show": {"code": "rejected", "message": "no client"}}

    async def run() -> None:
        async with server:
            _ = await adapt.notify_user("hello")

    with pytest.raises(HerdrOperationError, match="notification.show failed"):
        asyncio.run(run())


def test_empty_message_is_rejected_without_a_request(tmp_path: Path) -> None:
    adapt, _server = notify_response(tmp_path, SHOW)

    async def run() -> None:
        _ = await adapt.notify_user("   ")

    with pytest.raises(ValueError, match="cannot be empty"):
        asyncio.run(run())
