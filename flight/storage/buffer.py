"""Durable, idempotent local store-and-forward queue for telemetry and flight events."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from shared.protocol import TelemetryRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TelemetryBuffer:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS telemetry (
                sample_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
                payload TEXT NOT NULL, acknowledged_at TEXT)""")
            self._db.execute("""CREATE INDEX IF NOT EXISTS pending_idx
                ON telemetry (acknowledged_at, received_at)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL,
                event_uid TEXT, acknowledged_at TEXT)""")
            # Upgrade existing SQLite databases without discarding earlier event history.
            columns = {row[1] for row in self._db.execute('PRAGMA table_info(events)')}
            if 'event_uid' not in columns:
                self._db.execute('ALTER TABLE events ADD COLUMN event_uid TEXT')
            if 'acknowledged_at' not in columns:
                self._db.execute('ALTER TABLE events ADD COLUMN acknowledged_at TEXT')
            for (event_id,) in self._db.execute(
                "SELECT event_id FROM events WHERE event_uid IS NULL OR event_uid=''"
            ).fetchall():
                self._db.execute('UPDATE events SET event_uid=? WHERE event_id=?',
                                 (uuid4().hex, event_id))
            self._db.execute('CREATE UNIQUE INDEX IF NOT EXISTS event_uid_idx ON events(event_uid)')
            self._db.execute("""CREATE INDEX IF NOT EXISTS pending_events_idx
                ON events (acknowledged_at, event_id)""")

    def store(self, record: TelemetryRecord) -> bool:
        payload = record.to_json()
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO telemetry (sample_id, received_at, payload) VALUES (?,?,?)",
                (record.sample_id, utc_now(), payload),
            )
            return cursor.rowcount == 1

    def pending(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM telemetry WHERE acknowledged_at IS NULL "
                "ORDER BY received_at, rowid LIMIT ?", (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, sample_ids: list[str]) -> None:
        if not sample_ids:
            return
        with self._lock, self._db:
            timestamp = utc_now()
            self._db.executemany(
                "UPDATE telemetry SET acknowledged_at=? "
                "WHERE sample_id=? AND acknowledged_at IS NULL",
                [(timestamp, sample_id) for sample_id in sample_ids],
            )

    def event(self, state: str, message: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (event_uid, event_time, state, message) VALUES (?,?,?,?)",
                (uuid4().hex, utc_now(), state, message),
            )

    def pending_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """SELECT event_uid, event_time, state, message
                   FROM events WHERE acknowledged_at IS NULL
                   ORDER BY event_id LIMIT ?""", (limit,),
            ).fetchall()
        return [dict(zip(('event_uid', 'event_time', 'state', 'message'), row))
                for row in rows]

    def acknowledge_events(self, event_uids: list[str]) -> None:
        if not event_uids:
            return
        with self._lock, self._db:
            timestamp = utc_now()
            self._db.executemany(
                "UPDATE events SET acknowledged_at=? "
                "WHERE event_uid=? AND acknowledged_at IS NULL",
                [(timestamp, uid) for uid in event_uids],
            )

    def event_counts(self) -> tuple[int, int]:
        with self._lock:
            total, pending = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(acknowledged_at IS NULL), 0) FROM events"
            ).fetchone()
        return int(total), int(pending)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            total, pending = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(acknowledged_at IS NULL), 0) FROM telemetry"
            ).fetchone()
        return int(total), int(pending)

    def close(self):
        with self._lock:
            self._db.close()
