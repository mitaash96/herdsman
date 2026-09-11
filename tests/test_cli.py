import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.error import URLError
from urllib.request import Request

from pytest import MonkeyPatch
from typer.testing import CliRunner

from herdsman import cli
from herdsman.classes import (
    InitiativeFailed,
    PlanCreated,
    PlanProposed,
    PolicyDecisionRecorded,
)
from herdsman.store import EventStore
from tests.test_classes import AT, stream


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


def test_unattended_run_preserves_initiative_and_plan_targets(
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

    def run(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(b'{"checkpoint":null}')

    monkeypatch.setattr(cli, "urlopen", run)
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "init_a",
            "--plan-id",
            "plan_1",
            "--unattended",
            "--timeout",
            "600",
            "--port",
            "8123",
        ],
    )

    assert result.exit_code == 0
    request, timeout = requests[0]
    assert request.full_url == (
        "http://127.0.0.1:8123/plans/plan_1/initiatives/init_a/run"
    )
    assert json.loads(cast(bytes, request.data)) == {
        "timeout": 600.0,
        "unattended": True,
    }
    assert timeout == 610
def test_agent_memory_passes_attempt_identity_to_global_pull(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    requests: list[Request] = []

    def pull(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"leaf":"one"}')

    monkeypatch.setattr(cli, "urlopen", pull)
    result = CliRunner().invoke(
        cli.app,
        ["agent", "memory", "--query", "subject", "--attempt-id", "attempt_1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"leaf": "one"}
    assert requests[0].full_url.endswith(
        "/memory?query=subject&attempt_id=attempt_1"
    )


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


def test_replay_and_digest_commands_use_event_fold_and_rule_attribution(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream():
            _ = store.append(event)
        _ = store.append(
            PolicyDecisionRecorded(
                plan_id="plan_1",
                at=datetime(2026, 8, 25, 12, tzinfo=UTC) + timedelta(minutes=1),
                initiative_id="init_a",
                attempt_id="att_1",
                checkpoint_id="cp_1",
                outcome="stopped",
                rule_ids=["stop_loss.budget"],
                reason="budget refused",
            )
        )
    finally:
        store.close()

    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    runner = CliRunner()
    replayed = runner.invoke(
        cli.app,
        [
            "replay",
            "plan_1",
            "--at",
            "2026-08-25T12:00:00+00:00",
        ],
    )
    assert replayed.exit_code == 0
    assert json.loads(replayed.output)["policy_decisions"] == []

    digested = runner.invoke(cli.app, ["digest", "plan_1"])
    assert digested.exit_code == 0
    digest = cast(dict[str, object], json.loads(digested.output))
    decisions = cast(list[dict[str, object]], digest["decisions"])
    assert decisions[0]["rule_ids"] == ["stop_loss.budget"]
    assert decisions[0]["outcome"] == "stopped"


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


def test_recalibrate_posts_the_revision_and_revision_folds_locally(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    requests: list[tuple[Request, float]] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(b'{"plan_id":"plan_1","from_version":1,"to_version":2}')

    monkeypatch.setattr(cli, "urlopen", post)
    posted = CliRunner().invoke(
        cli.app,
        [
            "recalibrate", "plan_1",
            "--reason", "too narrow",
            "--timeout", "30",
            "--action-id", "recal-1",
            "--port", "8123",
        ],
    )
    assert posted.exit_code == 0
    assert json.loads(posted.output)["to_version"] == 2
    request, deadline = requests[0]
    assert request.full_url == "http://127.0.0.1:8123/plans/plan_1/recalibrate"
    assert request.method == "POST"
    assert json.loads(cast(bytes, request.data)) == {
        "reason": "too narrow",
        "timeout": 30.0,
        "action_id": "recal-1",
    }
    assert deadline == 40

    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        events = stream()[:2]
        for event in events:
            _ = store.append(event)
        first = events[1]
        assert isinstance(first, PlanProposed)
        edited = first.initiatives[1].model_copy(update={"brief": "cover it harder"})
        _ = store.append(
            PlanProposed(
                plan_id="plan_1",
                at=AT + timedelta(minutes=1),
                version=2,
                initiatives=[first.initiatives[0], edited],
            )
        )
    finally:
        store.close()

    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    monkeypatch.chdir(tmp_path)
    projected = CliRunner().invoke(cli.app, ["revision", "plan_1"])
    assert projected.exit_code == 0
    report = json.loads(projected.output)
    assert (report["from_version"], report["to_version"]) == (1, 2)
    assert report["revision"]["counts"]["edited"] == 1
    assert report["revision"]["counts"]["unchanged"] == 1

    missing = CliRunner().invoke(cli.app, ["revision", "nope"])
    assert missing.exit_code != 0
    assert "plan has no revision" in missing.output
    assert "Traceback" not in missing.output


def test_risk_command_reports_invalid_model_tiers_without_traceback(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:2]:
            _ = store.append(event)
    finally:
        store.close()

    tiers = tmp_path / ".herdsman" / "models.json"
    tiers.parent.mkdir()
    _ = tiers.write_text("{")
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["risk", "plan_1"])

    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "invalid JSON in model tiers" in result.output
    assert "Traceback" not in result.output


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


def _seed(path: Path, count: int) -> None:
    store = EventStore(path)
    try:
        for event in stream()[:count]:
            _ = store.append(event)
    finally:
        store.close()


def test_retry_command_confirms_shown_impact_before_posting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Disruptive CLI commands show impact and require --yes or a confirm."""
    path = tmp_path / "events.db"
    _seed(path, 2)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"checkpoint": null}')

    monkeypatch.setattr(cli, "urlopen", post)
    runner = CliRunner()

    # Without --yes and without a terminal confirmation, nothing is posted.
    aborted = runner.invoke(
        cli.app, ["retry", "init_a", "--plan-id", "plan_1"], input=""
    )
    assert aborted.exit_code != 0
    assert requests == []
    assert "Downstream impact:" in aborted.output

    confirmed = runner.invoke(
        cli.app, ["retry", "init_a", "--plan-id", "plan_1"], input="y\n"
    )
    assert confirmed.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/retry")
    assert "Downstream impact:" in confirmed.output

    # --yes skips the prompt for scripted use.
    assumed = runner.invoke(
        cli.app, ["retry", "init_a", "--yes", "--plan-id", "plan_1"]
    )
    assert assumed.exit_code == 0
    assert len(requests) == 2


def test_redirect_command_can_target_a_checkpoint(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A redirect may point the task at an existing checkpoint version."""
    path = tmp_path / "events.db"
    _seed(path, 2)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"id":"plan_1"}')

    monkeypatch.setattr(cli, "urlopen", post)
    result = CliRunner().invoke(
        cli.app,
        [
            "redirect", "init_a", "--checkpoint-id", "cp_1",
            "--yes", "--plan-id", "plan_1",
        ],
    )
    assert result.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/redirect")
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "brief": "",
        "checkpoint_id": "cp_1",
        "by": "operator",
        "reason": "",
    }


def test_retry_command_posts_to_the_retry_route(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path, 3)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[tuple[Request, float]] = []
    response: dict[str, object] = {"checkpoint": None}

    def post(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(cli, "urlopen", post)
    result = CliRunner().invoke(
        cli.app,
        [
            "retry", "init_a", "--yes", "--plan-id", "plan_1",
            "--timeout", "300", "--by", "lead",
        ],
    )

    assert result.exit_code == 0
    request, timeout = requests[0]
    assert (
        request.full_url
        == "http://127.0.0.1:8000/plans/plan_1/initiatives/init_a/retry"
    )
    assert json.loads(cast(bytes, request.data)) == {"timeout": 300.0, "by": "lead"}
    assert timeout == 310

    result = CliRunner().invoke(
        cli.app, ["retry", "init_a", "--yes", "--plan-id", "plan_1"]
    )
    assert result.exit_code == 0
    assert json.loads(cast(bytes, requests[-1][0].data)) == {
        "timeout": 600.0,
        "by": "operator",
    }


def test_redirect_reassign_and_nudge_commands_post_interventions(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path, 2)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"id":"plan_1"}')

    monkeypatch.setattr(cli, "urlopen", post)
    runner = CliRunner()

    redirected = runner.invoke(
        cli.app,
        [
            "redirect", "init_a", "--brief", "v2 brief", "--reason", "scope",
            "--yes", "--plan-id", "plan_1",
        ],
    )
    assert redirected.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/redirect")
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "brief": "v2 brief",
        "checkpoint_id": None,
        "by": "operator",
        "reason": "scope",
    }

    reassigned = runner.invoke(
        cli.app,
        ["reassign", "init_a", "luna", "big-1", "--yes", "--plan-id", "plan_1"],
    )
    assert reassigned.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/reassign")
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "harness": "luna",
        "model": "big-1",
        "by": "operator",
        "reason": "",
    }

    nudged = runner.invoke(
        cli.app,
        ["nudge", "init_a", "focus", "--ground-truth", "--plan-id", "plan_1"],
    )
    assert nudged.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/nudge")
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "text": "focus",
        "by": "operator",
        "ground_truth": True,
    }


