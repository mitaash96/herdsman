"""Shared fixtures for the tests that run against a real herdr daemon."""

import json
import os
import subprocess
from collections.abc import Iterator
from typing import cast

import pytest


def _workspace_ids() -> set[str]:
    """Every workspace herdr currently holds, or nothing if herdr is absent."""
    try:
        result = subprocess.run(
            ["herdr", "workspace", "list"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    try:
        # {"id", "result": {"type": "workspace_list", "workspaces": [...]}}
        payload = cast(object, json.loads(result.stdout))
        if not isinstance(payload, dict):
            return set()
        outcome = cast(dict[str, object], payload).get("result")
        if not isinstance(outcome, dict):
            return set()
        workspaces = cast(dict[str, object], outcome).get("workspaces")
    except ValueError:
        return set()
    if not isinstance(workspaces, list):
        return set()
    found: set[str] = set()
    for entry in cast(list[object], workspaces):
        if isinstance(entry, dict):
            workspace_id = cast(dict[str, object], entry).get("workspace_id")
            if isinstance(workspace_id, str):
                found.add(workspace_id)
    return found


@pytest.fixture
def herdr_workspaces() -> Iterator[None]:
    """Close whatever herdr workspaces a live test leaves behind.

    `worktree.create` opens a workspace for the project root as well as one
    for the linked worktree, and `discard` releases only the latter -- so each
    live run would otherwise add a permanent entry to the developer's own
    herdr session. Herdsman is strictly additive about global harness state,
    and that has to include the state its tests create.
    """
    before = _workspace_ids()
    cleanup = os.environ.get("HERDSMAN_TEST_REAL_HERDR") == "1"
    try:
        yield
    finally:
        if cleanup:
            for workspace_id in _workspace_ids() - before:
                _ = subprocess.run(
                    ["herdr", "workspace", "close", workspace_id],
                    capture_output=True,
                    timeout=10,
                )
