"""Per-invocation lifecycle reports; never modify harness configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import atomic_write_bytes

HOOK_KINDS = frozenset({"claude", "codex"})
_EVENTS = {"UserPromptSubmit": "working", "Stop": "idle", "PermissionRequest": "blocked"}


def lifecycle_args(kind: str, project_root: Path) -> tuple[str, ...]:
    """Add native hooks under source ``herdsman:<kind>`` for this launch only.

    Codex discovers hooks independently in each config layer, so session flags
    append handlers without replacing user hooks or legacy ``notify``. Trust
    only these exact handlers via session-local hashes, not a blanket bypass.
    """
    if kind not in HOOK_KINDS:
        return ()
    hooks = {
        event: [{"hooks": [{"type": "command", "command": (
            'if [ -n "${HERDR_PANE_ID:-}" ]; then '
            f'herdr pane report-agent "$HERDR_PANE_ID" --source herdsman:{kind} '
            f'--agent {kind} --state {state} >/dev/null; fi'
        ), "timeout": 10}]}]
        for event, state in _EVENTS.items()
    }
    if kind == "claude":
        path = project_root.resolve() / ".herdsman" / "hooks" / "claude.json"
        atomic_write_bytes(path, json.dumps({"hooks": hooks}).encode())
        return ("--settings", str(path))
    args: list[str] = []
    trust: list[str] = []
    for event, groups in hooks.items():
        handler = groups[0]["hooks"][0]
        # Codex hashes normalized TOML as sorted compact JSON (absent options
        # omitted); keep this in sync with its HookHandlerConfig normalization.
        identity = {"event_name": {"UserPromptSubmit": "user_prompt_submit",
                    "Stop": "stop", "PermissionRequest": "permission_request"}[event],
                    "hooks": [{**handler, "async": False}]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        command = json.dumps(handler["command"])
        args.extend(("-c", f'hooks.{event}=[{{hooks=[{{type="command",command={command},timeout=10}}]}}]'))
        trust.append(f'"/<session-flags>/config.toml:{identity["event_name"]}:0:0"={{trusted_hash="sha256:{digest}"}}')
    args.extend(("-c", "hooks.state={" + ",".join(trust) + "}"))
    return tuple(args)
