"""Daemon lifecycle: one record on disk, one live process per project.

`up`, `down`, `open`, and `restart` are the first four commands a stranger
types, so they must be idempotent: running one twice is not an error, and a
record left behind by a crash must not look like a running daemon.
"""

import json
import os
import socket
from pathlib import Path
from typing import NamedTuple, cast

from .kitchen import KITCHEN_DIR
from .store import atomic_write

RECORD_PATH = Path(KITCHEN_DIR) / "daemon.json"


class DaemonRecord(NamedTuple):
    """What a running daemon publishes about itself."""

    pid: int
    host: str
    port: int
    started_at: str

    def alive(self) -> bool:
        """Whether the recorded process still exists.

        Existence, not identity: a recycled PID is possible and the port probe
        in `read_record` is what settles the ambiguity.
        """
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def write_record(host: str, port: int, started_at: str, path: Path = RECORD_PATH) -> DaemonRecord:
    record = DaemonRecord(pid=os.getpid(), host=host, port=port, started_at=started_at)
    atomic_write(path, json.dumps(record._asdict(), sort_keys=True) + "\n")
    return record


def read_record(path: Path = RECORD_PATH) -> DaemonRecord | None:
    """The recorded daemon, or None when there is no usable record.

    A record whose process is gone is stale, not running; it is removed here so
    the next `up` is a clean start rather than a false "already running".
    """
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    fields = cast(dict[str, object], raw)
    try:
        record = DaemonRecord(
            pid=int(cast(int, fields["pid"])),
            host=str(fields["host"]),
            port=int(cast(int, fields["port"])),
            started_at=str(fields["started_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not record.alive():
        path.unlink(missing_ok=True)
        return None
    return record


def clear_record(path: Path = RECORD_PATH) -> None:
    path.unlink(missing_ok=True)


def port_free(host: str, port: int) -> bool:
    """Whether the daemon could bind this address right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def ui_bundle() -> Path | None:
    """The built Svelte bundle, from the installed wheel or a source checkout.

    Returns None when no bundle is present; `up` then serves the API alone
    rather than failing, because the API is the product's floor.
    """
    for candidate in (Path(__file__).parent / "ui", Path.cwd() / "ui" / "build"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


__all__ = [
    "RECORD_PATH",
    "DaemonRecord",
    "clear_record",
    "port_free",
    "read_record",
    "ui_bundle",
    "write_record",
]
