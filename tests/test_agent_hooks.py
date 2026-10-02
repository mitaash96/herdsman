"""Native flags and safe shell reports, without launching a harness."""

import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import TypedDict, cast

import pytest

from herdsman.agent_hooks import lifecycle_args


class Handler(TypedDict):
    type: str
    command: str
    timeout: int


class Group(TypedDict):
    hooks: list[Handler]


class Settings(TypedDict):
    hooks: dict[str, list[Group]]


def hook_settings(kind: str, args: tuple[str, ...]) -> Settings:
    data = json.loads(Path(args[1]).read_text()) if kind == "claude" else tomllib.loads("\n".join(args[1::2]))
    return cast(Settings, data)


@pytest.mark.parametrize("kind", ["pi", "opencode", "other", "../claude"])
def test_other_kinds_do_nothing(kind: str, tmp_path: Path) -> None:
    assert lifecycle_args(kind, tmp_path) == ()
    assert not list(tmp_path.iterdir())


def test_claude_settings_are_project_local_and_repeatable(tmp_path: Path) -> None:
    args = lifecycle_args("claude", tmp_path)
    assert args == lifecycle_args("claude", tmp_path)
    assert args == ("--settings", str(tmp_path / ".herdsman/hooks/claude.json"))
    data = hook_settings("claude", args)
    assert set(data) == {"hooks"}
    assert set(data["hooks"]) == {"UserPromptSubmit", "Stop", "PermissionRequest"}


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_hooks_report_each_state_and_noop_without_pane(kind: str, tmp_path: Path) -> None:
    args = lifecycle_args(kind, tmp_path)
    hooks = hook_settings(kind, args)["hooks"]
    fake = tmp_path / "herdr"
    _ = fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$REPORT"\n')
    fake.chmod(0o755)
    report = tmp_path / "report"
    env = {**os.environ, "PATH": str(tmp_path), "REPORT": str(report)}
    _ = env.pop("HERDR_PANE_ID", None)
    for event, state in {"UserPromptSubmit": "working", "Stop": "idle", "PermissionRequest": "blocked"}.items():
        command = hooks[event][0]["hooks"][0]["command"]
        _ = subprocess.run(["/bin/sh", "-c", command], env=env, check=True)
        assert not report.exists()
        _ = subprocess.run(["/bin/sh", "-c", command], env={**env, "HERDR_PANE_ID": "w-test:p1"}, check=True)
        assert report.read_text().splitlines() == ["pane", "report-agent", "w-test:p1", "--source", f"herdsman:{kind}", "--agent", kind, "--state", state]
        report.unlink()


def test_codex_trusts_only_its_session_handlers_and_preserves_notify(tmp_path: Path) -> None:
    args = lifecycle_args("codex", tmp_path)
    assert all(arg == "-c" for arg in args[::2])
    assert not any("notify=" in arg or "bypass" in arg for arg in args)
    assert not list(tmp_path.iterdir())
    hooks = hook_settings("codex", args)["hooks"]
    config = tomllib.loads("\n".join(args[1::2]))
    state = cast(dict[str, dict[str, str]], config["hooks"]["state"])
    for event, label in {"UserPromptSubmit": "user_prompt_submit", "Stop": "stop", "PermissionRequest": "permission_request"}.items():
        handler = hooks[event][0]["hooks"][0]
        identity = {"event_name": label, "hooks": [{**handler, "async": False}]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert state[f"/<session-flags>/config.toml:{label}:0:0"] == {"trusted_hash": f"sha256:{digest}"}
    # Installed Codex 0.160.0 hooks/list fixture: normalized identity hash.
    assert state["/<session-flags>/config.toml:stop:0:0"]["trusted_hash"] == "sha256:390d714b34cde2631a39e7d7264a24f9e9f760d78874b546ddf835eaf02845f2"
