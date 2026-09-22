import json
import socket
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from pytest import MonkeyPatch, mark
from typer.testing import CliRunner

from herdsman import cli
from herdsman.lifecycle import read_record, write_record
from herdsman.store import project_lock

pytestmark = mark.usefixtures("isolated_cli_cwd")


def test_record_staleness_is_reaped(tmp_path: Path) -> None:
    path = tmp_path / "daemon.json"
    _ = path.write_text(
        json.dumps({"pid": 2_000_000_000, "host": "127.0.0.1", "port": 8000, "started_at": "now"})
    )

    assert read_record(path) is None
    assert not path.exists()


def test_up_twice_uses_the_live_record(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(cli, "_herdr_warning", lambda: None)
    monkeypatch.setattr(cli, "ui_bundle", lambda: None)

    def free(host: str, port: int) -> bool:
        del host, port
        return True

    monkeypatch.setattr(cli, "port_free", free)

    def serve(host: str, port: int) -> None:
        calls.append((host, port))
        _ = write_record(host, port, "now")

    monkeypatch.setattr(cli, "serve", serve)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["up"])
    second = runner.invoke(cli.app, ["up"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert calls == [("127.0.0.1", 8000)]
    assert "already running" in second.output


def test_serve_records_the_bound_ephemeral_port(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[int] = []
    monkeypatch.setattr(cli, "ui_bundle", lambda: None)

    def run_server(app_instance: FastAPI, listener: socket.socket) -> None:
        del app_instance
        record = read_record()
        assert record is not None
        assert record.port == listener.getsockname()[1]
        assert record.port > 0
        seen.append(record.port)

    monkeypatch.setattr(cli, "_run_server", run_server)
    result = CliRunner().invoke(cli.app, ["serve", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert seen
    assert read_record() is None


def test_serve_refuses_a_second_daemon_lock(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with project_lock():
        result = CliRunner().invoke(cli.app, ["serve"])

    assert result.exit_code != 0
    assert "another process" in result.output


def test_down_without_a_record_is_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["down"])

    assert result.exit_code == 0
    assert "already down" in result.output


def test_open_headless_prints_the_recorded_url(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _ = write_record("127.0.0.2", 8123, "now")

    result = CliRunner().invoke(cli.app, ["open"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "http://127.0.0.2:8123/"


def test_restart_dispatches_by_arity(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_down() -> None:
        calls.append(("down", None))

    def fake_up(host: str, port: int) -> None:
        calls.append(("up", (host, port)))

    def mutate(
        initiative_id: str,
        action: str,
        payload: dict[str, object] | None,
        plan_id: str | None,
        host: str,
        port: int,
    ) -> None:
        calls.append(("task", (initiative_id, action, payload, plan_id, host, port)))

    monkeypatch.setattr(cli, "down", fake_down)
    monkeypatch.setattr(cli, "up", fake_up)
    monkeypatch.setattr(cli, "_mutate_initiative", mutate)
    runner = CliRunner()

    daemon = runner.invoke(cli.app, ["restart", "--port", "8123"])
    task = runner.invoke(cli.app, ["restart", "init_a", "--plan-id", "plan_1"])

    assert daemon.exit_code == 0, daemon.output
    assert task.exit_code == 0, task.output
    assert calls == [
        ("down", None),
        ("up", ("127.0.0.1", 8123)),
        ("task", ("init_a", "restart", {"by": "operator"}, "plan_1", "127.0.0.1", 8000)),
    ]


def test_doctor_reports_failures_and_fix_stays_in_project(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    _ = outside.write_text("keep", encoding="utf-8")
    monkeypatch.chdir(project)
    runner = CliRunner()

    def free(host: str, port: int) -> bool:
        # The port probe reads this machine, not the fixture: a daemon already
        # listening on the default port must not decide the test's outcome.
        del host, port
        return True

    monkeypatch.setattr(cli, "port_free", free)

    broken = runner.invoke(cli.app, ["doctor"])
    assert broken.exit_code == 1
    report = cast(dict[str, object], json.loads(broken.output))
    assert report["ok"] is False
    assert any(
        item["status"] == "fail"
        for item in cast(list[dict[str, str]], report["checks"])
    )

    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    _ = (project / ".herdsman" / "daemon.json").write_text(
        json.dumps({"pid": 2_000_000_000, "host": "127.0.0.1", "port": 8000, "started_at": "old"})
    )
    _ = (project / ".herdsman" / "project.lock").write_text("2000000000\n")
    fixed = runner.invoke(cli.app, ["doctor", "--fix"])

    assert fixed.exit_code == 0, fixed.output
    payload = cast(dict[str, object], json.loads(fixed.output))
    fixes = cast(list[str], payload["fixed"])
    assert "removed stale daemon record" in fixes
    assert "removed stale project lock" in fixes
    assert outside.read_text(encoding="utf-8") == "keep"


def test_prune_previews_without_deleting(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path / ".herdsman" / "replay" / "old.json"
    scratch.parent.mkdir(parents=True)
    _ = scratch.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["prune"])

    assert result.exit_code == 0, result.output
    payload = cast(dict[str, object], json.loads(result.output))
    assert payload["applied"] is False
    assert scratch.exists()
    assert cast(list[dict[str, str]], payload["items"])[0]["reason"] == "old replay scratch"


def test_demo_dry_run_needs_no_server(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["demo", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = cast(dict[str, object], json.loads(result.output))
    initiatives = cast(list[dict[str, object]], payload["initiatives"])
    assert [item["id"] for item in initiatives] == ["D1", "D2", "D3"]
    assert initiatives[2]["depends_on"] == ["D1", "D2"]
