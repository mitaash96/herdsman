import json
import time
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.request import Request

from pytest import MonkeyPatch
from typer.testing import CliRunner

from herdsman import cli
from herdsman.store import EventStore
from tests.test_classes import stream


def _seed(path: Path) -> None:
    store = EventStore(path)
    try:
        for event in stream():
            _ = store.append(event)
    finally:
        store.close()


def test_project_discovery_show_prefix_and_deep_link(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "project"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    _seed(root / ".herdsman" / "events.db")
    monkeypatch.chdir(nested)
    runner = CliRunner()

    shown = runner.invoke(cli.app, ["show", "plan_"])
    assert shown.exit_code == 0, shown.output
    payload = cast(dict[str, object], json.loads(shown.output))
    assert payload["kind"] == "plan"
    assert payload["plan_id"] == "plan_1"

    ambiguous = runner.invoke(cli.app, ["show", "init_"])
    assert ambiguous.exit_code != 0
    assert "ambiguous" in ambiguous.output

    linked = runner.invoke(cli.app, ["deep-link", "cp_"])
    assert linked.exit_code == 0
    assert json.loads(linked.output)["path"] == "/run?plan=plan_1&checkpoint=cp_1"


def test_create_reads_a_brief_from_stdin(monkeypatch: MonkeyPatch) -> None:
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        assert timeout == 130
        requests.append(request)
        return BytesIO(b'{"id":"plan_1"}')

    monkeypatch.setattr(cli, "urlopen", post)
    result = CliRunner().invoke(cli.app, ["create", "-"], input="from stdin\n")

    assert result.exit_code == 0, result.output
    assert json.loads(cast(bytes, requests[0].data)) == {"brief": "from stdin"}


def test_fleet_attention_digest_and_plan_archive_wrap_daemon_routes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def request(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        if request.full_url.endswith("/fleet"):
            return BytesIO(b'{"runs":[],"total_runs":0}')
        if request.full_url.endswith("/fleet/attention"):
            return BytesIO(
                b'[{"key":"plan:plan_1","summary":"approve plan","link":{"path":"/run?plan=plan_1"}}]'
            )
        if request.full_url.endswith("/while-away"):
            return BytesIO(b'[{"summary":"changed"}]')
        return BytesIO(b'{"archived":true}')

    monkeypatch.setattr(cli, "urlopen", request)
    runner = CliRunner()

    fleet = runner.invoke(cli.app, ["fleet"])
    assert fleet.exit_code == 0
    assert json.loads(fleet.output)["total_runs"] == 0

    attention = runner.invoke(cli.app, ["attention"])
    assert attention.exit_code == 3
    assert json.loads(attention.output)[0]["key"] == "plan:plan_1"

    digest = runner.invoke(
        cli.app, ["while-away", "--since", "2026-09-18T10:00:00+00:00"]
    )
    assert digest.exit_code == 0
    assert json.loads(cast(bytes, requests[-1].data)) == {
        "since": "2026-09-18T10:00:00+00:00"
    }

    archived = runner.invoke(cli.app, ["archive-plan", "plan_", "--reason", "done"])
    assert archived.exit_code == 0
    assert requests[-1].full_url.endswith("/plans/plan_1/archive")
    assert json.loads(cast(bytes, requests[-1].data))["reason"] == "done"


def test_checkpoint_recovery_and_packet_commands_use_prefixes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def request(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"ok":true}')

    monkeypatch.setattr(cli, "urlopen", request)
    runner = CliRunner()

    reviewed = runner.invoke(cli.app, ["checkpoint", "cp_", "approve"])
    assert reviewed.exit_code == 0, reviewed.output
    assert requests[-1].full_url.endswith("/plans/plan_1/checkpoints/cp_1/approve")

    recovery = runner.invoke(cli.app, ["recovery", "plan_"])
    assert recovery.exit_code == 0
    assert requests[-1].full_url.endswith("/plans/plan_1/recovery")

    packet = runner.invoke(cli.app, ["packet", "plan_", "att_"])
    assert packet.exit_code == 0
    assert requests[-1].full_url.endswith("/plans/plan_1/packets/att_1")


def test_config_show_get_set_validate_and_editor(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner = CliRunner()
    prefix = ["--project", str(tmp_path)]

    shown = runner.invoke(cli.app, [*prefix, "config", "show"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["version"] == 1

    set_result = runner.invoke(
        cli.app, [*prefix, "config", "set", "context_warning_tokens", "3000"]
    )
    assert set_result.exit_code == 0, set_result.output
    assert json.loads(set_result.output)["value"] == 3000

    got = runner.invoke(
        cli.app, [*prefix, "--format", "text", "config", "get", "context_warning_tokens"]
    )
    assert got.exit_code == 0
    assert got.output.strip() == "3000"

    editor = tmp_path / "edit.py"
    _ = editor.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json,sys",
                "p=sys.argv[1]; d=json.load(open(p)); d['context_warning_tokens']=4000; json.dump(d,open(p,'w'))",
                "",
            ]
        )
    )
    _ = editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    edited = runner.invoke(cli.app, [*prefix, "config", "edit"])
    assert edited.exit_code == 0, edited.output
    assert json.loads(edited.output)["changed"] is True

    valid = runner.invoke(cli.app, [*prefix, "config", "validate"])
    assert valid.exit_code == 0
    assert json.loads(valid.output)["valid"] is True
    assert json.loads((tmp_path / ".herdsman" / "kitchen.json").read_text())[
        "context_warning_tokens"
    ] == 4000


def test_wait_has_stable_settlement_attention_and_timeout_exit_codes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    response = {
        "plan_id": "plan_1",
        "graph": {
            "nodes": [
                {"initiative_id": "init_a", "state": "settled"},
                {"initiative_id": "init_c", "state": "settled"},
            ]
        },
    }

    def settled(request: Request, *, timeout: float) -> BytesIO:
        _ = request, timeout
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(cli, "urlopen", settled)
    runner = CliRunner()
    done = runner.invoke(
        cli.app, ["wait", "plan_", "--timeout", "0.1", "--interval", "0.001"]
    )
    assert done.exit_code == 0, done.output
    assert json.loads(done.output)["plan_id"] == "plan_1"

    def needs_attention(request: Request, *, timeout: float) -> BytesIO:
        _ = request, timeout
        return BytesIO(b'[{"key":"plan:plan_1"}]')

    monkeypatch.setattr(cli, "urlopen", needs_attention)
    attention = runner.invoke(cli.app, ["wait", "--attention", "--timeout", "0.1"])
    assert attention.exit_code == 3
    assert json.loads(attention.output)[0]["key"] == "plan:plan_1"


def test_retry_action_id_confirmation_and_null_response_are_machine_safe(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    requests: list[Request] = []

    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        requests.append(request)
        return BytesIO(b'{"checkpoint":null}')

    monkeypatch.setattr(cli, "urlopen", post)
    retry = CliRunner().invoke(
        cli.app,
        [
            "retry", "init_a", "--plan-id", "plan_1", "--yes",
            "--action-id", "retry-1",
        ],
    )

    assert retry.exit_code == 0, retry.output
    assert json.loads(cast(bytes, requests[0].data)) == {
        "timeout": 600.0,
        "by": "operator",
        "action_id": "retry-1",
    }
    assert json.loads(retry.stdout) is None

    run = CliRunner().invoke(cli.app, ["run", "init_a", "--plan-id", "plan_1"])
    assert run.exit_code == 0, run.output
    assert json.loads(run.stdout) is None


def test_disruptive_confirmation_is_written_to_stderr(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    def post(request: Request, *, timeout: float) -> BytesIO:
        _ = timeout
        if request.full_url.endswith("/cancel"):
            return BytesIO(b'{"ok":true}')
        return BytesIO(b'{"checkpoint":null}')

    monkeypatch.setattr(cli, "urlopen", post)

    result = CliRunner().invoke(
        cli.app, ["retry", "init_a", "--plan-id", "plan_1"], input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout.strip().splitlines()[-1]) is None
    assert "Downstream impact:" in result.stderr
    assert "Proceed?" in result.stderr

    cancelled = CliRunner().invoke(
        cli.app, ["cancel", "init_a", "--plan-id", "plan_1"], input="y\n"
    )
    assert cancelled.exit_code == 0, cancelled.output
    assert json.loads(cancelled.stdout.strip().splitlines()[-1]) == {"ok": True}
    assert "Downstream impact:" in cancelled.stderr
    assert "Proceed?" in cancelled.stderr


def test_config_set_reports_malformed_kitchen_as_bad_parameter(
    tmp_path: Path,
) -> None:
    kitchen = tmp_path / ".herdsman" / "kitchen.json"
    kitchen.parent.mkdir()
    _ = kitchen.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        ["--project", str(tmp_path), "config", "set", "context_warning_tokens", "1"],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.output


def test_wait_missing_daemon_node_fails_without_claiming_settlement(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    body = {
        "plan_id": "plan_1",
        "graph": {"nodes": [{"initiative_id": "init_c", "state": "settled"}]},
    }
    def get_status(request: Request, *, timeout: float) -> BytesIO:
        _ = request, timeout
        return BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(cli, "urlopen", get_status)

    result = CliRunner().invoke(
        cli.app, ["wait", "init_a", "--timeout", "0.1"]
    )

    assert result.exit_code == 2
    assert "not present in daemon status graph" in result.output
    assert "Traceback" not in result.output


def test_wait_failed_cancelled_and_timeout_exit_codes_are_deterministic(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "events.db"
    _seed(path)
    monkeypatch.setattr(cli, "EventStore", lambda: EventStore(path))
    runner = CliRunner()

    def status(state: str) -> None:
        body = {
            "plan_id": "plan_1",
            "graph": {"nodes": [{"initiative_id": "init_a", "state": state}]},
        }
        def get_status(request: Request, *, timeout: float) -> BytesIO:
            _ = request, timeout
            return BytesIO(json.dumps(body).encode())

        monkeypatch.setattr(cli, "urlopen", get_status)

    for state in ("failed", "cancelled"):
        status(state)
        result = runner.invoke(
            cli.app, ["wait", "init_a", "--timeout", "1"]
        )
        assert result.exit_code == 4, result.output

    status("running")
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    result = runner.invoke(
        cli.app,
        ["wait", "init_a", "--timeout", "1", "--interval", "1"],
    )
    assert result.exit_code == 5, result.output


def test_config_unknown_key_is_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["--project", str(tmp_path), "config", "get", "not_a_config_key"],
    )

    assert result.exit_code == 2
    assert "unknown config key" in result.output


def test_config_edit_refuses_stale_write_and_preserves_temp_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner = CliRunner()
    prefix = ["--project", str(tmp_path)]
    created = runner.invoke(
        cli.app, [*prefix, "config", "set", "context_warning_tokens", "3000"]
    )
    assert created.exit_code == 0, created.output
    canonical = tmp_path / ".herdsman" / "kitchen.json"

    def edit(path: Path) -> None:
        payload = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        payload["context_warning_tokens"] = 4000
        _ = path.write_text(json.dumps(payload), encoding="utf-8")
        external = cast(
            dict[str, object], json.loads(canonical.read_text(encoding="utf-8"))
        )
        external["context_warning_tokens"] = 3500
        _ = canonical.write_text(json.dumps(external), encoding="utf-8")

    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr(cli, "_open_editor", edit)
    result = runner.invoke(cli.app, [*prefix, "config", "edit"])

    assert result.exit_code == 2
    assert "edited file preserved at" in result.output
    preserved = Path(
        next(token for token in result.output.split() if token.startswith("/tmp/") and token.endswith(".json"))
    )
    assert preserved.is_file()
    assert json.loads(canonical.read_text(encoding="utf-8"))["context_warning_tokens"] == 3500
    preserved.unlink()


def test_ndjson_splits_top_level_lists_one_item_per_line(monkeypatch: MonkeyPatch) -> None:
    def attention(request: Request, *, timeout: float) -> BytesIO:
        _ = request, timeout
        return BytesIO(b'[{"id":2},{"id":1}]')

    monkeypatch.setattr(cli, "urlopen", attention)
    result = CliRunner().invoke(cli.app, ["--format", "ndjson", "attention"])

    assert result.exit_code == 3
    assert result.output.splitlines() == ['{"id":2}', '{"id":1}']


def test_output_modes_and_builtin_shell_completion(monkeypatch: MonkeyPatch) -> None:
    def fleet(request: Request, *, timeout: float) -> BytesIO:
        _ = request, timeout
        return BytesIO(b'{"runs":[],"total_runs":0}')

    monkeypatch.setattr(cli, "urlopen", fleet)
    runner = CliRunner()
    text = runner.invoke(cli.app, ["--format", "text", "fleet"])
    assert text.exit_code == 0
    assert "\n  \"runs\"" in text.output

    completion = runner.invoke(cli.app, ["--show-completion"])
    assert completion.exit_code == 0
    assert completion.output.strip()