def test_restart_and_focus_commands_post_their_bodies(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path, 2)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"pane_ref":"p_9f"}')

    monkeypatch.setattr(cli, "urlopen", post)
    runner = CliRunner()

    restarted = runner.invoke(cli.app, ["restart", "init_a", "--plan-id", "plan_1"])
    assert restarted.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/restart")
    assert json.loads(cast(bytes, requests[-1].data)) == {"by": "operator"}
    assert json.loads(restarted.output) == {"pane_ref": "p_9f"}

    focused = runner.invoke(cli.app, ["focus", "init_a", "--plan-id", "plan_1"])
    assert focused.exit_code == 0
    assert requests[-1].full_url.endswith("/initiatives/init_a/focus")
    assert requests[-1].data is None


def test_answer_and_auto_answer_commands_resolve_the_attempt_plan(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path, len(stream()))
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"id":"plan_1"}')

    monkeypatch.setattr(cli, "urlopen", post)
    runner = CliRunner()

    answered = runner.invoke(cli.app, ["answer", "att_1", "tabs", "spaces"])
    assert answered.exit_code == 0
    assert requests[-1].full_url == (
        "http://127.0.0.1:8000/plans/plan_1/attempts/att_1/answer"
    )
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "subject": "tabs",
        "answer": "spaces",
        "by": "operator",
    }

    auto = runner.invoke(cli.app, ["auto-answer", "att_1", "tabs"])
    assert auto.exit_code == 0
    assert requests[-1].full_url == (
        "http://127.0.0.1:8000/plans/plan_1/attempts/att_1/auto-answer"
    )
    assert json.loads(cast(bytes, requests[-1].data)) == {"subject": "tabs"}

    missing = runner.invoke(cli.app, ["answer", "att_missing", "tabs", "spaces"])
    assert missing.exit_code != 0
    assert "unknown attempt" in missing.output


