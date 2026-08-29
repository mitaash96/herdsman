from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from herdsman import cli
from herdsman.classes import PlanCreated
from herdsman.store import EventStore


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
