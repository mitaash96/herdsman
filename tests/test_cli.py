from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from herdsman import cli
from herdsman.classes import PlanCreated
from herdsman.store import EventStore
from tests.test_classes import stream


def test_review_and_approve_commands_use_the_event_stream(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:2]:
            _ = store.append(event)
    finally:
        store.close()

    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    reviewed = CliRunner().invoke(cli.app, ["review", "plan_1"])
    assert reviewed.exit_code == 0
    assert '"approval":"pending"' in reviewed.output

    approved = CliRunner().invoke(cli.app, ["approve", "plan_1"])
    assert approved.exit_code == 0
    assert '"approval":"approved"' in approved.output

    invalid = CliRunner().invoke(cli.app, ["approve", "plan_1"])
    assert invalid.exit_code != 0
    assert "already approved" in invalid.output


def test_events_command_prints_ndjson(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        _ = store.append(
            PlanCreated(plan_id="plan_1", at=datetime(2026, 8, 25, tzinfo=UTC), brief="x")
        )
    finally:
        store.close()

    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    result = CliRunner().invoke(cli.app, ["events", "plan_1"])

    assert result.exit_code == 0
    assert '"type":"plan_created"' in result.output