def test_impact_command_previews_downstream_from_the_event_stream(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path, 2)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))

    result = CliRunner().invoke(cli.app, ["impact", "init_a"])

    assert result.exit_code == 0
    assert json.loads(result.output)["descendants"][0]["initiative_id"] == "init_c"

    missing = CliRunner().invoke(cli.app, ["impact", "nope"])
    assert missing.exit_code != 0


def test_resume_command_posts_state_recovery_to_daemon(
    monkeypatch: MonkeyPatch,
) -> None:
    requests: list[tuple[Request, float]] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(b'{"plan_id":"plan_1","stale":[],"outcomes":{}}')

    monkeypatch.setattr(cli, "urlopen", post)
    result = CliRunner().invoke(
        cli.app, ["resume", "plan_1", "--port", "8123", "--assume-missing"]
    )

    assert result.exit_code == 0
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:8123/plans/plan_1/resume"
    assert json.loads(cast(bytes, request.data)) == {
        "assume_missing": True,
        "timeout": 600.0,
    }
    assert timeout == 610.0


def test_salvage_renders_preserved_failure_evidence(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for event in stream()[:-1] + [
            InitiativeFailed(
                plan_id="plan_1",
                at=datetime.now(UTC),
                initiative_id="init_a",
                reason="daemon death: pane p_1 missing",
                evidence=[".herdsman/artifacts/att_1.diag.patch"],
            )
        ]:
            _ = store.append(event)
    finally:
        store.close()
    preserved = tmp_path / ".herdsman" / "artifacts" / "att_1.diag.patch"
    preserved.parent.mkdir(parents=True, exist_ok=True)
    _ = preserved.write_text("diff --git a b")

    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    monkeypatch.chdir(tmp_path)
    rendered = CliRunner().invoke(cli.app, ["salvage", "plan_1"])

    assert rendered.exit_code == 0
    assert "init_a  FAILED" in rendered.output
    assert "daemon death: pane p_1 missing" in rendered.output
    assert f"att_1.diag.patch ({preserved.stat().st_size} bytes)" in rendered.output
    assert "attempt att_1" in rendered.output
