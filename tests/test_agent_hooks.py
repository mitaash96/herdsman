"""Native flags and atomic harness-event markers, without launching a harness."""

import hashlib
import json
import os
import subprocess
import time
import tomllib
from pathlib import Path
from typing import TypedDict, cast

import pytest

from herdsman.agent_hooks import harness_settled, lifecycle_args


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


def markers(root: Path) -> Path:
    return root / ".herdsman/hooks/run ' quoted"


@pytest.mark.parametrize("kind", ["pi", "opencode", "other", "../claude"])
def test_other_kinds_do_nothing(kind: str, tmp_path: Path) -> None:
    assert lifecycle_args(kind, tmp_path, markers(tmp_path)) == ()
    assert not list(tmp_path.iterdir())


def test_claude_settings_are_run_local_and_repeatable(tmp_path: Path) -> None:
    directory = markers(tmp_path)
    args = lifecycle_args("claude", tmp_path, directory)
    assert args == lifecycle_args("claude", tmp_path, directory)
    assert args == ("--settings", str(directory / "claude.json"))
    data = hook_settings("claude", args)
    assert set(data) == {"hooks"}
    assert set(data["hooks"]) == {"UserPromptSubmit", "Stop"}
    assert not any(directory.glob("start"))
    assert not any(directory.glob("stop"))


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_hooks_confirm_only_completed_turns_and_ignore_old_stop(kind: str, tmp_path: Path) -> None:
    directory = markers(tmp_path)
    args = lifecycle_args(kind, tmp_path, directory)
    hooks = hook_settings(kind, args)["hooks"]
    env = dict(os.environ)
    _ = env.pop("HERDR_PANE_ID", None)
    for _ in range(2):
        since = time.monotonic_ns()
        assert not harness_settled(directory, since)
        start = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        stop = hooks["Stop"][0]["hooks"][0]["command"]
        _ = subprocess.run(["/bin/sh", "-c", start], env=env, check=True)
        assert not harness_settled(directory, since)
        assert int((directory / "start").read_text()) > since
        _ = subprocess.run(["/bin/sh", "-c", stop], env=env, check=True)
        assert harness_settled(directory, since)
        assert int((directory / "stop").read_text()) > int((directory / "start").read_text())
    assert set(path.name for path in directory.iterdir()) <= {"start", "stop", "claude.json"}


@pytest.mark.parametrize("start,stop,since,settled", [
    (None, None, 0, False), ("10", None, 0, False),
    (None, "20", 0, False), ("bad", "20", 0, False),
    ("10", "bad", 0, False), ("0", "20", 0, False),
    ("20", "10", 0, False), ("20", "20", 0, False),
    ("10", "20", 20, False), ("10", "20", 30, False),
    ("10", "20", 15, False),  # late interrupt Stop, not a new prompt's Start
    ("15", "20", 15, False), ("16", "20", 15, True),
])
def test_settle_is_strict_and_fail_closed(
    tmp_path: Path, start: str | None, stop: str | None, since: int, settled: bool,
) -> None:
    for name, value in [("start", start), ("stop", stop)]:
        if value is not None:
            _ = (tmp_path / name).write_text(value)
    assert harness_settled(tmp_path, since) is settled


def test_markers_must_stay_project_local(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="marker_dir must be under"):
        _ = lifecycle_args("claude", tmp_path, tmp_path / "outside")
    assert not list(tmp_path.iterdir())


def test_codex_trusts_only_its_session_handlers_and_preserves_notify(tmp_path: Path) -> None:
    args = lifecycle_args("codex", tmp_path, markers(tmp_path))
    assert all(arg == "-c" for arg in args[::2])
    assert not any("notify=" in arg or "bypass" in arg for arg in args)
    hooks = hook_settings("codex", args)["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "Stop", "state"}
    config = tomllib.loads("\n".join(args[1::2]))
    state = cast(dict[str, dict[str, str]], config["hooks"]["state"])
    for event, label in {"UserPromptSubmit": "user_prompt_submit", "Stop": "stop"}.items():
        handler = hooks[event][0]["hooks"][0]
        identity = {"event_name": label, "hooks": [{**handler, "async": False}]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert state[f"/<session-flags>/config.toml:{label}:0:0"] == {"trusted_hash": f"sha256:{digest}"}
