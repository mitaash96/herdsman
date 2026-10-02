"""Per-invocation harness-event confirmation; no global configuration writes."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

from .store import atomic_write_bytes

HOOK_KINDS = frozenset({"claude", "codex"})
_EVENTS = {"UserPromptSubmit": "start", "Stop": "stop"}
_MARKER_SCRIPT = '''import os
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

path = Path(sys.argv[1]) / sys.argv[2]
with NamedTemporaryFile(dir=path.parent, delete=False) as file:
    file.write(str(time.monotonic_ns()).encode())
os.replace(file.name, path)
'''


def lifecycle_args(kind: str, project_root: Path, marker_dir: Path) -> tuple[str, ...]:
    """Install launch-only start/stop hooks in a unique run's marker directory.

    herdr 0.9.3 does not give Claude/Codex reports lifecycle authority, even
    with its native source IDs. Callers must confirm herdr settle with
    ``harness_settled``. Codex session flags append handlers to other layers;
    only these exact handlers are trusted, without a blanket trust bypass.

    pi takes ``--approve`` instead: a fresh worktree of a project with ``.pi/``
    resources opens a trust dialog herdr reports as idle, not blocked. The flag
    trusts project-local files for this run only and writes no trust store.
    """
    if kind == "pi":
        return ("--approve",)
    if kind not in HOOK_KINDS:
        return ()
    directory = project_root.resolve() / ".herdsman" / "hooks"
    marker_dir = marker_dir.resolve()
    if not marker_dir.is_relative_to(directory.resolve()):
        raise ValueError("marker_dir must be under project_root/.herdsman/hooks")
    marker_dir.mkdir(parents=True, exist_ok=True)
    script = directory / "mark.py"
    atomic_write_bytes(script, _MARKER_SCRIPT.encode())
    hooks = {
        event: [{"hooks": [{"type": "command", "command": shlex.join(
            [sys.executable, str(script), str(marker_dir), marker]
        ), "timeout": 10}]}]
        for event, marker in _EVENTS.items()
    }
    if kind == "claude":
        path = marker_dir / "claude.json"
        atomic_write_bytes(path, json.dumps({"hooks": hooks}).encode())
        return ("--settings", str(path))
    args: list[str] = []
    trust: list[str] = []
    for event, groups in hooks.items():
        handler = groups[0]["hooks"][0]
        # Codex hashes normalized TOML as sorted compact JSON (absent options
        # omitted); keep this in sync with its HookHandlerConfig normalization.
        label = "user_prompt_submit" if event == "UserPromptSubmit" else "stop"
        identity = {"event_name": label, "hooks": [{**handler, "async": False}]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        command = json.dumps(handler["command"])
        args.extend(("-c", f'hooks.{event}=[{{hooks=[{{type="command",command={command},timeout=10}}]}}]'))
        trust.append(f'"/<session-flags>/config.toml:{label}:0:0"={{trusted_hash="sha256:{digest}"}}')
    args.extend(("-c", "hooks.state={" + ",".join(trust) + "}"))
    return tuple(args)


def harness_settled(marker_dir: Path, since_ns: int) -> bool:
    """Fail closed unless the latest Stop is newer than Start and this turn.

    Both markers must belong to this prompt: an interrupt's late Stop with
    an earlier Start cannot confirm a failed restart. ``since_ns`` comes from
    ``time.monotonic_ns()`` before every prompt. Read Stop first so a concurrent
    new Start cannot make the previous turn look settled.
    """
    try:
        stop = int((marker_dir / "stop").read_text())
        start = int((marker_dir / "start").read_text())
    except (OSError, ValueError):
        return False
    return stop > start > max(0, since_ns)
