"""Project-local SQLite event store.

Events are the only thing on disk; `Plan.fold` is the reader. This module owns
durability and ordering and nothing else — no domain rule lives here, and no
foreign type crosses it: a payload is Pydantic JSON.

Single writer: the daemon is one process, and stream readers consume its
in-memory projection rather than polling this file.
"""

import fcntl
import os
import sqlite3
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from .classes import Event, Plan

DB_PATH = Path(".herdsman/events.db")
LOCK_PATH = Path(".herdsman/project.lock")

SCHEMA_VERSION = 1
"""Current on-disk schema. `migrate` walks a store forward to this number."""

_event: TypeAdapter[Event] = TypeAdapter(Event)

_DDL = """
CREATE TABLE IF NOT EXISTS events (
  seq     INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id TEXT NOT NULL,
  at      TEXT NOT NULL,
  type    TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_plan ON events(plan_id, seq);
"""


class LockBusy(RuntimeError):
    """Another process holds the project lock."""


def atomic_write(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` atomically and durably.

    The temporary file is created in the destination directory so `replace` is a
    same-filesystem rename, and both the file and its directory are fsynced:
    without the directory sync the rename itself can be lost to a crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding=encoding, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def project_lock(path: Path = LOCK_PATH, *, blocking: bool = False) -> Generator[None]:
    """Hold the single-writer project lock for the duration of the block.

    The event store assumes one writer. Two daemons on one `.herdsman` would
    interleave appends into a log whose ordering is its only contract, so the
    second one is refused here rather than corrupting state later.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, flags)
        except OSError as exc:
            raise LockBusy(f"another Herdsman process holds {path}") from exc
        try:
            _ = os.ftruncate(descriptor, 0)
            _ = os.write(descriptor, f"{os.getpid()}\n".encode())
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def migrate(db: sqlite3.Connection) -> list[int]:
    """Walk the store forward to `SCHEMA_VERSION`; return the versions applied.

    `user_version` is the marker because it costs no table and survives
    `VACUUM`. Version 0 is any store written before schema tracking existed;
    its shape already matches version 1, so the step is the stamp alone.
    """
    current = cast(int, db.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise ValueError(
            f"store schema {current} is newer than this Herdsman ({SCHEMA_VERSION}); upgrade herdsman"
        )
    applied: list[int] = []
    while current < SCHEMA_VERSION:
        current += 1
        _ = db.execute(f"PRAGMA user_version = {current}")
        applied.append(current)
    return applied


class EventStore:
    """Append-only log of `Event`, one row per event, one file per project."""

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db: sqlite3.Connection = sqlite3.connect(path, isolation_level=None)
        _ = self.db.execute("PRAGMA journal_mode=WAL")
        # A checkpoint lost to a crash means repeated work, which Gate 0 forbids.
        _ = self.db.execute("PRAGMA synchronous=FULL")
        _ = self.db.executescript(_DDL)
        _ = migrate(self.db)
        self._plans: dict[str, Plan] = {}

    def close(self) -> None:
        self.db.close()

    def append(self, ev: Event) -> Event:
        """Fold the event first; write it only if the projection accepts it.

        Returns the event with its store-assigned `seq`.
        """
        try:
            self._plans[ev.plan_id] = Plan.step(self._projection(ev.plan_id), ev)
        except ValueError:
            # The rejected event may have been applied in part; drop the cached
            # projection so the next read re-folds from what is actually on disk.
            _ = self._plans.pop(ev.plan_id, None)
            raise
        try:
            cursor = self.db.execute(
                "INSERT INTO events (plan_id, at, type, payload) VALUES (?, ?, ?, ?)",
                (ev.plan_id, ev.at.isoformat(), ev.type, ev.model_dump_json()),
            )
        except sqlite3.Error:
            _ = self._plans.pop(ev.plan_id, None)
            raise
        return ev.model_copy(update={"seq": cursor.lastrowid})

    def read(
        self,
        plan_id: str,
        *,
        through_seq: int | None = None,
        through_at: datetime | None = None,
    ) -> list[Event]:
        """Read an inclusive, seq-ordered event prefix.

        Timestamp filtering happens after decoding the domain datetime so
        offsets compare correctly; persisted sequence remains the ordering.
        """
        if through_seq is not None and through_seq < 0:
            raise ValueError("through_seq must be non-negative")
        if through_at is not None and through_at.tzinfo is None:
            raise ValueError("through_at must include a timezone")
        rows = cast(
            list[tuple[int, str]],
            self.db.execute(
                "SELECT seq, payload FROM events WHERE plan_id = ? ORDER BY seq",
                (plan_id,),
            ).fetchall(),
        )
        events: list[Event] = []
        for seq, payload in rows:
            if through_seq is not None and seq > through_seq:
                break
            event = _event.validate_json(payload).model_copy(update={"seq": seq})
            if through_at is not None and event.at > through_at:
                continue
            events.append(event)
        return events

    def plans(self) -> list[str]:
        """Every plan id on disk, oldest first — the daemon's restart entry point."""
        rows = cast(
            list[tuple[str]],
            self.db.execute(
                "SELECT plan_id FROM events GROUP BY plan_id ORDER BY MIN(seq)"
            ).fetchall(),
        )
        return [plan_id for (plan_id,) in rows]

    def load(self, plan_id: str) -> Plan:
        plan = self._projection(plan_id)
        if plan is None:
            raise ValueError(f"unknown plan {plan_id}")
        return plan

    def _projection(self, plan_id: str) -> Plan | None:
        """The folded plan, built from disk on first touch and kept thereafter."""
        if plan_id not in self._plans:
            events = self.read(plan_id)
            if events:
                self._plans[plan_id] = Plan.fold(events)
        return self._plans.get(plan_id)
