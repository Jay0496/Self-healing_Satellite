"""Durable, idempotent local store-and-forward queue for telemetry records."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

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
                event_time TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)""")

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
            self._db.executemany(
                "UPDATE telemetry SET acknowledged_at=? "
                "WHERE sample_id=? AND acknowledged_at IS NULL",
                [(utc_now(), sample_id) for sample_id in sample_ids],
            )

    def event(self, state: str, message: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (event_time, state, message) VALUES (?,?,?)",
                (utc_now(), state, message),
            )

    def counts(self) -> tuple[int, int]:
        with self._lock:
            total, pending = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(acknowledged_at IS NULL), 0) FROM telemetry"
            ).fetchone()
        return int(total), int(pending)

    def close(self):
        with self._lock:
            self._db.close()
