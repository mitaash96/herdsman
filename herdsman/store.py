"""Project-local SQLite event store.

Events are the only thing on disk; `Plan.fold` is the reader. This module owns
durability and ordering and nothing else — no domain rule lives here, and no
foreign type crosses it: a payload is Pydantic JSON.

Single writer: the daemon is one process, and stream readers consume its
in-memory projection rather than polling this file.
"""

import sqlite3
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from .classes import Event, Plan

DB_PATH = Path(".herdsman/events.db")

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


class EventStore:
    """Append-only log of `Event`, one row per event, one file per project."""

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db: sqlite3.Connection = sqlite3.connect(path, isolation_level=None)
        _ = self.db.execute("PRAGMA journal_mode=WAL")
        # A checkpoint lost to a crash means repeated work, which Gate 0 forbids.
        _ = self.db.execute("PRAGMA synchronous=FULL")
        _ = self.db.executescript(_DDL)
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
        cursor = self.db.execute(
            "INSERT INTO events (plan_id, at, type, payload) VALUES (?, ?, ?, ?)",
            (ev.plan_id, ev.at.isoformat(), ev.type, ev.model_dump_json()),
        )
        return ev.model_copy(update={"seq": cursor.lastrowid})

    def read(self, plan_id: str) -> list[Event]:
        """This plan's events, in append order, with `seq` filled from the column."""
        rows = cast(
            list[tuple[int, str]],
            self.db.execute(
                "SELECT seq, payload FROM events WHERE plan_id = ? ORDER BY seq",
                (plan_id,),
            ).fetchall(),
        )
        # The column is the source of truth for ordering: the payload was written
        # before the rowid existed and carries seq=0.
        return [
            _event.validate_json(payload).model_copy(update={"seq": seq})
            for seq, payload in rows
        ]

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
