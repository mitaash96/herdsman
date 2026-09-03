import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.error import URLError
from urllib.request import Request

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

    requests: list[Request] = []

    def approve(request: Request, *, timeout: int) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"approval":"approved"}')

    monkeypatch.setattr(cli, "urlopen", approve)
    approved = CliRunner().invoke(cli.app, ["approve", "plan_1"])

    assert approved.exit_code == 0
    assert '"approval":"approved"' in approved.output
    assert requests[0].full_url == "http://127.0.0.1:8000/plans/plan_1/approve"


def test_create_command_uses_daemon_http_api(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    requests: list[tuple[Request, float]] = []

    def create(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(b'{"id":"plan_1","approval":"pending"}')

    monkeypatch.setattr(cli, "urlopen", create)
    result = CliRunner().invoke(
        cli.app,
        ["create", "make a change", "--host", "127.0.0.2", "--port", "8123"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"id": "plan_1", "approval": "pending"}
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.2:8123/plans"
    assert request.method == "POST"
    assert json.loads(cast(bytes, request.data)) == {"brief": "make a change"}
    assert request.get_header("Content-type") == "application/json"
    assert timeout == 130


def test_run_command_posts_to_daemon_and_prints_bare_checkpoint(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:3]:
            _ = store.append(event)
    finally:
        store.close()
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[tuple[Request, float]] = []
    response: dict[str, object] = {
        "checkpoint": {
            "id": "cp_1",
            "attempt_id": "att_1",
            "changed_paths": [],
            "base_sha": "base",
            "head_sha": "base",
            "checks": [],
            "caveats": [],
            "exit_code": 0,
            "patch_path": ".herdsman/artifacts/cp_1.patch",
            "usage": {"input_tokens": 1, "output_tokens": 2, "source": "harness"},
        }
    }

    def run(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(cli, "urlopen", run)
    result = CliRunner().invoke(
        cli.app,
        ["run", "init_a", "--plan-id", "plan_1", "--timeout", "600", "--port", "8123"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == response["checkpoint"]
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:8123/plans/plan_1/initiatives/init_a/run"
    assert json.loads(cast(bytes, request.data)) == {"timeout": 600.0}
    assert timeout == 610


def test_daemon_mutation_commands_report_how_to_start_unreachable_daemon(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def unreachable(request: Request, *, timeout: float) -> BytesIO:
        _ = (request, timeout)
        raise URLError("connection refused")

    monkeypatch.setattr(cli, "urlopen", unreachable)
    result = CliRunner().invoke(cli.app, ["create", "make a change"])

    assert result.exit_code != 0
    assert "cannot reach Herdsman daemon" in result.output
    assert "herdsman up" in result.output


def test_settle_command_posts_to_daemon_after_read_only_plan_lookup(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:3]:
            _ = store.append(event)
    finally:
        store.close()
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def settle(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"id":"plan_1","approval":"approved"}')

    monkeypatch.setattr(cli, "urlopen", settle)
    result = CliRunner().invoke(
        cli.app, ["settle", "init_a", "cp_1", "--plan-id", "plan_1"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"id": "plan_1", "approval": "approved"}
    assert requests[0].full_url == (
        "http://127.0.0.1:8000/plans/plan_1/initiatives/init_a/settle/cp_1"
    )
    assert requests[0].data is None


def test_discard_command_posts_to_daemon_after_read_only_plan_lookup(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:4]:
            _ = store.append(event)
    finally:
        store.close()
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def discard(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"id":"plan_1","approval":"approved"}')

    monkeypatch.setattr(cli, "urlopen", discard)
    result = CliRunner().invoke(
        cli.app, ["discard", "init_a", "att_1", "--plan-id", "plan_1"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"id": "plan_1", "approval": "approved"}
    assert requests[0].full_url == (
        "http://127.0.0.1:8000/plans/plan_1/initiatives/init_a/discard/att_1"
    )
    assert requests[0].data is None


def test_init_creates_an_idempotent_project_local_runtime(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(cli.app, ["init"])
    second = runner.invoke(cli.app, ["init"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (tmp_path / ".herdsman" / "events.db").is_file()
    assert "project-local" in first.output


def test_up_starts_the_existing_daemon_surface(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, int]] = []

    def fake_serve(host: str, port: int) -> None:
        calls.append((host, port))

    monkeypatch.setattr(cli, "serve", fake_serve)
    result = CliRunner().invoke(cli.app, ["up", "--host", "127.0.0.2", "--port", "8123"])

    assert result.exit_code == 0
    assert calls == [("127.0.0.2", 8123)]
    assert "Herdr session not started" in result.output
    assert "Browser UI not started" in result.output


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


def test_graph_and_risk_commands_read_the_event_stream(
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
    monkeypatch.chdir(tmp_path)

    graphed = CliRunner().invoke(cli.app, ["graph", "plan_1"])
    assert graphed.exit_code == 0
    assert '"ready":["init_a"]' in graphed.output
    assert '"critical_path":["init_a","init_c"]' in graphed.output

    risked = CliRunner().invoke(cli.app, ["risk", "plan_1"])
    assert risked.exit_code == 0
    assert '"conflicts":[]' in risked.output

    missing = CliRunner().invoke(cli.app, ["graph", "nope"])
    assert missing.exit_code != 0


def test_run_plan_command_posts_to_the_daemon(
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

    requests: list[tuple[Request, float]] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(b'{"plan_id":"plan_1"}')

    monkeypatch.setattr(cli, "urlopen", post)
    result = CliRunner().invoke(
        cli.app, ["run-plan", "plan_1", "--max-concurrent", "2", "--timeout", "600"]
    )

    assert result.exit_code == 0
    request, deadline = requests[0]
    assert request.full_url.endswith("/plans/plan_1/run")
    body = cast(bytes, request.data)
    assert json.loads(body)["max_concurrent"] == 2
    # Two initiatives could legitimately run back to back, so one initiative's
    # timeout must not bound the whole request.
    assert deadline == 600.0 * 2 + 10
